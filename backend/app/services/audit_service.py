"""审计日志服务（FR-015、6.2）。

约束：
- 只记录操作者、时间、动作、目标与结果；密码、原始堆栈等敏感内容禁止写入。
- 写入失败不得影响主业务（审计是旁路），但会在应用日志中留下告警。
"""

from __future__ import annotations

import logging

from app.core.logging_setup import scrub

logger = logging.getLogger(__name__)

# 动作常量：便于前端筛选与管理页展示
ACTION_LOGIN = "auth.login"
ACTION_LOGIN_FAILED = "auth.login_failed"
ACTION_LOGOUT = "auth.logout"
ACTION_PASSWORD_CHANGE = "auth.change_password"
ACTION_USER_CREATE = "admin.user_create"
ACTION_USER_UPDATE = "admin.user_update"
ACTION_USER_RESET_PASSWORD = "admin.user_reset_password"
ACTION_DATASET_IMPORT = "admin.dataset_import"
ACTION_MODEL_UPLOAD = "admin.model_upload"
ACTION_MODEL_ACTIVATE = "admin.model_activate"
ACTION_MODEL_REGISTER = "admin.model_register"
ACTION_PREDICTION_CREATE = "prediction.create"
ACTION_UPLOAD_TRACK = "upload.track"

ACTION_LABELS_CN = {
    ACTION_LOGIN: "登录成功",
    ACTION_LOGIN_FAILED: "登录失败",
    ACTION_LOGOUT: "退出登录",
    ACTION_PASSWORD_CHANGE: "修改密码",
    ACTION_USER_CREATE: "创建用户",
    ACTION_USER_UPDATE: "修改用户状态",
    ACTION_USER_RESET_PASSWORD: "重置用户密码",
    ACTION_DATASET_IMPORT: "导入数据集",
    ACTION_MODEL_UPLOAD: "上传模型",
    ACTION_MODEL_ACTIVATE: "启用模型",
    ACTION_MODEL_REGISTER: "登记内置基线模型",
    ACTION_PREDICTION_CREATE: "创建预测任务",
    ACTION_UPLOAD_TRACK: "上传轨迹数据",
}


def client_ip() -> str | None:
    from flask import request

    try:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64]
        return (request.remote_addr or "")[:64] or None
    except RuntimeError:
        return None


def record(
    action: str,
    *,
    actor=None,
    target_type: str | None = None,
    target_id=None,
    result: str = "success",
    detail: str | None = None,
    commit: bool = False,
) -> None:
    """写入一条审计日志。

    默认只 add 不 commit，由调用方与主业务在同一次事务中提交，避免出现
    “业务失败但审计成功”或反之的不一致；独立使用时可传 commit=True。
    """
    from app.core.security import sanitize_text
    from app.extensions import db
    from app.models import AuditLog

    try:
        entry = AuditLog(
            actor_id=getattr(actor, "id", None),
            actor_username=getattr(actor, "username", None),
            action=action,
            target_type=target_type,
            target_id=None if target_id is None else str(target_id)[:64],
            result=result,
            detail=sanitize_text(detail, max_length=480) if detail else None,
            ip_address=client_ip(),
        )
        db.session.add(entry)
        if commit:
            db.session.commit()
    except Exception as exc:  # pragma: no cover
        logger.warning("写入审计日志失败（不影响主流程）：%s detail=%s", exc, scrub({"detail": detail or ""}))
        try:
            db.session.rollback()
        except Exception:
            pass


__all__ = [
    "ACTION_LOGIN",
    "ACTION_LOGIN_FAILED",
    "ACTION_LOGOUT",
    "ACTION_PASSWORD_CHANGE",
    "ACTION_USER_CREATE",
    "ACTION_USER_UPDATE",
    "ACTION_USER_RESET_PASSWORD",
    "ACTION_DATASET_IMPORT",
    "ACTION_MODEL_UPLOAD",
    "ACTION_MODEL_ACTIVATE",
    "ACTION_MODEL_REGISTER",
    "ACTION_PREDICTION_CREATE",
    "ACTION_UPLOAD_TRACK",
    "ACTION_LABELS_CN",
    "client_ip",
    "record",
]
