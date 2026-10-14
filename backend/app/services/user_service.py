"""用户管理服务（FR-012、UC-06）。"""

from __future__ import annotations

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.security import (
    generate_token,
    hash_password,
    utcnow,
    validate_password_strength,
)
from app.extensions import db
from app.models import User
from app.services import audit_service

USERNAME_MIN = 3
USERNAME_MAX = 32


def list_users(*, keyword: str | None = None, page: int = 1, page_size: int = 20):
    query = User.query
    if keyword:
        pattern = f"%{keyword.strip()}%"
        query = query.filter(User.username.ilike(pattern) | User.display_name.ilike(pattern))
    total = query.count()
    items = (
        query.order_by(User.role.asc(), User.username.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return items, total


def get_user(user_id: int) -> User:
    user = db.session.get(User, user_id)
    if user is None:
        raise NotFoundError(f"未找到编号为 {user_id} 的用户。")
    return user


def _validate_username(username: str) -> str:
    value = username.strip()
    if len(value) < USERNAME_MIN or len(value) > USERNAME_MAX:
        raise ValidationError(f"用户名长度必须在 {USERNAME_MIN}~{USERNAME_MAX} 个字符之间。")
    if not all(ch.isalnum() or ch in "._-" for ch in value):
        raise ValidationError("用户名只能包含字母、数字、下划线、点和短横线。")
    return value


def create_user(
    actor: User,
    *,
    username: str,
    password: str | None = None,
    display_name: str = "",
    role: str = "user",
) -> tuple[User, str | None]:
    """创建用户。未提供密码时自动生成一次性初始密码并返回给管理员（不写日志）。"""
    name = _validate_username(username)
    if User.query.filter(func_lower(name)).first() is not None:
        raise ConflictError(f"用户名 {name} 已存在，请更换后重试。")
    if role not in {"user", "admin"}:
        raise ValidationError("角色只能是 user 或 admin。")

    initial_password = password or f"Ty{generate_token(4)}@1"
    validate_password_strength(initial_password, username=name)

    user = User(
        username=name,
        display_name=(display_name or name).strip()[:64],
        password_hash=hash_password(initial_password),
        role=role,
        status="active",
        password_changed_at=utcnow(),
    )
    db.session.add(user)
    audit_service.record(
        audit_service.ACTION_USER_CREATE,
        actor=actor,
        target_type="user",
        target_id=name,
        detail=f"角色 {role}",
    )
    db.session.commit()
    return user, (None if password else initial_password)


def func_lower(value: str):
    """SQLite 下的不区分大小写比较。"""
    from sqlalchemy import func

    return func.lower(User.username) == value.lower()


def update_user(
    actor: User,
    user_id: int,
    *,
    status: str | None = None,
    role: str | None = None,
    display_name: str | None = None,
) -> User:
    user = get_user(user_id)

    if status is not None:
        if status not in {"active", "disabled", "locked"}:
            raise ValidationError("状态只能是 active、disabled 或 locked。")
        if status == "disabled" and _active_admin_count(exclude_id=user.id) == 0:
            raise ValidationError("系统必须保留至少一个可用的管理员账号，无法禁用该账号。")
        if status == "disabled" and user.id == actor.id:
            raise ValidationError("不能禁用当前登录的管理员账号。")
        if user.status != status:
            user.status = status
            if status == "active":
                user.failed_login_count = 0
                user.locked_until = None

    if role is not None:
        if role not in {"user", "admin"}:
            raise ValidationError("角色只能是 user 或 admin。")
        if role == "user" and user.is_admin and _active_admin_count(exclude_id=user.id) == 0:
            raise ValidationError("系统必须保留至少一个可用的管理员账号，无法降级该账号。")
        user.role = role

    if display_name is not None:
        user.display_name = display_name.strip()[:64]

    audit_service.record(
        audit_service.ACTION_USER_UPDATE,
        actor=actor,
        target_type="user",
        target_id=user.id,
        detail=f"status={user.status} role={user.role}",
    )
    db.session.commit()
    return user


def reset_password(actor: User, user_id: int, *, new_password: str | None = None) -> tuple[User, str]:
    """重置密码。未指定新密码时随机生成并返回一次。"""
    user = get_user(user_id)
    password = new_password or f"Ty{generate_token(4)}@1"
    validate_password_strength(password, username=user.username)
    user.password_hash = hash_password(password)
    user.password_changed_at = utcnow()
    user.failed_login_count = 0
    user.locked_until = None
    if user.status == "locked":
        user.status = "active"
    audit_service.record(
        audit_service.ACTION_USER_RESET_PASSWORD,
        actor=actor,
        target_type="user",
        target_id=user.id,
        detail="管理员重置密码",
    )
    db.session.commit()
    return user, password


def _active_admin_count(*, exclude_id: int | None = None) -> int:
    query = User.query.filter(User.role == "admin", User.status == "active")
    if exclude_id is not None:
        query = query.filter(User.id != exclude_id)
    return query.count()


def ensure_admin(username: str, password: str, *, display_name: str = "系统管理员") -> tuple[User, bool]:
    """确保存在一个管理员账号（初始化脚本使用）。返回 (用户, 是否新建)。"""
    existing = User.query.filter(func_lower(username)).first()
    if existing is not None:
        if existing.role != "admin":
            existing.role = "admin"
            db.session.commit()
        return existing, False
    validate_password_strength(password, username=username)
    user = User(
        username=username,
        display_name=display_name,
        password_hash=hash_password(password),
        role="admin",
        status="active",
        password_changed_at=utcnow(),
    )
    db.session.add(user)
    db.session.commit()
    return user, True


__all__ = [
    "list_users",
    "get_user",
    "create_user",
    "update_user",
    "reset_password",
    "ensure_admin",
]
