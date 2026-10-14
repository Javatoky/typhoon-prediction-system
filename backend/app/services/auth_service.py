"""认证服务（FR-001 / FR-002、UC-01、NFR-008）。"""

from __future__ import annotations

from flask import session

from app.core.errors import AuthError, ValidationError
from app.core.security import (
    DISABLED_MESSAGE,
    LOCKED_MESSAGE_TEMPLATE,
    UNIFIED_CREDENTIAL_MESSAGE,
    hash_password,
    is_locked,
    register_failed_login,
    register_successful_login,
    remaining_lock_minutes,
    utcnow,
    validate_password_strength,
    verify_password,
)
from app.extensions import db
from app.models import LoginAttempt, User
from app.services import audit_service


def _record_attempt(username: str, *, success: bool, reason: str | None = None) -> None:
    db.session.add(
        LoginAttempt(
            username=username[:64],
            ip_address=audit_service.client_ip(),
            success=success,
            reason=reason,
            attempted_at=utcnow(),
        )
    )


def authenticate(username: str, password: str) -> User:
    """校验凭据并建立会话。

    安全要点：
    - “用户不存在”和“密码错误”返回同一提示（6.2）。
    - 连续失败达到上限后锁定，禁用账号直接拒绝。
    """
    from flask import current_app

    max_failures = int(current_app.config.get("LOGIN_MAX_FAILURES", 5))
    lock_minutes = int(current_app.config.get("LOGIN_LOCK_MINUTES", 15))

    user = User.query.filter_by(username=username).first()

    if user is None:
        _record_attempt(username, success=False, reason="user_not_found")
        audit_service.record(
            audit_service.ACTION_LOGIN_FAILED,
            target_type="user",
            target_id=username,
            result="failure",
            detail=f"用户名不存在：{username}",
        )
        db.session.commit()
        raise AuthError(UNIFIED_CREDENTIAL_MESSAGE, code="invalid_credentials")

    if user.status == "disabled":
        _record_attempt(username, success=False, reason="disabled")
        audit_service.record(
            audit_service.ACTION_LOGIN_FAILED,
            actor=user,
            target_type="user",
            target_id=user.id,
            result="failure",
            detail="账号已禁用",
        )
        db.session.commit()
        raise AuthError(DISABLED_MESSAGE, code="account_disabled")

    if is_locked(user):
        minutes = remaining_lock_minutes(user)
        _record_attempt(username, success=False, reason="locked")
        audit_service.record(
            audit_service.ACTION_LOGIN_FAILED,
            actor=user,
            target_type="user",
            target_id=user.id,
            result="failure",
            detail=f"账号锁定中，剩余 {minutes} 分钟",
        )
        db.session.commit()
        raise AuthError(
            LOCKED_MESSAGE_TEMPLATE.format(minutes=minutes), code="account_locked"
        )

    if not verify_password(user.password_hash, password):
        register_failed_login(user, max_failures=max_failures, lock_minutes=lock_minutes)
        _record_attempt(username, success=False, reason="bad_password")
        remaining = max(0, max_failures - int(user.failed_login_count or 0))
        audit_service.record(
            audit_service.ACTION_LOGIN_FAILED,
            actor=user,
            target_type="user",
            target_id=user.id,
            result="failure",
            detail=f"密码错误，剩余尝试次数 {remaining}",
        )
        db.session.commit()
        if user.status == "locked":
            raise AuthError(
                LOCKED_MESSAGE_TEMPLATE.format(minutes=lock_minutes), code="account_locked"
            )
        raise AuthError(UNIFIED_CREDENTIAL_MESSAGE, code="invalid_credentials")

    register_successful_login(user)
    _record_attempt(username, success=True)
    audit_service.record(  # 自身会 commit，一并提交上面的记录
        audit_service.ACTION_LOGIN,
        actor=user,
        target_type="user",
        target_id=user.id,
        detail=f"角色 {user.role}",
    )
    db.session.commit()
    establish_session(user)
    return user


def establish_session(user: User) -> None:
    from app.api.deps import SESSION_LAST_SEEN, SESSION_LOGIN_AT, SESSION_USER_KEY

    session.clear()
    session.permanent = True
    session[SESSION_USER_KEY] = user.id
    session[SESSION_LOGIN_AT] = utcnow().isoformat()
    session[SESSION_LAST_SEEN] = utcnow().isoformat()


def logout() -> None:
    from app.api.deps import load_current_user

    user = load_current_user()
    if user is not None:
        audit_service.record(
            audit_service.ACTION_LOGOUT,
            actor=user,
            target_type="user",
            target_id=user.id,
            detail="用户主动退出",
        )
    session.clear()


def change_password(user: User, *, current_password: str, new_password: str) -> None:
    if not verify_password(user.password_hash, current_password):
        raise ValidationError("当前密码不正确，请重新输入。")
    if current_password == new_password:
        raise ValidationError("新密码不能与当前密码相同。")
    validate_password_strength(new_password, username=user.username)
    user.password_hash = hash_password(new_password)
    user.password_changed_at = utcnow()
    user.failed_login_count = 0
    user.locked_until = None
    if user.status == "locked":
        user.status = "active"
    audit_service.record(
        audit_service.ACTION_PASSWORD_CHANGE,
        actor=user,
        target_type="user",
        target_id=user.id,
        detail="用户自助修改密码",
    )
    db.session.commit()


__all__ = ["authenticate", "establish_session", "logout", "change_password"]
