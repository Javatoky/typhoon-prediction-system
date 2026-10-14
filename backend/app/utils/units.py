"""单位与坐标换算（3.3 数据质量规则、11.3 数据预处理）。

IBTrACS v4 原始口径：
- 风速列（USA_WIND / WMO_WIND 等）单位为 knot。
- 气压列（USA_PRES / WMO_PRES）单位为 hPa（毫巴）。
- 经度可能为 0~360，也可能为 -180~180，取决于导出方式。

本项目内部统一口径（3.4 数据字典）：
    lat ∈ [-90, 90]，lon ∈ [-180, 180]，max_wind_mps（m/s），central_pressure_hpa（hPa）
"""

from __future__ import annotations

import math

KNOT_TO_MPS = 0.514444
KT_TO_MPS = KNOT_TO_MPS


def knots_to_mps(knots: float | None) -> float | None:
    if knots is None:
        return None
    return round(float(knots) * KNOT_TO_MPS, 2)


def mps_to_knots(mps: float | None) -> float | None:
    if mps is None:
        return None
    return round(float(mps) / KNOT_TO_MPS, 2)


def normalize_lon(lon: float | None) -> float | None:
    """把任意经度统一到 [-180, 180)。"""
    if lon is None:
        return None
    value = float(lon)
    if math.isnan(value) or math.isinf(value):
        return None
    value = ((value + 180.0) % 360.0 + 360.0) % 360.0 - 180.0
    # 避免 -180 与 180 混用造成的重复点
    if value == -180.0 and lon > 0:
        value = 180.0
    return round(value, 3)


def looks_like_0_360(lons: list[float | None]) -> bool:
    """启发式判断一批经度是否使用 0~360 口径（全部非负且存在 >180 的值）。"""
    values = [v for v in lons if v is not None]
    if not values:
        return False
    return all(v >= 0 for v in values) and any(v > 180.0 for v in values)


def clamp_lat(lat: float) -> float:
    return max(-90.0, min(90.0, float(lat)))


def normalize_lat(lat: float | None) -> float | None:
    if lat is None:
        return None
    value = float(lat)
    if math.isnan(value) or math.isinf(value):
        return None
    return round(clamp_lat(value), 3)


def in_focus_area(lat: float | None, lon: float | None, area: dict) -> bool:
    """判断是否落在 3.1 定义的关注区（105°E-135°E、5°N-45°N）。"""
    if lat is None or lon is None:
        return False
    lon_norm = normalize_lon(lon)
    if lon_norm is None:
        return False
    return (
        area["lat_min"] <= lat <= area["lat_max"] and area["lon_min"] <= lon_norm <= area["lon_max"]
    )


def is_in_range(value: float | None, bounds: tuple[float, float]) -> bool:
    if value is None:
        return False
    return bounds[0] <= value <= bounds[1]


__all__ = [
    "KNOT_TO_MPS",
    "KT_TO_MPS",
    "knots_to_mps",
    "mps_to_knots",
    "normalize_lon",
    "normalize_lat",
    "looks_like_0_360",
    "clamp_lat",
    "in_focus_area",
    "is_in_range",
]
