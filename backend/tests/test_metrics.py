"""评价指标与强度等级测试（11.4、FR-009）。"""

from __future__ import annotations

import math

import pytest

from app.utils.intensity import classify_intensity, threshold_table
from app.utils.metrics import (
    ade,
    category_accuracy,
    distance_errors_by_hour,
    fde,
    haversine_km,
    mae,
    persistence_baseline,
    summarize,
)
from app.utils.units import knots_to_mps, normalize_lon
from app.utils.timeutil import parse_datetime


def test_haversine_known_distance():
    # 赤道上相差 1 度经度 ≈ 111.19 km
    assert haversine_km(0.0, 0.0, 0.0, 1.0) == pytest.approx(111.19, abs=0.1)
    assert haversine_km(30.0, 120.0, 30.0, 120.0) == 0.0


def test_distance_errors_by_hour_and_aggregates():
    predicted = [[20.0, 120.0], [21.0, 121.0], [22.0, 122.0]]
    actual = [[20.0, 120.0], [20.0, 120.0], [20.0, 120.0]]
    errors = distance_errors_by_hour(predicted, actual, [6, 12, 24])
    assert errors[6] == 0.0
    assert errors[12] > 0
    assert ade(predicted, actual) == pytest.approx(sum(errors.values()) / 3, rel=1e-6)


def test_distance_errors_by_hour_averages_repeated_hours():
    """回归测试：同一 forecast_hour 出现多次时必须求平均，不能被字典覆盖。

    批量评估（N 个样本 × 12 个时效）会让每个时效重复 N 次；
    早期实现用 dict(zip(hours, errors))，结果退化成"最后一个样本的误差"，
    让 24 小时误差看起来远好于持续性基线——这类错误不报错，只会骗人。
    """
    predicted = [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 10.0]]
    actual = [[0.0, 1.0], [0.0, 0.0], [0.0, 0.0], [0.0, 10.0]]
    hours = [6, 12, 6, 12]
    errors = distance_errors_by_hour(predicted, actual, hours)
    one_degree = haversine_km(0.0, 0.0, 0.0, 1.0)
    # 6h：样本1 误差 = one_degree，样本2 误差 = 0 -> 平均应为一半
    assert errors[6] == pytest.approx(one_degree / 2, rel=1e-9)
    assert errors[12] == pytest.approx(0.0, abs=1e-9)


def test_distance_errors_by_hour_rejects_length_mismatch():
    with pytest.raises(ValueError):
        distance_errors_by_hour([[0.0, 0.0]], [[0.0, 0.0]], [6, 12])


def test_fde_uses_72_hour_index():
    predicted = [[10.0, 110.0]] * 1 + [[10.0, 110.0]] * 11
    actual = [[10.0, 110.0]] * 12
    assert fde(predicted, actual) == 0.0
    predicted[11] = [11.0, 110.0]
    assert fde(predicted, actual) == pytest.approx(haversine_km(11.0, 110.0, 10.0, 110.0), rel=1e-6)


def test_mae_ignores_missing_values():
    assert mae([1.0, None, 3.0], [2.0, 5.0, 1.0]) == pytest.approx(1.5)


def test_category_accuracy():
    assert category_accuracy(["TY", "TS"], ["TY", "TY"]) == 0.5
    assert math.isnan(category_accuracy(["TY"], [None]))


def test_summarize_builds_expected_keys():
    predicted = [[20.0, 120.0]] * 12
    actual = [[20.0, 120.0]] * 12
    summary = summarize(
        predicted,
        actual,
        predicted_wind=[30.0] * 12,
        actual_wind=[31.0] * 12,
        predicted_pressure=[990.0] * 12,
        actual_pressure=[992.0] * 12,
        predicted_category=["TS"] * 12,
        actual_category=["TS"] * 12,
    )
    assert summary["ade_km"] == 0.0
    assert summary["wind_mae_mps"] == pytest.approx(1.0)
    assert summary["pressure_mae_hpa"] == pytest.approx(2.0)
    assert summary["category_accuracy"] == 1.0
    assert set(summary["key_hours_km"]) <= {"6", "12", "24", "48", "72"}


def test_persistence_baseline_repeats_last_point():
    window = [[10.0, 110.0, 20.0, 1000.0], [11.0, 111.0, 25.0, 995.0]]
    result = persistence_baseline(window)
    assert len(result) == 12
    assert result[0] == [11.0, 111.0, 25.0, 995.0]
    assert result[-1] == [11.0, 111.0, 25.0, 995.0]


@pytest.mark.parametrize(
    "wind,code",
    [
        (10.7, "NA"),
        (10.8, "TD"),
        (17.1, "TD"),
        (17.2, "TS"),
        (24.5, "STS"),
        (32.7, "TY"),
        (41.5, "STY"),
        (51.0, "SuperTY"),
        (70.0, "SuperTY"),
        (None, "NA"),
    ],
)
def test_intensity_thresholds(wind, code):
    assert classify_intensity(wind)[0] == code


def test_threshold_table_covers_all_levels():
    table = threshold_table()
    assert len(table) == 6
    assert table[-1]["max_wind_mps"] is None


def test_unit_and_time_helpers():
    assert knots_to_mps(100) == pytest.approx(51.44, abs=0.01)
    assert normalize_lon(350.0) == pytest.approx(-10.0)
    assert normalize_lon(-180.0) == pytest.approx(-180.0)
    assert parse_datetime("2026-07-18T00:00:00Z") is not None
    assert parse_datetime("2026-07-18 06:00:00") is not None
    assert parse_datetime("nope") is None
