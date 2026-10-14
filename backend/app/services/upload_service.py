"""CSV 上传与校验服务（FR-004 / FR-005、UC-03、6.2 安全要求）。

安全要点：
- 上传落盘使用随机文件名 + 仅保留字母数字后缀，禁止路径穿越与可执行扩展名。
- 超限文件在解析前拒绝（NFR-004）。
- 任何阻断性错误都不保存业务数据，并按行号返回原因。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.core.errors import PayloadTooLargeError, ValidationError
from app.extensions import db
from app.ml import schema
from app.ml.validator import ValidationReport, validate_csv_rows
from app.models import Upload, UploadPoint, User
from app.utils.timeutil import format_iso, is_exact_step

logger = logging.getLogger(__name__)

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030")


@dataclass
class UploadResult:
    upload: Upload
    report: ValidationReport
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "upload": self.upload.to_dict(with_points=False),
            "validation": self.report.to_dict(),
        }


def _allowed_suffix(filename: str) -> str:
    from flask import current_app

    suffix = Path(filename).suffix.lower()
    allowed = [item.lower() for item in current_app.config.get("ALLOWED_UPLOAD_SUFFIXES", [".csv"])]
    if suffix not in allowed:
        raise ValidationError(
            f"只允许上传 {'、'.join(allowed)} 文件，当前文件类型为 {suffix or '未知'}。"
        )
    return suffix


def _read_text(raw: bytes) -> tuple[str, str]:
    """按 UTF-8 优先尝试解码，失败时回退 GB18030，并返回实际使用的编码。"""
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValidationError(
        "文件编码无法识别，请另存为 UTF-8 编码的 CSV 后重新上传（Excel 可选“CSV UTF-8(逗号分隔)”。"
    )


def _normalize_rows(reader: csv.DictReader) -> tuple[list[str], list[dict]]:
    """把任意表头归一化为标准列名。"""
    raw_header = [name.strip() if name else "" for name in (reader.fieldnames or [])]
    if not raw_header:
        raise ValidationError("CSV 没有表头行，第一行必须是字段名。")
    mapping: dict[str, str] = {}
    for name in raw_header:
        canonical = schema.canonical_column(name)
        if canonical and canonical not in mapping.values():
            mapping[name] = canonical

    rows: list[dict] = []
    for raw_row in reader:
        normalized: dict = {}
        for key, value in raw_row.items():
            canonical = mapping.get((key or "").strip())
            if canonical:
                normalized[canonical] = value
        if any(value not in (None, "") for value in normalized.values()):
            rows.append(normalized)
    return raw_header, rows


def parse_csv_bytes(raw: bytes, *, input_length: int | None = None) -> tuple[list[str], list[dict], str]:
    """解析 CSV 字节内容，返回 (原始表头, 归一化行, 编码)。"""
    from flask import current_app

    text, encoding = _read_text(raw)
    buffer = io.StringIO(text, newline="")
    try:
        reader = csv.DictReader(buffer)
        header, rows = _normalize_rows(reader)
    except csv.Error as exc:
        raise ValidationError(f"CSV 解析失败：{exc}。请检查是否存在未闭合的引号或分隔符错误。") from None

    limit = int(input_length or current_app.config.get("INPUT_LENGTH", schema.INPUT_LENGTH))
    if not rows:
        raise ValidationError("CSV 中没有数据行，请检查文件内容。")
    # 只保留最近若干行参与推理窗口，但校验全部行以便给出完整质量报告
    return header, rows, encoding


def validate_upload_bytes(raw: bytes, *, filename: str = "upload.csv", input_length: int | None = None) -> ValidationReport:
    """只做校验（不落盘、不写库），供管理员导入与单元测试复用。"""
    from flask import current_app

    header, rows, _encoding = parse_csv_bytes(raw, input_length=input_length)
    limit = int(input_length or current_app.config.get("INPUT_LENGTH", schema.INPUT_LENGTH))
    report = validate_csv_rows(rows, header=header, input_length=limit)
    if report.sid is None:
        report.add("warning", "CSV_NO_SID", "未能识别 sid 列的有效值。")
    return report


def save_upload(user: User, *, filename: str, raw: bytes, input_length: int | None = None) -> UploadResult:
    """完整的上传流程：大小与类型检查 → 解析 → 校验 → 保存有效输入。"""
    from flask import current_app

    max_bytes = int(current_app.config.get("MAX_UPLOAD_MB", 20)) * 1024 * 1024
    if len(raw) > max_bytes:
        raise PayloadTooLargeError(
            f"文件大小 {len(raw) / 1024 / 1024:.1f} MB 超过 {current_app.config.get('MAX_UPLOAD_MB', 20)} MB 限制，"
            "请在解析前裁剪数据后重新上传。"
        )
    if not raw:
        raise ValidationError("上传的文件为空，请重新选择。")

    _allowed_suffix(filename)
    limit = int(input_length or current_app.config.get("INPUT_LENGTH", schema.INPUT_LENGTH))
    header, rows, encoding = parse_csv_bytes(raw, input_length=limit)

    report = validate_csv_rows(rows, header=header, input_length=limit)
    if not report.ok:
        # 阻断性错误：不保存任何业务数据（UC-03 异常流程）
        raise ValidationError(
            f"数据校验未通过（共 {report.error_count} 处错误），已拒绝保存，请按行号修正后重新上传。",
            details=report.to_dict(issue_limit=50),
        )

    # 落盘：随机文件名，只保留安全后缀（6.2）
    from app.core.security import generate_token, normalize_stored_filename

    suffix = normalize_stored_filename(filename) or ".csv"
    stored_name = f"{generate_token(16)}{suffix}"
    upload_dir = Path(current_app.config["UPLOAD_DIR"])
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / stored_name
    target.write_bytes(raw)

    checksum = hashlib.sha256(raw).hexdigest()
    upload = Upload(
        user_id=user.id,
        label=f"{report.sid or Path(filename).stem}（{len(report.points)} 点）",
        original_filename=Path(filename).name[:255],
        stored_filename=stored_name,
        file_size_bytes=len(raw),
        checksum=checksum,
        sid=report.sid,
        row_count=report.row_count,
        valid_point_count=len(report.points),
        quality_summary_json=json.dumps(report.to_dict(), ensure_ascii=False),
        status="validated",
    )
    db.session.add(upload)
    db.session.flush()  # 取得 upload.id

    for point in report.points:
        db.session.add(
            UploadPoint(
                upload_id=upload.id,
                iso_time=point.iso_time,
                lat=point.lat,
                lon=point.lon,
                max_wind_mps=point.max_wind_mps,
                central_pressure_hpa=point.central_pressure_hpa,
                quality_flag=point.quality_flag,
                source_row=point.source_row,
            )
        )
    db.session.commit()

    logger.info(
        "用户 %s 上传 %s：%d 行，%d 个有效点（编码 %s）",
        user.username,
        filename,
        report.row_count,
        len(report.points),
        encoding,
    )
    return UploadResult(upload=upload, report=report)


def list_uploads(user: User, *, page: int = 1, page_size: int = 20) -> tuple[list[Upload], int]:
    query = Upload.query.filter_by(user_id=user.id).order_by(Upload.created_at.desc())
    total = query.count()
    return query.offset((page - 1) * page_size).limit(page_size).all(), total


def get_upload(user: User, upload_id: int) -> Upload:
    from app.core.errors import NotFoundError

    upload = Upload.query.get(upload_id)
    if upload is None or (upload.user_id != user.id and not user.is_admin):
        raise NotFoundError(f"未找到编号为 {upload_id} 的上传记录。")
    return upload


def window_from_upload(upload: Upload, *, input_length: int) -> list[list[float]]:
    """从上传记录中取末尾连续 input_length 个点作为推理窗口。"""
    points = sorted(upload.points, key=lambda point: point.iso_time)
    report_window: list[list[float]] = []
    complete = [
        point
        for point in points
        if point.max_wind_mps is not None and point.central_pressure_hpa is not None
    ]
    if len(complete) < input_length:
        return []
    tail = complete[-input_length:]
    for previous, current in zip(tail, tail[1:]):
        if not is_exact_step(current.iso_time, previous.iso_time, 6):
            return []
    for point in tail:
        report_window.append(
            [
                float(point.lat),
                float(point.lon),
                float(point.max_wind_mps),
                float(point.central_pressure_hpa),
            ]
        )
    return report_window


def window_times(upload: Upload, *, input_length: int) -> list:
    points = sorted(upload.points, key=lambda point: point.iso_time)
    return [point.iso_time for point in points[-input_length:]]


__all__ = [
    "UploadResult",
    "parse_csv_bytes",
    "validate_upload_bytes",
    "save_upload",
    "list_uploads",
    "get_upload",
    "window_from_upload",
    "window_times",
    "format_iso",
]
