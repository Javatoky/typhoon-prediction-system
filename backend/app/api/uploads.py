"""CSV 上传与校验接口（FR-004 / FR-005、UC-03）。"""

from __future__ import annotations

from flask import Blueprint, request

from app.api.deps import current_user, login_required
from app.core.errors import PayloadTooLargeError, ValidationError
from app.core.params import parse_pagination
from app.core.responses import paginated, success
from app.services import audit_service, upload_service

bp = Blueprint("uploads", __name__, url_prefix="/api/uploads")


def _read_upload_file() -> tuple[str, bytes]:
    if "file" not in request.files:
        raise ValidationError(
            "未接收到文件。请使用 multipart/form-data 提交，字段名为 file（例如 "
            "curl -F \"file=@track.csv\"）。"
        )
    storage = request.files["file"]
    filename = storage.filename or "upload.csv"
    if not filename:
        raise ValidationError("文件名不能为空。")
    raw = storage.read()
    if not raw:
        raise ValidationError("上传的文件为空，请重新选择。")

    from flask import current_app

    max_bytes = int(current_app.config.get("MAX_UPLOAD_MB", 20)) * 1024 * 1024
    if len(raw) > max_bytes:
        raise PayloadTooLargeError(
            f"文件大小 {len(raw) / 1024 / 1024:.1f} MB 超过 "
            f"{current_app.config.get('MAX_UPLOAD_MB', 20)} MB 限制，请在解析前裁剪后重新上传。"
        )
    return filename, raw


@bp.post("/track")
@login_required
def upload_track():
    """上传并校验轨迹 CSV；校验通过后保存有效输入（UC-03）。"""
    filename, raw = _read_upload_file()
    user = current_user()
    result = upload_service.save_upload(user, filename=filename, raw=raw)
    audit_service.record(
        audit_service.ACTION_UPLOAD_TRACK,
        actor=user,
        target_type="upload",
        target_id=result.upload.id,
        detail=f"{filename} 有效点 {result.upload.valid_point_count}/{result.upload.row_count}",
    )
    from app.extensions import db

    db.session.commit()
    return success(result.to_dict(), status=201)


@bp.post("/track/validate")
@login_required
def validate_track():
    """只校验不保存，便于用户在正式提交前查看质量报告。"""
    filename, raw = _read_upload_file()
    report = upload_service.validate_upload_bytes(raw, filename=filename)
    return success({"validation": report.to_dict(), "saved": False})


@bp.get("")
@login_required
def list_uploads():
    params = {key: value for key, value in request.args.items()}
    from flask import current_app

    page, page_size = parse_pagination(
        params, default=int(current_app.config.get("PAGE_SIZE_DEFAULT", 20)), maximum=100
    )
    items, total = upload_service.list_uploads(current_user(), page=page, page_size=page_size)
    return paginated(
        [item.to_dict() for item in items], page=page, page_size=page_size, total=total
    )


@bp.get("/<int:upload_id>")
@login_required
def upload_detail(upload_id: int):
    upload = upload_service.get_upload(current_user(), upload_id)
    return success(upload.to_dict(with_points=True))


__all__ = ["bp"]
