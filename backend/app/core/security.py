"""安全工具：密码哈希、登录失败锁定（UC-01 扩展流程、NFR-008）。

原则（6.2）：
- 密码只保存哈希，绝不落日志、绝不返回给前端。
- 登录失败提示统一文案，不暴露账号是否存在。
"""

from __future__ import annotations

import secrets
import unicodedata
from datetime import datetime, timedelta, timezone

from werkzeug.security import check_password_hash, generate_password_hash

from app.core.errors import ValidationError

# 统一登录失败提示（不区分“用户不存在”和“密码错误”）
UNIFIED_CREDENTIAL_MESSAGE = "用户名或密码不正确，请检查后重试。"
LOCKED_MESSAGE_TEMPLATE = "账号因连续登录失败已被锁定，请于 {minutes} 分钟后重试或联系管理员。"
DISABLED_MESSAGE = "账号已被禁用，请联系管理员。"


def utcnow() -> datetime:
    """返回不带时区信息的 UTC 时间（SQLite 存储口径统一）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_password(password: str) -> str:
    from flask import current_app

    method = current_app.config.get("PASSWORD_HASH_METHOD", "pbkdf2:sha256:600000")
    return generate_password_hash(password, method=method)


def verify_password(password_hash: str, password: str) -> bool:
    if not password_hash:
        return False
    try:
        return check_password_hash(password_hash, password)
    except (ValueError, TypeError):
        return False


def validate_password_strength(password: str, *, username: str | None = None) -> None:
    """密码策略校验。失败时抛出 ValidationError，并给出可操作提示。"""
    from flask import current_app

    min_length = int(current_app.config.get("PASSWORD_MIN_LENGTH", 8))
    if len(password) < min_length:
        raise ValidationError(f"密码长度至少 {min_length} 位，请重新设置。")
    if len(password) > 128:
        raise ValidationError("密码长度不能超过 128 位。")
    categories = 0
    if any(ch.islower() for ch in password):
        categories += 1
    if any(ch.isupper() for ch in password):
        categories += 1
    if any(ch.isdigit() for ch in password):
        categories += 1
    if any(not ch.isalnum() for ch in password):
        categories += 1
    if categories < 2:
        raise ValidationError("密码需至少包含大写字母、小写字母、数字、符号中的两类。")
    if username and username.lower() in password.lower():
        raise ValidationError("密码不能包含用户名。")
    if any(ch.isspace() for ch in password):
        raise ValidationError("密码不能包含空白字符。")


def is_locked(user) -> bool:
    """判断账号是否处于锁定窗口内。"""
    if user is None or user.locked_until is None:
        return False
    return user.locked_until > utcnow()


def remaining_lock_minutes(user) -> int:
    if user is None or user.locked_until is None:
        return 0
    delta = user.locked_until - utcnow()
    return max(1, int(delta.total_seconds() // 60) + (1 if delta.total_seconds() % 60 else 0))


def register_failed_login(user, *, max_failures: int, lock_minutes: int) -> None:
    """累计失败次数，达到上限则锁定。调用方负责提交事务。"""
    if user is None:
        return
    user.failed_login_count = int(user.failed_login_count or 0) + 1
    if user.failed_login_count >= max_failures:
        user.locked_until = utcnow() + timedelta(minutes=lock_minutes)
        user.failed_login_count = 0
        user.status = "locked"


def register_successful_login(user) -> None:
    """成功后清零失败计数并解除锁定。"""
    if user is None:
        return
    user.failed_login_count = 0
    user.locked_until = None
    if user.status == "locked":
        user.status = "active"
    user.last_login_at = utcnow()


def generate_token(length: int = 32) -> str:
    """生成随机文件名片段，用于上传文件落盘（6.2 禁止路径穿越）。"""
    return secrets.token_hex(length)


def normalize_stored_filename(original_name: str) -> str:
    """从用户提供的文件名中只保留安全的后缀，其余全部丢弃。"""
    safe_suffix = ""
    if "." in original_name:
        candidate = original_name.rsplit(".", 1)[-1]
        candidate = "".join(ch for ch in candidate if ch.isalnum())
        if candidate:
            safe_suffix = "." + candidate.lower()
    return safe_suffix


def sanitize_text(text: str, *, max_length: int = 500) -> str:
    """清理用于展示/存储的文本：去除控制字符并截断。"""
    if not text:
        return ""
    cleaned = "".join(ch for ch in str(text) if unicodedata.category(ch)[0] != "C" or ch in "\n\t")
    cleaned = cleaned.strip()
    if len(cleaned) > max_length:
        cleaned = cleaned[: max_length - 1] + "…"
    return cleaned


__all__ = [
    "UNIFIED_CREDENTIAL_MESSAGE",
    "LOCKED_MESSAGE_TEMPLATE",
    "DISABLED_MESSAGE",
    "utcnow",
    "hash_password",
    "verify_password",
    "validate_password_strength",
    "is_locked",
    "remaining_lock_minutes",
    "register_failed_login",
    "register_successful_login",
    "generate_token",
    "normalize_stored_filename",
    "sanitize_text",
]
