"""时间工具：统一 UTC 解析、6 小时步长校验与展示口径（DQ-01）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.params import coerce_float  # noqa: F401  (便于调用方从同一模块导入)

TIME_FORMATS = (
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d",
    "%Y%m%d%H",
    "%Y%m%d",
)


def ensure_utc(value: datetime) -> datetime:
    """把带时区的时间转为 UTC naive；naive 时间视为已经是 UTC。"""
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def parse_datetime(raw) -> datetime | None:
    """宽松解析时间字符串，失败返回 None。"""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return ensure_utc(raw)
    text = str(raw).strip()
    if text == "" or text.lower() in {"nan", "none", "null", "na"}:
        return None
    # 统一 'Z' 与 '+00:00' 结尾
    normalized = text.replace("Z", "+00:00") if text.endswith("Z") else text
    try:
        return ensure_utc(datetime.fromisoformat(normalized))
    except ValueError:
        pass
    for fmt in TIME_FORMATS:
        try:
            return ensure_utc(datetime.strptime(text, fmt))
        except ValueError:
            continue
    return None


def format_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return ensure_utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def hours_between(later: datetime, earlier: datetime) -> float:
    return (ensure_utc(later) - ensure_utc(earlier)).total_seconds() / 3600.0


def is_exact_step(later: datetime, earlier: datetime, step_hours: int) -> bool:
    """判断两点间隔是否**恰好**为一个时间步长（DQ-01：相邻时间间隔必须为 6 小时）。

    注意：这里不能写成“6 小时的整数倍”。否则 24 小时间隔也会被判定为“连续”，
    导致跨越大段缺测的观测点被拼进模型输入窗口（DQ-04 的核心约束）。
    """
    return abs(hours_between(later, earlier) - float(step_hours)) < 1e-6


def is_step_multiple(later: datetime, earlier: datetime, step_hours: int) -> bool:
    """兼容保留：间隔是否恰为一个时间步长（内部转为 is_exact_step）。"""
    return is_exact_step(later, earlier, step_hours)


def add_hours(value: datetime, hours: int) -> datetime:
    return ensure_utc(value) + timedelta(hours=hours)


__all__ = [
    "TIME_FORMATS",
    "ensure_utc",
    "parse_datetime",
    "format_iso",
    "hours_between",
    "is_step_multiple",
    "is_exact_step",
    "add_hours",
]
