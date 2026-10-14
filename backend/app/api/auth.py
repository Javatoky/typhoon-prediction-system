"""认证接口（FR-001 / FR-002、7 外部接口需求）。"""

from __future__ import annotations

from flask import Blueprint, current_app

from app.api.deps import current_user, login_required
from app.core.params import get_json, require_str
from app.core.responses import success
from app.ml.registry import get_registry
from app.services import auth_service

bp = Blueprint("auth", __name__, url_prefix="/api/auth")


@bp.post("/login")
def login():
    payload = get_json()
    username = require_str(payload, "username", max_length=64, label="用户名")
    password = require_str(payload, "password", max_length=128, label="密码")
    user = auth_service.authenticate(username, password)
    return success(
        {
            "user": user.to_dict(),
            "model": get_registry().status(),
            "session_idle_minutes": current_app.config.get("SESSION_IDLE_MINUTES", 30),
        }
    )


@bp.post("/logout")
@login_required
def logout():
    auth_service.logout()
    return success({"message": "已退出登录。"})


@bp.get("/me")
@login_required
def me():
    user = current_user()
    return success({"user": user.to_dict()})


@bp.post("/change-password")
@login_required
def change_password():
    payload = get_json()
    current_password = require_str(payload, "current_password", max_length=128, label="当前密码")
    new_password = require_str(payload, "new_password", max_length=128, label="新密码")
    confirm = payload.get("confirm_password")
    if confirm is not None and str(confirm) != new_password:
        from app.core.errors import ValidationError

        raise ValidationError("两次输入的新密码不一致，请重新输入。")
    auth_service.change_password(
        current_user(), current_password=current_password, new_password=new_password
    )
    return success({"message": "密码修改成功，请使用新密码重新登录。"})


__all__ = ["bp"]
