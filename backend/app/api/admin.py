"""管理员接口（FR-012 ~ FR-015、7 外部接口需求、7.1 无权限接口仍须拒绝）。"""

from __future__ import annotations

from flask import Blueprint, request

from app.api.deps import admin_required, current_user
from app.core.errors import ValidationError
from app.core.params import get_json, optional_bool, optional_int, optional_choice, parse_pagination, require_str
from app.core.responses import paginated, success
from app.extensions import db
from app.ml.registry import get_registry
from app.models import AuditLog, ModelVersion
from app.services import (
    audit_service,
    dataset_service,
    model_service,
    prediction_service,
    user_service,
)

bp = Blueprint("admin", __name__, url_prefix="/api/admin")


# ---------------------------------------------------------------------------
# 用户管理（FR-012）
# ---------------------------------------------------------------------------
@bp.get("/users")
@admin_required
def list_users():
    params = {key: value for key, value in request.args.items()}
    from flask import current_app

    page, page_size = parse_pagination(
        params, default=int(current_app.config.get("PAGE_SIZE_DEFAULT", 20)), maximum=100
    )
    items, total = user_service.list_users(
        keyword=params.get("keyword") or None, page=page, page_size=page_size
    )
    return paginated(
        [user.to_dict(include_private=True) for user in items],
        page=page,
        page_size=page_size,
        total=total,
    )


@bp.post("/users")
@admin_required
def create_user():
    payload = get_json()
    username = require_str(payload, "username", min_length=3, max_length=32, label="用户名")
    password = payload.get("password") or None
    if password is not None:
        password = require_str(payload, "password", min_length=8, max_length=128, label="初始密码")
    role = optional_choice(payload, "role", ["user", "admin"], default="user", label="角色")
    display_name = str(payload.get("display_name") or "").strip()
    user, initial_password = user_service.create_user(
        current_user(),
        username=username,
        password=password,
        display_name=display_name,
        role=role or "user",
    )
    data = {"user": user.to_dict(include_private=True)}
    if initial_password:
        # 仅在本次响应中返回一次，不写日志（6.2）
        data["initial_password"] = initial_password
        data["notice"] = "请通过安全渠道告知用户，并提醒其首次登录后立即修改密码。"
    return success(data, status=201)


@bp.put("/users/<int:user_id>")
@admin_required
def update_user(user_id: int):
    payload = get_json()
    user = user_service.update_user(
        current_user(),
        user_id,
        status=payload.get("status") or None,
        role=payload.get("role") or None,
        display_name=payload.get("display_name") if "display_name" in payload else None,
    )
    return success({"user": user.to_dict(include_private=True)})


@bp.post("/users/<int:user_id>/reset-password")
@admin_required
def reset_password(user_id: int):
    payload = get_json() if request.is_json else {}
    new_password = payload.get("new_password") or None
    if new_password is not None:
        new_password = require_str(payload, "new_password", min_length=8, max_length=128, label="新密码")
    user, password = user_service.reset_password(current_user(), user_id, new_password=new_password)
    return success(
        {
            "user": user.to_dict(include_private=True),
            "new_password": password,
            "notice": "请通过安全渠道告知用户，并提醒其登录后立即修改密码。",
        }
    )


# ---------------------------------------------------------------------------
# 数据集管理（FR-013）
# ---------------------------------------------------------------------------
@bp.get("/datasets")
@admin_required
def list_datasets():
    params = {key: value for key, value in request.args.items()}
    from flask import current_app

    page, page_size = parse_pagination(
        params, default=int(current_app.config.get("PAGE_SIZE_DEFAULT", 20)), maximum=100
    )
    items, total = dataset_service.list_imports(page=page, page_size=page_size)
    return paginated(
        [item.to_dict() for item in items], page=page, page_size=page_size, total=total
    )


@bp.post("/datasets/import")
@admin_required
def import_dataset():
    """导入 IBTrACS 或标准格式 CSV（UC-07：校验未通过则整批回滚）。"""
    if "file" not in request.files:
        raise ValidationError(
            "未接收到数据集文件。请使用 multipart/form-data，字段名为 file，"
            "可附加 source、source_version、strict 表单字段。"
        )
    storage = request.files["file"]
    raw = storage.read()
    if not raw:
        raise ValidationError("上传的数据集文件为空。")
    filename = storage.filename or "dataset.csv"
    source = (request.form.get("source") or "IBTrACS").strip()[:128]
    source_version = (request.form.get("source_version") or "").strip()[:32] or None
    strict = (request.form.get("strict") or "").strip().lower() in {"1", "true", "yes", "on"}

    record, stats = dataset_service.import_upload(
        current_user(),
        filename=filename,
        raw=raw,
        source=source,
        source_version=source_version,
        strict=strict,
    )
    return success({"import": record.to_dict(), "stats": stats.to_dict()}, status=201)


# ---------------------------------------------------------------------------
# 模型管理（FR-014）
# ---------------------------------------------------------------------------
@bp.get("/models")
@admin_required
def list_models():
    params = {key: value for key, value in request.args.items()}
    from flask import current_app

    page, page_size = parse_pagination(
        params, default=int(current_app.config.get("PAGE_SIZE_DEFAULT", 20)), maximum=100
    )
    items, total = model_service.list_models(page=page, page_size=page_size)
    return paginated(
        [item.to_dict() for item in items], page=page, page_size=page_size, total=total
    )


@bp.post("/models")
@admin_required
def upload_model():
    """上传模型包：权重(.pt) + 元数据(.json) + 可选标准化器(.json)。"""
    if "weights" not in request.files and "metadata" not in request.files:
        raise ValidationError(
            "至少需要上传权重文件（字段名 weights，.pt/.pth）或元数据（字段名 metadata，.json）。"
        )
    weights_file = request.files.get("weights")
    metadata_file = request.files.get("metadata")
    scaler_file = request.files.get("scaler")

    weights = weights_file.read() if weights_file else None
    metadata = metadata_file.read() if metadata_file else None
    scaler = scaler_file.read() if scaler_file else None

    name = (request.form.get("name") or "").strip()
    version = (request.form.get("version") or "").strip()
    if metadata and not name:
        import json

        try:
            parsed = json.loads(metadata.decode("utf-8-sig"))
            name = str(parsed.get("name") or "").strip()
            version = version or str(parsed.get("version") or "").strip()
        except (UnicodeDecodeError, ValueError):
            pass
    if not name:
        raise ValidationError("请提供模型名称（表单字段 name，或在元数据 JSON 中提供 name）。")
    if not version:
        raise ValidationError("请提供模型版本号（表单字段 version，或在元数据 JSON 中提供 version）。")

    activate = (request.form.get("activate") or "").strip().lower() in {"1", "true", "yes", "on"}
    outcome = model_service.upload_model(
        current_user(),
        name=name,
        version=version,
        weights=weights,
        weights_filename=weights_file.filename if weights_file else None,
        metadata=metadata,
        metadata_filename=metadata_file.filename if metadata_file else None,
        scaler=scaler,
        scaler_filename=scaler_file.filename if scaler_file else None,
        activate=activate,
    )
    return success(outcome.to_dict(), status=201)


@bp.put("/models/<int:model_id>/activate")
@admin_required
def activate_model(model_id: int):
    record = model_service.activate_model(current_user(), model_id)
    return success({"model": record.to_dict(), "runtime": get_registry().status()})


@bp.post("/models/baseline")
@admin_required
def ensure_baseline():
    """登记/启用内置持续性基线（用于尚未训练出 Transformer 的阶段）。"""
    record = model_service.ensure_baseline_version(current_user(), activate=True)
    get_registry().load_active()
    return success({"model": record.to_dict(), "runtime": get_registry().status()})


@bp.get("/models/runtime-status")
@admin_required
def runtime_status():
    registry = get_registry()
    return success(
        {
            "runtime": registry.status(),
            "versions": [
                item.to_dict()
                for item in ModelVersion.query.order_by(ModelVersion.created_at.desc()).limit(20)
            ],
        }
    )


# ---------------------------------------------------------------------------
# 操作日志与概览（FR-015）
# ---------------------------------------------------------------------------
@bp.get("/logs")
@admin_required
def list_logs():
    params = {key: value for key, value in request.args.items()}
    from flask import current_app

    page, page_size = parse_pagination(
        params, default=int(current_app.config.get("PAGE_SIZE_DEFAULT", 20)), maximum=200
    )
    query = AuditLog.query
    if params.get("action"):
        query = query.filter(AuditLog.action == params["action"])
    if params.get("actor"):
        query = query.filter(AuditLog.actor_username == params["actor"])
    if params.get("result"):
        query = query.filter(AuditLog.result == params["result"])
    total = query.count()
    items = (
        query.order_by(AuditLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return paginated(
        [item.to_dict() for item in items],
        page=page,
        page_size=page_size,
        total=total,
    )


@bp.get("/logs/actions")
@admin_required
def log_actions():
    rows = db.session.query(AuditLog.action).distinct().all()
    return success(
        {
            "actions": [
                {"value": row[0], "label": audit_service.ACTION_LABELS_CN.get(row[0], row[0])}
                for row in rows
            ]
        }
    )


@bp.get("/overview")
@admin_required
def overview():
    from app.models import DatasetImport, Typhoon, User

    user = current_user()
    return success(
        {
            "users": {
                "total": User.query.count(),
                "active": User.query.filter_by(status="active").count(),
                "admin": User.query.filter_by(role="admin").count(),
            },
            "data": {
                "typhoons": Typhoon.query.count(),
                "track_points": sum(
                    item.track_point_count or 0 for item in Typhoon.query.all()
                ),
                "imports": DatasetImport.query.count(),
            },
            "tasks": prediction_service.statistics(user),
            "model": get_registry().status(),
        }
    )


__all__ = ["bp"]
