"""强度等级映射（3.2 派生输出、11.1）。

依据：GB/T 19201-2006《热带气旋等级》，按最大持续风速（m/s）划分。
属于教学展示结果，报告中必须注明等级依据与风速平均时段口径（2.4 假设与依赖）。

阈值表集中在 config.Config.INTENSITY_LEVELS，本模块只负责映射逻辑。
"""

from __future__ import annotations

from typing import Iterable

DEFAULT_LEVELS: list[dict] = [
    {"code": "TD", "name_cn": "热带低压", "min_wind_mps": 10.8},
    {"code": "TS", "name_cn": "热带风暴", "min_wind_mps": 17.2},
    {"code": "STS", "name_cn": "强热带风暴", "min_wind_mps": 24.5},
    {"code": "TY", "name_cn": "台风", "min_wind_mps": 32.7},
    {"code": "STY", "name_cn": "强台风", "min_wind_mps": 41.5},
    {"code": "SuperTY", "name_cn": "超强台风", "min_wind_mps": 51.0},
]

UNKNOWN_CODE = "NA"
UNKNOWN_NAME = "未知"


def _levels() -> list[dict]:
    try:
        from flask import current_app

        levels = current_app.config.get("INTENSITY_LEVELS")
        if levels:
            return list(levels)
    except RuntimeError:  # 无应用上下文（离线脚本中直接调用）
        pass
    return DEFAULT_LEVELS


def _sorted_levels(levels: Iterable[dict] | None = None) -> list[dict]:
    data = list(levels) if levels is not None else _levels()
    return sorted(data, key=lambda item: float(item["min_wind_mps"]))


def classify_intensity(wind_mps: float | None, levels: Iterable[dict] | None = None) -> tuple[str, str]:
    """把风速映射为 (等级代码, 中文名)。

    低于热带低压下限（10.8 m/s）时返回 ("NA", "未知")，前端据此显示为“未定级”。
    """
    if wind_mps is None:
        return UNKNOWN_CODE, UNKNOWN_NAME
    wind = float(wind_mps)
    result = (UNKNOWN_CODE, UNKNOWN_NAME)
    for level in _sorted_levels(levels):
        if wind >= float(level["min_wind_mps"]):
            result = (str(level["code"]), str(level["name_cn"]))
        else:
            break
    return result


def intensity_name(code: str | None, levels: Iterable[dict] | None = None) -> str:
    if not code:
        return UNKNOWN_NAME
    for level in _sorted_levels(levels):
        if str(level["code"]) == code:
            return str(level["name_cn"])
    return UNKNOWN_NAME


def threshold_table(levels: Iterable[dict] | None = None) -> list[dict]:
    """导出阈值表，供前端图例与报告附录使用。"""
    table = []
    ordered = _sorted_levels(levels)
    for index, level in enumerate(ordered):
        upper = ordered[index + 1]["min_wind_mps"] if index + 1 < len(ordered) else None
        table.append(
            {
                "code": level["code"],
                "name_cn": level["name_cn"],
                "min_wind_mps": level["min_wind_mps"],
                "max_wind_mps": upper,
            }
        )
    return table


__all__ = [
    "DEFAULT_LEVELS",
    "UNKNOWN_CODE",
    "UNKNOWN_NAME",
    "classify_intensity",
    "intensity_name",
    "threshold_table",
]
