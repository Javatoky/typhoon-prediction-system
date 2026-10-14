"""数据集导入服务（FR-013、UC-07）。

支持两种来源：
1. IBTrACS v4 原始 CSV（列名如 SID / ISO_TIME / LAT / LON / USA_WIND / USA_PRES，风速单位为 knot）。
2. 本项目标准 CSV（sid / iso_time / lat / lon / max_wind_mps / pressure_hpa）。

事务策略（UC-07 异常流程）：
- 默认“非严格”模式：跳过无法解析的行并统计，其余数据入库；
- strict=True：任一行不合法则整批回滚，不留下半导入状态。
"""

from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.core.errors import ValidationError
from app.core.security import generate_token, normalize_stored_filename, utcnow
from app.extensions import db
from app.ml import schema
from app.models import DatasetImport, TrackPoint, Typhoon, User
from app.services import audit_service
from app.utils.intensity import classify_intensity
from app.utils.timeutil import parse_datetime
from app.utils.units import KNOT_TO_MPS, in_focus_area, looks_like_0_360, normalize_lat, normalize_lon

logger = logging.getLogger(__name__)

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030")


@dataclass
class ImportStats:
    row_count: int = 0
    typhoon_count: int = 0
    point_count: int = 0
    skipped_row_count: int = 0
    errors: list[str] = field(default_factory=list)
    lon_converted: bool = False
    wind_unit: str = "mps"
    # 口径统计（docs/data-card.md D-02/D-03/D-04）
    pressure_fallback_count: int = 0  # 由其他机构气压列填补的点数
    wind_converted_count: int = 0  # knot → m/s 换算的点数
    skipped_by_year: int = 0  # 因年份下限被剔除的台风数

    def to_dict(self) -> dict:
        return {
            "row_count": self.row_count,
            "typhoon_count": self.typhoon_count,
            "point_count": self.point_count,
            "skipped_row_count": self.skipped_row_count,
            "lon_converted": self.lon_converted,
            "wind_unit": self.wind_unit,
            "pressure_fallback_count": self.pressure_fallback_count,
            "pressure_fallback_pct": (
                round(self.pressure_fallback_count / self.point_count * 100, 2)
                if self.point_count
                else 0.0
            ),
            "wind_converted_count": self.wind_converted_count,
            "skipped_by_year": self.skipped_by_year,
            "errors": self.errors[:50],
        }


def _decode(raw: bytes) -> str:
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValidationError("文件编码无法识别，请转换为 UTF-8 后重新导入。")


def _to_float(value):
    """把任意来源的值安全转为 float，失败或非有限值返回 None。"""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if text == "" or text.lower() in {"nan", "none", "null", "na", "-"}:
            return None
        value = text
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _detect_wind_unit(header: list[str]) -> str:
    """IBTrACS 原始 USA_WIND / WMO_WIND 为 knot；本项目标准列 max_wind_mps 为 m/s。"""
    lowered = {name.strip().lower() for name in header if name}
    if "max_wind_mps" in lowered or "wind_mps" in lowered:
        return "mps"
    return "kt"


def parse_dataset_csv(raw: bytes) -> tuple[list[str], list[dict], str]:
    text = _decode(raw)
    reader = csv.DictReader(io.StringIO(text, newline=""))
    header = [name.strip() if name else "" for name in (reader.fieldnames or [])]
    if not header:
        raise ValidationError("文件没有表头行，无法识别字段。")

    mapping: dict[str, str] = {}
    for name in header:
        canonical = schema.canonical_column(name)
        if canonical and canonical not in mapping.values():
            mapping[name] = canonical

    missing = [name for name in ("sid", "iso_time", "lat", "lon") if name not in mapping.values()]
    if missing:
        raise ValidationError(
            f"缺少必需列：{'、'.join(missing)}。IBTrACS 原始 CSV 应至少包含 SID、ISO_TIME、LAT、LON。"
        )

    rows: list[dict] = []
    for raw_row in reader:
        normalized: dict = {}
        for key, value in raw_row.items():
            canonical = mapping.get((key or "").strip())
            if canonical:
                normalized[canonical] = value
        if any(value not in (None, "") for value in normalized.values()):
            rows.append(normalized)
    if not rows:
        raise ValidationError("文件中没有数据行。")
    return header, rows, _detect_wind_unit(header)


def import_rows(
    actor: User,
    *,
    rows: list[dict],
    header: list[str],
    wind_unit: str,
    source: str,
    source_version: str | None,
    original_filename: str,
    stored_filename: str | None,
    file_size_bytes: int | None,
    strict: bool = False,
    basin_filter: str | None = "WP",
    synoptic_only: bool = False,
    min_year: int | None = None,
    fallback_pressure: tuple[str, ...] = schema.FALLBACK_PRESSURE_COLUMNS,
) -> tuple[DatasetImport, ImportStats]:
    """把已解析的行写入数据库，返回导入记录与统计。

    口径参数（冻结依据 docs/data-card.md）：
        basin_filter      目标盆地；按 **SID 整体**取舍，不按行截断轨迹（D-05）
        synoptic_only     只保留 00/06/12/18 UTC 记录（D-03）；IBTrACS 主记录为 3 小时间隔，
                          不开启此选项无法构成严格 6 小时窗口
        min_year          年份下限（D-04）
        fallback_pressure 气压回退列顺序（D-02）：主列缺失时依次填补并记录来源
    """
    from flask import current_app

    record = DatasetImport(
        source=source,
        source_version=source_version,
        original_filename=original_filename[:255],
        stored_filename=stored_filename,
        file_size_bytes=file_size_bytes,
        status="validating",
        imported_by=actor.id,
    )
    db.session.add(record)
    db.session.commit()

    stats = ImportStats(wind_unit=wind_unit)
    stats.row_count = len(rows)
    raw_lons = []
    for row in rows:
        try:
            raw_lons.append(float(row.get("lon")))
        except (TypeError, ValueError):
            raw_lons.append(None)
    stats.lon_converted = looks_like_0_360(raw_lons)

    focus_area = dict(current_app.config.get("FOCUS_AREA"))
    step_hours = int(current_app.config.get("TIME_STEP_HOURS", schema.TIME_STEP_HOURS))

    # 整个“解析分组 + 写库”都在同一个 try 中：
    # 严格模式下任何一行出错都要整批回滚，并把导入记录标记为 rolled_back（UC-07 异常流程）。
    try:
        # ------------------------------------------------------------------
        # 第一遍：按 SID 收集原始行（不做盆地/年份剔除，保证能按 SID 整体取舍）
        # 口径依据：docs/data-card.md D-05（跨盆地按 SID 保留全轨迹）
        # ------------------------------------------------------------------
        grouped: dict[str, dict] = {}
        for index, row in enumerate(rows):
            excel_row = index + 2
            sid = (row.get("sid") or "").strip()
            if not sid:
                stats.skipped_row_count += 1
                _note_error(stats, f"第 {excel_row} 行缺少 SID，已跳过。", strict)
                continue

            basin = (row.get("basin") or "").strip().upper()
            iso_time = parse_datetime(row.get("iso_time"))
            lat = _to_float(row.get("lat"))
            lon = _to_float(row.get("lon"))
            if iso_time is None or lat is None or lon is None:
                stats.skipped_row_count += 1
                _note_error(stats, f"第 {excel_row} 行的时间或经纬度无法解析，已跳过。", strict)
                continue
            if not (schema.LAT_RANGE[0] <= lat <= schema.LAT_RANGE[1]) or not (
                -180.0 <= lon <= 360.0
            ):
                stats.skipped_row_count += 1
                _note_error(stats, f"第 {excel_row} 行的经纬度超出范围，已跳过。", strict)
                continue

            # D-01/D-03：synoptic 时刻过滤（IBTrACS 主记录为 3 小时间隔，
            # 不重采样则无法构成严格 6 小时窗口，详见 docs/data-card.md）
            if synoptic_only and iso_time.hour not in schema.SYNOPTIC_HOURS:
                stats.skipped_row_count += 1
                continue

            entry = grouped.setdefault(
                sid,
                {
                    "sid": sid,
                    "name": (row.get("name") or "").strip() or None,
                    "basins": set(),
                    "season": None,
                    "points": {},
                },
            )
            if basin:
                entry["basins"].add(basin)
            if entry["name"] is None and (row.get("name") or "").strip():
                entry["name"] = (row.get("name") or "").strip()
            season_raw = row.get("season")
            if entry["season"] is None:
                try:
                    entry["season"] = (
                        int(float(season_raw)) if season_raw not in (None, "") else iso_time.year
                    )
                except (TypeError, ValueError):
                    entry["season"] = iso_time.year
            # 同一 SID 同一时刻只保留第一次出现的有效记录
            point = entry["points"].get(iso_time)
            if point is None:
                point = {
                    "iso_time": iso_time,
                    "lat": normalize_lat(lat),
                    "lon": normalize_lon(lon),
                    "wind_raw": _to_float(row.get("max_wind_mps")),
                    "pressure_primary": _to_float(row.get("pressure_hpa")),
                    "pressure_fallbacks": {},
                }
                entry["points"][iso_time] = point
            # D-02：记录各回退列的原值，供第二遍按优先级填补
            for column in fallback_pressure:
                if column in point["pressure_fallbacks"]:
                    continue
                candidate = _to_float(row.get(column))
                if candidate is not None:
                    point["pressure_fallbacks"][column] = candidate

        # ------------------------------------------------------------------
        # 第二遍：按 SID 决定取舍（盆地 + 年份），再处理风速单位与气压回退
        # ------------------------------------------------------------------
        target_basin = (basin_filter or "").upper()
        min_year = int(min_year) if min_year is not None else None
        kept_sids: list[str] = []
        pressure_fallback_count = 0
        wind_converted_count = 0

        for sid, entry in grouped.items():
            # D-05：台风的任一部分落在目标盆地即保留全部记录（不按行截断轨迹）
            if target_basin and entry["basins"] and target_basin not in entry["basins"]:
                stats.skipped_row_count += len(entry["points"])
                continue

            points: list[dict] = []
            for key in sorted(entry["points"]):
                raw = entry["points"][key]
                # D-01：风速单位换算（IBTrACS USA_WIND 为 knot）
                wind = raw["wind_raw"]
                if wind is not None and wind_unit == "kt":
                    wind = round(wind * KNOT_TO_MPS, 2)
                    wind_converted_count += 1
                if wind is not None and not (
                    schema.WIND_RANGE_MPS[0] <= wind <= schema.WIND_RANGE_MPS[1]
                ):
                    wind = None

                # D-02：气压主列优先，缺失时依次回退到其他机构列
                pressure = raw["pressure_primary"]
                source = "primary"
                if pressure is None:
                    for column in fallback_pressure:
                        candidate = raw["pressure_fallbacks"].get(column)
                        if candidate is None:
                            continue
                        if not (
                            schema.PRESSURE_RANGE_HPA[0]
                            <= candidate
                            <= schema.PRESSURE_RANGE_HPA[1]
                        ):
                            continue
                        pressure = candidate
                        source = f"fallback:{column}"
                        pressure_fallback_count += 1
                        break
                if pressure is not None and not (
                    schema.PRESSURE_RANGE_HPA[0] <= pressure <= schema.PRESSURE_RANGE_HPA[1]
                ):
                    pressure = None
                    source = "invalid"

                points.append(
                    {
                        "iso_time": raw["iso_time"],
                        "lat": raw["lat"],
                        "lon": raw["lon"],
                        "max_wind_mps": wind,
                        "central_pressure_hpa": None if pressure is None else round(pressure, 1),
                        "pressure_source": source,
                    }
                )

            if not points:
                continue
            # D-04：年份下限（按台风起始年判断，避免把跨年台风截断）
            if min_year is not None and points[0]["iso_time"].year < min_year:
                stats.skipped_row_count += len(points)
                stats.skipped_by_year += 1
                continue

            entry["points_list"] = points
            kept_sids.append(sid)

        # ------------------------------------------------------------------
        # 第三遍：写库
        # ------------------------------------------------------------------
        imported_ids: list[int] = []
        for sid in kept_sids:
            entry = grouped[sid]
            points = entry["points_list"]
            typhoon = Typhoon.query.filter_by(sid=sid).first()
            if typhoon is None:
                typhoon = Typhoon(sid=sid)
                db.session.add(typhoon)
            typhoon.name = entry["name"] or typhoon.name
            typhoon.season = entry["season"] or typhoon.season or points[0]["iso_time"].year
            # 主盆地：优先记为筛选目标盆地，否则取该台风实际的第一个盆地标签
            typhoon.basin = target_basin or (sorted(entry["basins"])[0] if entry["basins"] else "WP")
            typhoon.source = source
            typhoon.source_version = source_version
            typhoon.start_time = points[0]["iso_time"]
            typhoon.end_time = points[-1]["iso_time"]
            winds = [p["max_wind_mps"] for p in points if p["max_wind_mps"] is not None]
            pressures = [
                p["central_pressure_hpa"] for p in points if p["central_pressure_hpa"] is not None
            ]
            typhoon.max_wind_mps = max(winds) if winds else None
            typhoon.min_pressure_hpa = min(pressures) if pressures else None
            typhoon.track_point_count = len(points)
            typhoon.in_focus_area = any(
                in_focus_area(p["lat"], p["lon"], focus_area) for p in points
            )
            typhoon.dataset_import_id = record.id
            db.session.flush()

            for point in points:
                code, _name = classify_intensity(point["max_wind_mps"])
                db.session.add(
                    TrackPoint(
                        typhoon_id=typhoon.id,
                        iso_time=point["iso_time"],
                        lat=point["lat"],
                        lon=point["lon"],
                        max_wind_mps=point["max_wind_mps"],
                        central_pressure_hpa=point["central_pressure_hpa"],
                        # D-02：气压来自回退列时标记为 interpolated，便于统计与披露
                        quality_flag=(
                            "original" if point["pressure_source"] == "primary" else "interpolated"
                        ),
                        intensity_category=code,
                        in_focus_area=in_focus_area(point["lat"], point["lon"], focus_area),
                        pressure_source=point["pressure_source"],
                    )
                )
            stats.point_count += len(points)
            imported_ids.append(typhoon.id)
            if stats.typhoon_count % 200 == 0:
                db.session.flush()

        stats.typhoon_count = len(imported_ids)
        stats.pressure_fallback_count = pressure_fallback_count
        stats.wind_converted_count = wind_converted_count
        record.row_count = stats.row_count
        record.typhoon_count = stats.typhoon_count
        record.skipped_row_count = stats.skipped_row_count
        record.status = "imported"
        record.imported_at = utcnow()
        record.message = (
            f"导入完成：{stats.typhoon_count} 个台风、{stats.point_count} 个轨迹点，"
            f"跳过 {stats.skipped_row_count} 行。"
        )
        audit_service.record(
            audit_service.ACTION_DATASET_IMPORT,
            actor=actor,
            target_type="dataset_import",
            target_id=record.id,
            detail=record.message,
        )
        db.session.commit()
        return record, stats

    except Exception as exc:
        db.session.rollback()
        logger.warning("数据集导入失败并整批回滚：%s", exc)
        failed = db.session.get(DatasetImport, record.id)
        if failed is not None:
            failed.status = "rolled_back"
            failed.message = f"导入失败并已整批回滚：{exc}"
            db.session.commit()
            record = failed
        raise ValidationError(
            f"导入过程中出现错误，已整批回滚，数据库未留下半导入状态：{exc}"
        ) from exc


def _note_error(stats: ImportStats, message: str, strict: bool) -> None:
    stats.errors.append(message)
    if strict:
        raise ValidationError(message + "（当前为严格模式，整批回滚）")


def import_upload(
    actor: User,
    *,
    filename: str,
    raw: bytes,
    source: str = "IBTrACS",
    source_version: str | None = None,
    strict: bool = False,
) -> tuple[DatasetImport, ImportStats]:
    """保存原始文件并导入，供管理员接口使用。"""
    from flask import current_app

    max_bytes = int(current_app.config.get("MAX_MODEL_MB", 200)) * 1024 * 1024  # 数据集允许更大
    if len(raw) > max_bytes:
        raise ValidationError(
            f"数据集文件 {len(raw) / 1024 / 1024:.1f} MB 超过 {max_bytes // 1024 // 1024} MB 限制，请先裁剪到 WP 盆地。"
        )
    header, rows, wind_unit = parse_dataset_csv(raw)

    suffix = normalize_stored_filename(filename) or ".csv"
    stored_name = f"{generate_token(16)}{suffix}"
    target_dir = Path(current_app.config["UPLOAD_DIR"]) / "datasets"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / stored_name).write_bytes(raw)

    return import_rows(
        actor,
        rows=rows,
        header=header,
        wind_unit=wind_unit,
        source=source,
        source_version=source_version,
        original_filename=filename,
        stored_filename=stored_name,
        file_size_bytes=len(raw),
        strict=strict,
    )


def list_imports(*, page: int = 1, page_size: int = 20):
    query = DatasetImport.query.order_by(DatasetImport.created_at.desc())
    total = query.count()
    return query.offset((page - 1) * page_size).limit(page_size).all(), total


__all__ = [
    "ImportStats",
    "parse_dataset_csv",
    "import_rows",
    "import_upload",
    "list_imports",
]
