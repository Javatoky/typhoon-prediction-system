"""请求参数解析与校验工具。

统一入口可保证错误提示风格一致（NFR-007：提示包含原因和修正方法）。
"""

from __future__ import annotations

import math
from typing import Any

from app.core.errors import ValidationError


def get_json() -> dict[str, Any]:
    """读取并校验 JSON 请求体。"""
    from flask import request

    if not request.is_json:
        raise ValidationError("请求体必须是 JSON（Content-Type: application/json）。")
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValidationError("请求体必须是 JSON 对象，例如 {\"username\": \"...\"}。")
    return payload


def require_str(
    payload: dict[str, Any],
    key: str,
    *,
    min_length: int = 1,
    max_length: int = 255,
    label: str | None = None,
) -> str:
    label = label or key
    raw = payload.get(key)
    if raw is None:
        raise ValidationError(f"缺少必填字段 {label}。")
    if not isinstance(raw, str):
        raise ValidationError(f"字段 {label} 必须是字符串。")
    value = raw.strip()
    if len(value) < min_length:
        raise ValidationError(f"字段 {label} 不能为空。")
    if len(value) > max_length:
        raise ValidationError(f"字段 {label} 长度不能超过 {max_length} 个字符。")
    return value


def optional_int(
    payload: dict[str, Any],
    key: str,
    *,
    default: int | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
    label: str | None = None,
) -> int | None:
    label = label or key
    raw = payload.get(key)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValidationError(f"字段 {label} 必须是整数。") from None
    if minimum is not None and value < minimum:
        raise ValidationError(f"字段 {label} 不能小于 {minimum}。")
    if maximum is not None and value > maximum:
        raise ValidationError(f"字段 {label} 不能大于 {maximum}。")
    return value


def optional_bool(payload: dict[str, Any], key: str, *, default: bool = False) -> bool:
    raw = payload.get(key)
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(raw)


def optional_choice(
    payload: dict[str, Any],
    key: str,
    choices: list[str] | tuple[str, ...],
    *,
    default: str | None = None,
    label: str | None = None,
) -> str | None:
    label = label or key
    raw = payload.get(key)
    if raw is None or raw == "":
        return default
    value = str(raw)
    if value not in choices:
        raise ValidationError(f"字段 {label} 只能是 {'、'.join(choices)} 之一，当前为 {value}。")
    return value


def parse_pagination(payload: dict[str, Any], *, default: int = 20, maximum: int = 100) -> tuple[int, int]:
    page = optional_int(payload, "page", default=1, minimum=1) or 1
    page_size = optional_int(payload, "page_size", default=default, minimum=1, maximum=maximum) or default
    return page, page_size


def coerce_float(value: Any) -> float | None:
    """把任意来源的值安全转为 float，失败或非有限值返回 None。"""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if text == "" or text.lower() in {"nan", "none", "null", "na", "-"}:
            return None
        value = text
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


__all__ = [
    "get_json",
    "require_str",
    "optional_int",
    "optional_bool",
    "optional_choice",
    "parse_pagination",
    "coerce_float",
]
