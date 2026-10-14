"""数据质量校验器（DQ-01 ~ DQ-06，FR-005，UC-03）。

设计：
- 纯 Python 实现，不依赖 pandas/numpy，便于在 Web 请求中快速执行与单元测试。
- 返回结构化报告：行级 issue 列表 + 逐行质量标志 + 可用于推理的观测序列。
- 阻断性错误（error）导致整体拒绝；提示性信息（warning）不影响保存，但写入质量报告。

规则映射：
    DQ-01 时间戳可解析、严格递增、相邻间隔 6 小时
    DQ-02 纬度 [-90,90]，经度统一为 [-180,180]（自动转换 0~360 口径）
    DQ-03 风速与气压必须为数值，单位统一 m/s 与 hPa
    DQ-04 至少连续 5 个有效观测点，不足时说明缺失位置
    DQ-05 短缺失可插值并记录 quality_flag=interpolated；关键字段连续缺失不得进入推理
    DQ-06 训练/验证/测试按 SID 划分（见 training/dataset.py，不在本模块）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from app.ml import schema
from app.utils.metrics import haversine_km
from app.utils.timeutil import format_iso, is_exact_step, parse_datetime
from app.utils.units import (
    KNOT_TO_MPS,
    in_focus_area,
    looks_like_0_360,
    normalize_lat,
    normalize_lon,
)

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

STATUS_PASS = "pass"
STATUS_WARN = "warning"
STATUS_FAIL = "fail"


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    row: int | None = None
    column: str | None = None
    value: Any = None

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "row": self.row,
            "column": self.column,
            "value": None if self.value is None else str(self.value),
        }


@dataclass
class ValidatedPoint:
    """通过校验（含插值）的观测点。"""

    iso_time: Any
    lat: float
    lon: float
    max_wind_mps: float | None
    central_pressure_hpa: float | None
    quality_flag: str = "original"
    source_row: int | None = None
    in_focus_area: bool = False

    @property
    def features(self) -> list[float]:
        return [
            float(self.lat),
            float(self.lon),
            float(self.max_wind_mps if self.max_wind_mps is not None else 0.0),
            float(self.central_pressure_hpa if self.central_pressure_hpa is not None else 0.0),
        ]

    def to_dict(self) -> dict:
        return {
            "iso_time": format_iso(self.iso_time),
            "lat": self.lat,
            "lon": self.lon,
            "max_wind_mps": self.max_wind_mps,
            "central_pressure_hpa": self.central_pressure_hpa,
            "quality_flag": self.quality_flag,
            "source_row": self.source_row,
            "in_focus_area": self.in_focus_area,
        }


@dataclass
class ValidationReport:
    status: str = STATUS_PASS
    issues: list[Issue] = field(default_factory=list)
    points: list[ValidatedPoint] = field(default_factory=list)
    row_count: int = 0
    rejected_row_count: int = 0
    interpolated_count: int = 0
    unknown_columns: list[str] = field(default_factory=list)
    sid: str | None = None
    lon_converted: bool = False
    converted_wind_unit: str | None = None
    lon_range_detected: str | None = None
    window: list[list[float]] = field(default_factory=list)  # 供预测使用的输入窗口

    @property
    def ok(self) -> bool:
        return self.status != STATUS_FAIL

    @property
    def error_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == SEVERITY_ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == SEVERITY_WARNING)

    def add(self, severity: str, code: str, message: str, **kwargs) -> None:
        self.issues.append(Issue(severity=severity, code=code, message=message, **kwargs))

    def first_errors(self, limit: int = 20) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == SEVERITY_ERROR][:limit]

    def to_dict(self, *, issue_limit: int = 50) -> dict:
        errors = [issue.to_dict() for issue in self.issues if issue.severity == SEVERITY_ERROR]
        warnings = [issue.to_dict() for issue in self.issues if issue.severity == SEVERITY_WARNING]
        infos = [issue.to_dict() for issue in self.issues if issue.severity == SEVERITY_INFO]
        return {
            "status": self.status,
            "ok": self.ok,
            "row_count": self.row_count,
            "rejected_row_count": self.rejected_row_count,
            "valid_point_count": len(self.points),
            "interpolated_count": self.interpolated_count,
            "sid": self.sid,
            "lon_converted": self.lon_converted,
            "lon_range_detected": self.lon_range_detected,
            "converted_wind_unit": self.converted_wind_unit,
            "unknown_columns": self.unknown_columns,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "errors": errors[:issue_limit],
            "warnings": warnings[:issue_limit],
            "infos": infos[:issue_limit],
            "truncated": len(errors) > issue_limit or len(warnings) > issue_limit,
        }


def _cell(row: dict, column: str):
    value = row.get(column)
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None
    return value


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return number


def build_validation_report(
    rows: Sequence[dict],
    *,
    input_length: int = schema.INPUT_LENGTH,
    enforce_input_window: bool = True,
    wind_unit: str | None = None,
    max_issues: int = 500,
    knot_auto_threshold: float | None = None,
) -> ValidationReport:
    """校验一批已解析为字典的行。

    参数：
        rows: 每行形如 {"sid": ..., "iso_time": ..., "lat": ..., "lon": ...,
                        "max_wind_mps": ..., "pressure_hpa": ...}（键必须是标准列名）
        wind_unit: 当调用方已知原始风速单位为 knot 时传 "kt"，否则按 m/s 处理；
                   若为 None 且数值超过物理上限（默认 70 m/s，见 schema.KNOT_AUTO_THRESHOLD_MPS），
                   会按 knot 自动换算并给出警告。
        enforce_input_window: 是否强制要求存在连续 input_length 个有效点（DQ-04）
        knot_auto_threshold: 覆盖自动换算阈值，便于测试或特殊数据源。
    """
    threshold = (
        float(knot_auto_threshold)
        if knot_auto_threshold is not None
        else float(getattr(schema, "KNOT_AUTO_THRESHOLD_MPS", 70.0))
    )
    report = ValidationReport(row_count=len(rows))

    def add(severity: str, code: str, message: str, **kwargs) -> None:
        if len(report.issues) < max_issues:
            report.add(severity, code, message, **kwargs)
        elif len(report.issues) == max_issues:
            report.add(
                SEVERITY_WARNING,
                "ISSUE_TRUNCATED",
                f"问题数量过多，仅显示前 {max_issues} 条，请修正后重新上传。",
            )

    if not rows:
        add(SEVERITY_ERROR, "CSV_EMPTY", "CSV 中没有数据行，请检查文件内容。")
        report.status = STATUS_FAIL
        return report

    # ------------------------------------------------------------------
    # 收集候选点（同时执行字段级校验）
    # ------------------------------------------------------------------
    raw_lons = [_to_float(_cell(row, "lon")) for row in rows]
    lon_converted = looks_like_0_360(raw_lons)
    report.lon_converted = lon_converted
    report.lon_range_detected = "0-360" if lon_converted else "-180-180"

    candidates: list[ValidatedPoint] = []
    seen_times: set = set()

    for index, row in enumerate(rows):
        excel_row = index + 2  # 表头占第 1 行，便于用户按 Excel 行号定位

        sid_raw = _cell(row, "sid")
        if sid_raw is None:
            add(SEVERITY_ERROR, "DQ01_SID_MISSING", "缺少台风编号 sid，该行无法归属到任何台风。", row=excel_row, column="sid")
            report.rejected_row_count += 1
            continue
        sid_text = str(sid_raw).strip()
        if report.sid is None:
            report.sid = sid_text
        elif sid_text != report.sid:
            add(
                SEVERITY_ERROR,
                "MULTI_SID",
                f"一个文件只能包含一个台风编号，本行为 {sid_text}，文件首个编号为 {report.sid}。",
                row=excel_row,
                column="sid",
                value=sid_text,
            )
            report.rejected_row_count += 1
            continue

        time_raw = _cell(row, "iso_time")
        parsed_time = parse_datetime(time_raw)
        if parsed_time is None:
            add(
                SEVERITY_ERROR,
                "DQ01_TIME_UNPARSABLE",
                f"时间戳无法解析（当前值 {time_raw!r}），请使用形如 2026-07-18T00:00:00Z 的 UTC 时间。",
                row=excel_row,
                column="iso_time",
                value=time_raw,
            )
            report.rejected_row_count += 1
            continue
        if parsed_time in seen_times:
            add(
                SEVERITY_ERROR,
                "DQ01_TIME_DUPLICATE",
                f"时间戳重复：{format_iso(parsed_time)}，同一台风同一时刻只能出现一次。",
                row=excel_row,
                column="iso_time",
                value=format_iso(parsed_time),
            )
            report.rejected_row_count += 1
            continue
        seen_times.add(parsed_time)

        lat_raw = _to_float(_cell(row, "lat"))
        lon_raw = _to_float(_cell(row, "lon"))
        if lat_raw is None:
            add(SEVERITY_ERROR, "DQ02_LAT_INVALID", "纬度必须为数值。", row=excel_row, column="lat", value=_cell(row, "lat"))
            report.rejected_row_count += 1
            continue
        if lon_raw is None:
            add(SEVERITY_ERROR, "DQ02_LON_INVALID", "经度必须为数值。", row=excel_row, column="lon", value=_cell(row, "lon"))
            report.rejected_row_count += 1
            continue
        if not (schema.LAT_RANGE[0] <= lat_raw <= schema.LAT_RANGE[1]):
            add(
                SEVERITY_ERROR,
                "DQ02_LAT_RANGE",
                f"纬度 {lat_raw} 超出 [{schema.LAT_RANGE[0]}, {schema.LAT_RANGE[1]}]，请检查字段是否串列。",
                row=excel_row,
                column="lat",
                value=lat_raw,
            )
            report.rejected_row_count += 1
            continue
        # 经度：原始允许 0~360 或 -180~180，超出 [0,360] 一律拒绝
        if not (-180.0 <= lon_raw <= 360.0):
            add(
                SEVERITY_ERROR,
                "DQ02_LON_RANGE",
                f"经度 {lon_raw} 超出可接受范围，应使用 0~360 或 -180~180 口径。",
                row=excel_row,
                column="lon",
                value=lon_raw,
            )
            report.rejected_row_count += 1
            continue

        wind_raw = _to_float(_cell(row, "max_wind_mps"))
        if wind_raw is None:
            add(
                SEVERITY_ERROR,
                "DQ03_WIND_INVALID",
                "最大持续风速必须为数值，单位为 m/s。",
                row=excel_row,
                column="max_wind_mps",
                value=_cell(row, "max_wind_mps"),
            )
            report.rejected_row_count += 1
            continue
        if wind_raw < 0:
            add(
                SEVERITY_ERROR,
                "DQ03_WIND_NEGATIVE",
                f"最大持续风速不能为负值（当前 {wind_raw}）。",
                row=excel_row,
                column="max_wind_mps",
                value=wind_raw,
            )
            report.rejected_row_count += 1
            continue

        wind_mps = wind_raw
        if wind_unit == "kt" or (
            wind_unit is None and threshold < wind_raw <= schema.KNOT_MAX_PLAUSIBLE
        ):
            wind_mps = round(wind_raw * KNOT_TO_MPS, 2)
            report.converted_wind_unit = "kt->m/s"
        if wind_mps > schema.WIND_RANGE_MPS[1]:
            add(
                SEVERITY_ERROR,
                "DQ03_WIND_RANGE",
                f"风速 {wind_mps} m/s 超出合理上限 {schema.WIND_RANGE_MPS[1]} m/s，请确认单位是否为 m/s。",
                row=excel_row,
                column="max_wind_mps",
                value=wind_mps,
            )
            report.rejected_row_count += 1
            continue

        pressure_raw = _to_float(_cell(row, "pressure_hpa"))
        if pressure_raw is None:
            add(
                SEVERITY_ERROR,
                "DQ03_PRESSURE_INVALID",
                "最低中心气压必须为数值，单位为 hPa。",
                row=excel_row,
                column="pressure_hpa",
                value=_cell(row, "pressure_hpa"),
            )
            report.rejected_row_count += 1
            continue
        if not (schema.PRESSURE_RANGE_HPA[0] <= pressure_raw <= schema.PRESSURE_RANGE_HPA[1]):
            add(
                SEVERITY_ERROR,
                "DQ03_PRESSURE_RANGE",
                f"气压 {pressure_raw} hPa 超出合理范围 "
                f"[{schema.PRESSURE_RANGE_HPA[0]}, {schema.PRESSURE_RANGE_HPA[1]}] hPa，请确认单位。",
                row=excel_row,
                column="pressure_hpa",
                value=pressure_raw,
            )
            report.rejected_row_count += 1
            continue

        point = ValidatedPoint(
            iso_time=parsed_time,
            lat=normalize_lat(lat_raw),  # type: ignore[arg-type]
            lon=normalize_lon(lon_raw),  # type: ignore[arg-type]
            max_wind_mps=wind_mps,
            central_pressure_hpa=round(pressure_raw, 1),
            quality_flag="original",
            source_row=excel_row,
        )
        candidates.append(point)

    if not candidates:
        add(
            SEVERITY_ERROR,
            "NO_VALID_POINT",
            f"没有任何一行通过校验（共 {report.row_count} 行，全部被拒绝），请先按上面的行级提示修正。",
        )
        report.status = STATUS_FAIL
        return report

    if lon_converted:
        add(
            SEVERITY_INFO,
            "DQ02_LON_CONVERTED",
            "检测到经度为 0~360 口径，系统已统一转换为 -180~180 口径保存。",
        )
    if report.converted_wind_unit:
        add(
            SEVERITY_WARNING,
            "DQ03_WIND_UNIT_CONVERTED",
            "检测到风速数值过大，已按 knot 换算为 m/s；若原始单位不是 knot，请修正后重新上传。",
        )

    # ------------------------------------------------------------------
    # DQ-01：严格递增 + 6 小时间隔
    # ------------------------------------------------------------------
    candidates.sort(key=lambda item: item.iso_time)
    ordered: list[ValidatedPoint] = []
    for previous, current in zip(candidates, candidates[1:]):
        if current.iso_time <= previous.iso_time:
            add(
                SEVERITY_ERROR,
                "DQ01_TIME_NOT_INCREASING",
                f"时间序列未严格递增：{format_iso(current.iso_time)} 未晚于 {format_iso(previous.iso_time)}。",
                row=current.source_row,
                column="iso_time",
            )
            report.rejected_row_count += 1
            continue
        ordered.append(previous)
    ordered.append(candidates[-1])

    # ------------------------------------------------------------------
    # DQ-05：短缺口插值（仅允许 12 小时内的单点缺口）
    # ------------------------------------------------------------------
    filled: list[ValidatedPoint] = []
    for previous, current in zip(ordered, ordered[1:]):
        filled.append(previous)
        gap_hours = (current.iso_time - previous.iso_time).total_seconds() / 3600.0
        missing = int(round(gap_hours / schema.TIME_STEP_HOURS)) - 1
        if missing <= 0:
            continue
        if missing <= schema.MAX_INTERPOLATE_GAP:
            # 单点线性插值，保留插值标志（DQ-05）
            for step in range(1, missing + 1):
                ratio = step / (missing + 1)
                filled.append(
                    ValidatedPoint(
                        iso_time=previous.iso_time
                        + (current.iso_time - previous.iso_time) * ratio,
                        lat=round(previous.lat + (current.lat - previous.lat) * ratio, 3),
                        lon=round(
                            normalize_lon(
                                previous.lon + (current.lon - previous.lon) * ratio
                            ),  # type: ignore[arg-type]
                            3,
                        ),
                        max_wind_mps=(
                            round(previous.max_wind_mps + (current.max_wind_mps - previous.max_wind_mps) * ratio, 2)
                            if previous.max_wind_mps is not None and current.max_wind_mps is not None
                            else None
                        ),
                        central_pressure_hpa=(
                            round(
                                previous.central_pressure_hpa
                                + (current.central_pressure_hpa - previous.central_pressure_hpa) * ratio,
                                1,
                            )
                            if previous.central_pressure_hpa is not None
                            and current.central_pressure_hpa is not None
                            else None
                        ),
                        quality_flag="interpolated",
                        source_row=current.source_row,
                    )
                )
                report.interpolated_count += 1
            add(
                SEVERITY_WARNING,
                "DQ05_GAP_INTERPOLATED",
                f"在 {format_iso(previous.iso_time)} 与 {format_iso(current.iso_time)} 之间存在 {int(gap_hours)} 小时缺口，"
                f"已线性插值 {missing} 个点并标记为 interpolated。",
                row=current.source_row,
                column="iso_time",
            )
        else:
            add(
                SEVERITY_ERROR,
                "DQ05_GAP_TOO_LARGE",
                f"在 {format_iso(previous.iso_time)} 与 {format_iso(current.iso_time)} 之间存在 {int(gap_hours)} 小时缺口"
                f"（超过 {schema.MAX_INTERPOLATE_GAP * schema.TIME_STEP_HOURS * 2} 小时），缺失过多无法插值，"
                "请补充观测点后重新上传。",
                row=current.source_row,
                column="iso_time",
            )
            report.rejected_row_count += 1
    if ordered:
        filled.append(ordered[-1])

    # 插值后若仍存在非 6 小时步长，视为阻断性错误（DQ-01）
    for previous, current in zip(filled, filled[1:]):
        if not is_exact_step(current.iso_time, previous.iso_time, schema.TIME_STEP_HOURS):
            add(
                SEVERITY_ERROR,
                "DQ01_TIME_STEP",
                f"{format_iso(previous.iso_time)} 与 {format_iso(current.iso_time)} 的间隔不是 6 小时，"
                "本系统仅支持严格 6 小时步长的观测序列，请补齐缺失时刻后重新上传。",
                row=current.source_row,
                column="iso_time",
            )
            report.rejected_row_count += 1

    # 缺测字段统计（DQ-05：关键字段连续缺失不得进入推理）
    for point in filled:
        if point.max_wind_mps is None or point.central_pressure_hpa is None:
            report.add(
                SEVERITY_WARNING,
                "DQ05_FIELD_MISSING",
                f"{format_iso(point.iso_time)} 的风速或气压缺失，该点不参与模型输入（模型要求四个特征齐全）。",
                row=point.source_row,
                column="max_wind_mps" if point.max_wind_mps is None else "pressure_hpa",
            )

    report.points = filled

    # 可疑跳变提示（非阻断）
    for previous, current in zip(filled, filled[1:]):
        distance = haversine_km(previous.lat, previous.lon, current.lat, current.lon)
        if distance > schema.SUSPICIOUS_JUMP_KM_PER_6H:
            add(
                SEVERITY_WARNING,
                "DQ02_SUSPICIOUS_JUMP",
                f"{format_iso(current.iso_time)} 相对前一点位移 {distance:.0f} km，明显超过 6 小时合理移动距离，请核对经纬度。",
                row=current.source_row,
                column="lat",
            )

    for point in filled:
        point.in_focus_area = in_focus_area(point.lat, point.lon, _focus_area())

    # ------------------------------------------------------------------
    # DQ-04：必须存在连续 input_length 个完整点
    # ------------------------------------------------------------------
    window = extract_input_window(filled, input_length=input_length)
    report.window = window
    if window:
        add(
            SEVERITY_INFO,
            "DQ04_WINDOW_READY",
            f"已找到连续 {input_length} 个有效观测点作为模型输入窗口"
            f"（{format_iso(filled[-input_length].iso_time)} 至 {format_iso(filled[-1].iso_time)}）。",
        )
    elif enforce_input_window:
        missing_positions = describe_missing_positions(filled, input_length=input_length)
        add(
            SEVERITY_ERROR,
            "DQ04_INSUFFICIENT_POINTS",
            f"输入至少需要连续 {input_length} 个同时具备经纬度、风速、气压的观测点，"
            f"当前可用 {len(filled)} 个点。{missing_positions}",
        )

    if report.error_count:
        report.status = STATUS_FAIL
    elif report.warning_count:
        report.status = STATUS_WARN
    else:
        report.status = STATUS_PASS
    return report


def _focus_area() -> dict:
    try:
        from flask import current_app

        return dict(current_app.config.get("FOCUS_AREA"))
    except RuntimeError:
        return {"lon_min": 105.0, "lon_max": 135.0, "lat_min": 5.0, "lat_max": 45.0}


def has_complete_features(point: ValidatedPoint) -> bool:
    return point.max_wind_mps is not None and point.central_pressure_hpa is not None


def extract_input_window(
    points: Sequence[ValidatedPoint], *, input_length: int = schema.INPUT_LENGTH
) -> list[list[float]]:
    """从序列末尾向前取连续 input_length 个四特征齐全的点，返回 [[lat, lon, wind, pres], ...]。

    DQ-04：如果末尾这些点之间存在时间断层（不是严格 6 小时步长），则返回空列表，
    由调用方给出“无法构成输入窗口”的原因（describe_missing_positions）。
    注意：不能在这里做去重，否则断层会被抹平，模型会拿到跨越数十小时的“连续”窗口。
    """
    ordered = sorted(points, key=lambda item: item.iso_time)
    complete = [point for point in ordered if has_complete_features(point)]
    if len(complete) < input_length:
        return []
    tail = complete[-input_length:]
    for previous, current in zip(tail, tail[1:]):
        if not is_exact_step(current.iso_time, previous.iso_time, schema.TIME_STEP_HOURS):
            return []
    return [point.features for point in tail]


def describe_missing_positions(
    points: Sequence[ValidatedPoint], *, input_length: int = schema.INPUT_LENGTH
) -> str:
    """解释为什么凑不出输入窗口（DQ-04 要求说明缺失位置）。"""
    if not points:
        return "文件中没有可用观测点。"
    incomplete_rows = [
        point.source_row for point in points if not has_complete_features(point)
    ]
    parts: list[str] = []
    if incomplete_rows:
        shown = "、".join(str(row) for row in incomplete_rows[:10])
        parts.append(f"以下行缺少风速或气压：{shown}")
    complete_count = sum(1 for point in points if has_complete_features(point))
    parts.append(f"其中字段齐全的点共 {complete_count} 个，需要 {input_length} 个")
    if complete_count >= input_length:
        parts.append("但字段齐全的点之间存在时间断层，请补齐缺失时刻或改用连续时间段上传")
    return "；".join(parts) + "。"


def validate_csv_rows(
    rows: Iterable[dict],
    *,
    header: Sequence[str] | None = None,
    input_length: int = schema.INPUT_LENGTH,
    wind_unit: str | None = None,
) -> ValidationReport:
    """面向 Web 上传的入口：先做表头检查，再执行行级校验。"""
    rows = list(rows)
    report = ValidationReport()

    def add(severity: str, code: str, message: str, **kwargs) -> None:
        report.add(severity, code, message, **kwargs)

    if header is not None:
        normalized = [schema.canonical_column(name) for name in header]
        unknown = [
            str(name) for name, canonical in zip(header, normalized) if canonical is None
        ]
        report.unknown_columns = unknown
        if unknown:
            add(
                SEVERITY_WARNING,
                "CSV_UNKNOWN_COLUMN",
                f"以下列无法识别，将被忽略：{'、'.join(unknown)}。可用列名为 "
                f"{'、'.join(schema.REQUIRED_CSV_COLUMNS)}。",
            )
        missing = [name for name in schema.REQUIRED_CSV_COLUMNS if name not in normalized]
        if missing:
            add(
                SEVERITY_ERROR,
                "CSV_HEADER_MISSING",
                f"缺少必需列：{'、'.join(missing)}。表头必须包含 "
                f"{'、'.join(schema.REQUIRED_CSV_COLUMNS)}（UTF-8 编码，逗号分隔）。",
            )
            report.status = STATUS_FAIL
            return report

    body = build_validation_report(
        rows, input_length=input_length, enforce_input_window=True, wind_unit=wind_unit
    )
    # 保留表头阶段发现的问题，并追加行级校验结果
    header_issues = report.issues
    seen: set[tuple] = set()
    merged: list[Issue] = []
    for issue in list(header_issues) + list(body.issues):
        key = (issue.severity, issue.code, issue.row, issue.column, issue.message)
        if key in seen:
            continue
        seen.add(key)
        merged.append(issue)
    report.issues = merged
    report.points = body.points
    report.row_count = body.row_count
    report.rejected_row_count = body.rejected_row_count
    report.interpolated_count = body.interpolated_count
    report.sid = body.sid
    report.lon_converted = body.lon_converted
    report.lon_range_detected = body.lon_range_detected
    report.converted_wind_unit = body.converted_wind_unit
    report.window = body.window
    report.status = body.status

    if header is not None and report.error_count:
        report.status = STATUS_FAIL
    elif header is not None and report.warning_count and report.status == STATUS_PASS:
        report.status = STATUS_WARN
    return report


__all__ = [
    "Issue",
    "ValidatedPoint",
    "ValidationReport",
    "SEVERITY_ERROR",
    "SEVERITY_WARNING",
    "SEVERITY_INFO",
    "STATUS_PASS",
    "STATUS_WARN",
    "STATUS_FAIL",
    "build_validation_report",
    "validate_csv_rows",
    "extract_input_window",
    "describe_missing_positions",
]
