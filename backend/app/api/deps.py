"""控制器共用装饰器：登录校验、角色校验、会话活动时间。

对应 7.1：无权限功能不显示，且接口仍须拒绝。
"""

from __future__ import annotations

from functools import wraps

from flask import g, session

from app.core.errors import AuthError, ForbiddenError
from app.extensions import db
from app.models import User

SESSION_USER_KEY = "user_id"
SESSION_LOGIN_AT = "login_at"
SESSION_LAST_SEEN = "last_seen"


def _coerce_user_id(raw):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def load_current_user() -> User | None:
    """读取会话中的用户并缓存到 g。"""
    if "current_user" in g:
        return g.current_user
    user = None
    user_id = _coerce_user_id(session.get(SESSION_USER_KEY))
    if user_id is not None:
        user = db.session.get(User, user_id)
        if user is not None and user.status == "disabled":
            session.clear()
            user = None
    g.current_user = user
    return user


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = load_current_user()
        if user is None:
            raise AuthError("登录状态已失效，请重新登录。")
        return view(*args, **kwargs)

    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        user = load_current_user()
        if user is None:
            raise AuthError("登录状态已失效，请重新登录。")
        if not user.is_admin:
            raise ForbiddenError("该操作仅限系统管理员，请使用管理员账号登录。")
        return view(*args, **kwargs)

    return wrapper


def current_user() -> User:
    user = load_current_user()
    if user is None:
        raise AuthError("登录状态已失效，请重新登录。")
    return user


__all__ = [
    "SESSION_USER_KEY",
    "SESSION_LOGIN_AT",
    "SESSION_LAST_SEEN",
    "load_current_user",
    "login_required",
    "admin_required",
    "current_user",
]
