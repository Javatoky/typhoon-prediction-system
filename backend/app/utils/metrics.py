"""轨迹误差评价指标（11.4）。

轨迹距离使用 Haversine 大圆距离（km），这是需求指定的唯一距离口径；
风速与气压使用 MAE；强度等级报告准确率。
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

EARTH_RADIUS_KM = 6371.0088
KEY_FORECAST_HOURS = (6, 12, 24, 48, 72)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两点间大圆距离（km）。"""
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    d_phi = phi2 - phi1
    d_lambda = math.radians(float(lon2) - float(lon1))
    a = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    a = min(1.0, max(0.0, a))
    return 2.0 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _pair_errors(
    predicted: Sequence[Sequence[float]], actual: Sequence[Sequence[float]]
) -> list[float]:
    errors: list[float] = []
    for index, (pred, obs) in enumerate(zip(predicted, actual)):
        pred_lat, pred_lon = float(pred[0]), float(pred[1])
        obs_lat, obs_lon = float(obs[0]), float(obs[1])
        errors.append(haversine_km(pred_lat, pred_lon, obs_lat, obs_lon))
    return errors


def distance_errors_by_hour(
    predicted: Sequence[Sequence[float]],
    actual: Sequence[Sequence[float]],
    forecast_hours: Sequence[int] | None = None,
    *,
    time_step_hours: int = 6,
) -> dict[int, float]:
    """按预测时效返回**该时效全部样本的平均**距离误差 {forecast_hour: km}。

    predicted/actual 的每行为 [lat, lon, ...]，顺序必须与时效一一对应。

    ⚠️ 实现要点：批量评估时同一 forecast_hour 会在 N 个样本中重复出现，
    必须按键聚合后求平均。曾用 `dict(zip(hours, errors))` 实现，同 key 被后者覆盖，
    返回值退化成"最后一个样本的误差"——这类错误不报错，只会让指标看起来变好。
    """
    hours = list(forecast_hours) if forecast_hours else [
        time_step_hours * (i + 1) for i in range(len(predicted))
    ]
    errors = _pair_errors(predicted, actual)
    if len(hours) != len(errors):
        raise ValueError(
            f"forecast_hours 长度({len(hours)}) 必须与样本数({len(errors)}) 一致。"
        )
    buckets: dict[int, list[float]] = {}
    for hour, error in zip(hours, errors):
        buckets.setdefault(int(hour), []).append(float(error))
    return {hour: sum(values) / len(values) for hour, values in sorted(buckets.items())}


def ade(predicted: Sequence[Sequence[float]], actual: Sequence[Sequence[float]]) -> float:
    """ADE：全部预测时效的平均大圆距离误差（km）。"""
    errors = _pair_errors(predicted, actual)
    if not errors:
        return float("nan")
    return sum(errors) / len(errors)


def fde(
    predicted: Sequence[Sequence[float]],
    actual: Sequence[Sequence[float]],
    *,
    final_hour: int = 72,
    time_step_hours: int = 6,
) -> float:
    """FDE：指定时效（默认 72 小时）的距离误差（km）。"""
    errors = _pair_errors(predicted, actual)
    if not errors:
        return float("nan")
    index = final_hour // time_step_hours - 1
    if index < 0 or index >= len(errors):
        return float("nan")
    return errors[index]


def mae(predicted: Iterable[float | None], actual: Iterable[float | None]) -> float:
    """平均绝对误差，自动忽略任一侧缺失的样本。"""
    pairs = [
        (float(p), float(a))
        for p, a in zip(predicted, actual)
        if p is not None and a is not None
    ]
    if not pairs:
        return float("nan")
    return sum(abs(p - a) for p, a in pairs) / len(pairs)


def rmse(predicted: Iterable[float | None], actual: Iterable[float | None]) -> float:
    pairs = [
        (float(p), float(a))
        for p, a in zip(predicted, actual)
        if p is not None and a is not None
    ]
    if not pairs:
        return float("nan")
    return math.sqrt(sum((p - a) ** 2 for p, a in pairs) / len(pairs))


def category_accuracy(
    predicted_codes: Iterable[str | None], actual_codes: Iterable[str | None]
) -> float:
    pairs = [
        (p, a)
        for p, a in zip(predicted_codes, actual_codes)
        if p is not None and a is not None
    ]
    if not pairs:
        return float("nan")
    return sum(1 for p, a in pairs if p == a) / len(pairs)


def summarize(
    predicted: Sequence[Sequence[float]],
    actual: Sequence[Sequence[float]],
    *,
    predicted_wind: Sequence[float | None] | None = None,
    actual_wind: Sequence[float | None] | None = None,
    predicted_pressure: Sequence[float | None] | None = None,
    actual_pressure: Sequence[float | None] | None = None,
    predicted_category: Sequence[str | None] | None = None,
    actual_category: Sequence[str | None] | None = None,
    forecast_hours: Sequence[int] | None = None,
    time_step_hours: int = 6,
    key_hours: Sequence[int] = KEY_FORECAST_HOURS,
) -> dict:
    """汇总一次评估的全部指标，供训练评估脚本与 Web 结果对比复用。"""
    by_hour = distance_errors_by_hour(
        predicted, actual, forecast_hours, time_step_hours=time_step_hours
    )
    result = {
        "distance_km": {str(hour): round(value, 2) for hour, value in by_hour.items()},
        "key_hours_km": {
            str(hour): round(by_hour[hour], 2) for hour in key_hours if hour in by_hour
        },
        "ade_km": round(ade(predicted, actual), 2),
        "fde_km": round(fde(predicted, actual, time_step_hours=time_step_hours), 2),
    }
    if predicted_wind is not None and actual_wind is not None:
        result["wind_mae_mps"] = round(mae(predicted_wind, actual_wind), 3)
    if predicted_pressure is not None and actual_pressure is not None:
        result["pressure_mae_hpa"] = round(mae(predicted_pressure, actual_pressure), 3)
    if predicted_category is not None and actual_category is not None:
        result["category_accuracy"] = round(category_accuracy(predicted_category, actual_category), 4)
    return result


def persistence_baseline(input_window: Sequence[Sequence[float]]) -> list[list[float]]:
    """持续性预测基线：用最后一个观测点重复填充全部时效（13.1 第2步）。

    对应 NFR-005：Transformer 的 24 小时平均误差目标不劣于该基线。
    """
    if not input_window:
        return []
    last = list(input_window[-1])
    return [list(last) for _ in input_window] + [
        list(last) for _ in range(max(0, 12 - len(input_window)))
    ]


__all__ = [
    "EARTH_RADIUS_KM",
    "KEY_FORECAST_HOURS",
    "haversine_km",
    "distance_errors_by_hour",
    "ade",
    "fde",
    "mae",
    "rmse",
    "category_accuracy",
    "summarize",
    "persistence_baseline",
]
