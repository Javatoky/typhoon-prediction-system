"""预处理器、基线推理与 Transformer 前向测试（11.3 / 11.6）。"""

from __future__ import annotations

import json

import pytest

from app.ml import schema
from app.ml.forecaster import KIND_BASELINE, build_baseline_forecaster, torch_available
from app.ml.preprocess import FeatureScaler, correct_physical_range
from app.ml.validator import ValidatedPoint
from datetime import datetime, timedelta


def _window():
    return [
        [18.2, 126.5, 28.0, 985.0],
        [18.8, 125.7, 30.5, 980.0],
        [19.5, 124.8, 33.0, 975.0],
        [20.3, 123.9, 35.0, 970.0],
        [21.1, 123.0, 37.0, 965.0],
    ]


def test_scaler_roundtrip_and_persistence(tmp_path):
    rows = _window() + [[22.0, 122.0, 40.0, 960.0]]
    scaler = FeatureScaler(
        mean=[sum(row[i] for row in rows) / len(rows) for i in range(4)],
        std=[1.0, 2.0, 3.0, 4.0],
    )
    transformed = scaler.transform_window(rows)
    restored = scaler.inverse_transform(transformed)
    for original, back in zip(rows, restored):
        assert back == pytest.approx(original, rel=1e-9)

    path = scaler.save(tmp_path / "scaler.json")
    loaded = FeatureScaler.load(path)
    assert loaded.mean == scaler.mean
    assert loaded.std == scaler.std
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["feature_columns"] == list(schema.FEATURE_COLUMNS)


def test_scaler_rejects_wrong_feature_order():
    bad = {
        "feature_columns": ["lon", "lat", "max_wind_mps", "central_pressure_hpa"],
        "mean": [0, 0, 0, 0],
        "std": [1, 1, 1, 1],
    }
    with pytest.raises(ValueError, match="特征顺序"):
        FeatureScaler.from_dict(bad)


def test_fixed_range_scaler_marks_flag():
    scaler = FeatureScaler.from_fixed_range()
    assert scaler.fixed_range is True
    assert len(scaler.mean) == schema.FEATURE_DIM


def test_baseline_forecaster_output_shape_and_physics():
    forecaster = build_baseline_forecaster()
    result = forecaster.predict(_window())
    assert len(result.points) == 12
    assert result.model_kind == KIND_BASELINE
    assert result.forecast_hours == list(schema.FORECAST_HOURS)
    last = _window()[-1]
    for point in result.points:
        assert point[0] == pytest.approx(last[0], abs=1e-6)
        assert point[1] == pytest.approx(last[1], abs=1e-6)
        assert 0 <= point[2] <= schema.WIND_RANGE_MPS[1]
        assert schema.PRESSURE_RANGE_HPA[0] <= point[3] <= schema.PRESSURE_RANGE_HPA[1]
    assert any("基线" in note for note in result.notes)


def test_baseline_requires_full_window():
    forecaster = build_baseline_forecaster()
    with pytest.raises(ValueError):
        forecaster.predict(_window()[:3])


def test_correct_physical_range_clamps_and_limits_jumps():
    rows = [[95.0, 999.0, 500.0, 10.0], [100.0, 1000.0, 600.0, 5.0]]
    corrected = correct_physical_range(rows, previous_row=[20.0, 120.0, 30.0, 990.0])
    assert -90.0 <= corrected[0][0] <= 90.0
    assert -180.0 <= corrected[0][1] <= 180.0
    assert 0.0 <= corrected[0][2] <= schema.WIND_RANGE_MPS[1]
    assert schema.PRESSURE_RANGE_HPA[0] <= corrected[0][3] <= schema.PRESSURE_RANGE_HPA[1]
    # 跳变被收缩到上限附近
    assert corrected[0][0] <= 20.0 + 8.0


def test_window_extraction_uses_last_contiguous_points():
    from app.ml.validator import extract_input_window

    base = datetime(2026, 7, 18, 0, 0, 0)
    points = [
        ValidatedPoint(
            iso_time=base,
            lat=18.0,
            lon=126.0,
            max_wind_mps=20.0,
            central_pressure_hpa=1000.0,
        )
    ]
    for step in range(1, 6):
        points.append(
            ValidatedPoint(
                iso_time=base + timedelta(hours=6 * step),
                lat=18.0 + step,
                lon=126.0 - step,
                max_wind_mps=20.0 + step,
                central_pressure_hpa=1000.0 - step,
            )
        )
    window = extract_input_window(points)
    assert len(window) == 5
    assert window[-1][0] == 23.0

    # 时间断裂 -> 无法构成窗口（构造 5 个点但中间缺 12 小时）
    broken = points[:3] + [
        ValidatedPoint(
            iso_time=base + timedelta(hours=36),
            lat=22.0,
            lon=122.0,
            max_wind_mps=24.0,
            central_pressure_hpa=996.0,
        ),
        ValidatedPoint(
            iso_time=base + timedelta(hours=42),
            lat=23.0,
            lon=121.0,
            max_wind_mps=25.0,
            central_pressure_hpa=995.0,
        ),
    ]
    assert extract_input_window(broken) == []


@pytest.mark.skipif(not torch_available(), reason="未安装 PyTorch，跳过 Transformer 前向测试")
def test_transformer_forward_shape():
    import torch

    from app.ml.models.transformer import TyphoonTransformer

    model = TyphoonTransformer(feature_dim=4, input_length=5, output_length=12, d_model=64, nhead=4)
    output = model(torch.zeros(2, 5, 4))
    assert tuple(output.shape) == (2, 12, 4)
    assert model.hyperparams["d_model"] == 64


@pytest.mark.skipif(not torch_available(), reason="未安装 PyTorch，跳过 Transformer 校验测试")
def test_transformer_rejects_wrong_input_length():
    import torch

    from app.ml.models.transformer import TyphoonTransformer

    model = TyphoonTransformer(feature_dim=4, input_length=5, output_length=12)
    with pytest.raises(ValueError):
        model(torch.zeros(1, 3, 4))
    with pytest.raises(ValueError):
        model(torch.zeros(1, 5, 7))


@pytest.mark.skipif(not torch_available(), reason="未安装 PyTorch，跳过端到端推理测试")
def test_transformer_forecaster_end_to_end(tmp_path):
    import torch

    from app.ml.forecaster import build_transformer_forecaster
    from app.ml.models.transformer import TyphoonTransformer

    model = TyphoonTransformer(feature_dim=4, input_length=5, output_length=12)
    weights = tmp_path / "best.pt"
    torch.save(model.state_dict(), weights)

    scaler = FeatureScaler.from_fixed_range()
    scaler_path = scaler.save(tmp_path / "scaler.json")

    forecaster = build_transformer_forecaster(
        weights_path=weights,
        hyperparams=model.hyperparams,
        scaler_path=scaler_path,
        label="unit-test",
    )
    result = forecaster.predict(_window())
    assert len(result.points) == 12
    assert all(len(point) == 4 for point in result.points)
    assert result.model_label == "unit-test"
