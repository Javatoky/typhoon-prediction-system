"""模型评估脚本（11.4 指标、6.1 准确性说明、NFR-005 基线对比）。

评估内容：
- 分时效 Haversine 距离误差（6/12/24/48/72 小时）、ADE、FDE；
- 风速 MAE、气压 MAE、强度等级准确率；
- 与持续性基线在同一独立测试集上的对比（按台风划分，DQ-06）。

用法：
    python training/evaluate.py                       # 评估 data/models/best.pt
    python training/evaluate.py --weights path/to/best.pt --split test
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.ml import schema  # noqa: E402
from app.ml.preprocess import FeatureScaler  # noqa: E402
from app.utils.intensity import classify_intensity  # noqa: E402
from app.utils.metrics import summarize  # noqa: E402
from training.config import TrainConfig  # noqa: E402


def require_torch():
    try:
        import torch
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "未安装 PyTorch，无法加载模型权重。请执行：pip install -r backend/requirements-ml.txt"
        ) from exc
    return torch


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="评估 Transformer 台风路径预测模型")
    parser.add_argument("--processed-dir", default=str(TrainConfig().processed_dir))
    parser.add_argument("--weights", default=str(TrainConfig().output_dir / "best.pt"))
    parser.add_argument("--metadata", default=str(TrainConfig().output_dir / "metadata.json"))
    parser.add_argument("--split", default="test", choices=["train", "val", "test"])
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--output", default=None, help="指标 JSON 输出路径，默认写到模型目录 metrics.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    torch = require_torch()
    from app.ml.models.transformer import TyphoonTransformer

    processed_dir = Path(args.processed_dir)
    scaler = FeatureScaler.load(processed_dir / "scaler.json")
    data = np.load(processed_dir / "dataset.npz")
    x = data[f"x_{args.split}"]
    y = data[f"y_{args.split}"]
    if len(x) == 0:
        raise SystemExit(f"{args.split} 集合为空，请检查数据划分。")

    keyword = {}
    metadata_path = Path(args.metadata)
    if metadata_path.exists():
        keyword = json.loads(metadata_path.read_text(encoding="utf-8")).get("hyperparams", {})
    model = TyphoonTransformer(
        feature_dim=schema.FEATURE_DIM,
        input_length=int(keyword.get("input_length", schema.INPUT_LENGTH)),
        output_length=int(keyword.get("output_length", schema.OUTPUT_LENGTH)),
        d_model=int(keyword.get("d_model", 64)),
        nhead=int(keyword.get("nhead", 4)),
        num_encoder_layers=int(keyword.get("num_encoder_layers", 2)),
        dim_feedforward=int(keyword.get("dim_feedforward", 128)),
        dropout=float(keyword.get("dropout", 0.1)),
    )
    state = torch.load(args.weights, map_location="cpu", weights_only=False)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    model.load_state_dict(state)
    model.eval()

    with torch.no_grad():
        standardized = model(torch.tensor(x, dtype=torch.float32)).numpy()

    predicted = np.stack([scaler.inverse_transform(item) for item in standardized])
    actual = np.stack([scaler.inverse_transform(item) for item in y])
    baseline = np.repeat(x[:, -1:, :], actual.shape[1], axis=1)
    baseline_raw = np.stack([scaler.inverse_transform(item) for item in baseline])

    report = {
        "split": args.split,
        "sample_count": int(len(x)),
        "feature_columns": list(schema.FEATURE_COLUMNS),
        "model": metrics_for(predicted, actual),
        "persistence_baseline": metrics_for(baseline_raw, actual),
        "notes": [
            "距离误差按 Haversine 大圆距离计算，单位为 km。",
            "强度等级按 GB/T 19201-2006 由预测风速映射得到，属教学展示结果。",
            "测试集按台风 SID 划分，与训练集无重叠（DQ-06）。",
        ],
    }

    model_24 = report["model"]["key_hours_km"].get("24")
    base_24 = report["persistence_baseline"]["key_hours_km"].get("24")
    if model_24 is not None and base_24 is not None:
        report["meets_24h_baseline"] = model_24 <= base_24
        report["comparison_24h"] = {
            "model_km": model_24,
            "baseline_km": base_24,
            "verdict": "优于或持平持续性基线" if model_24 <= base_24 else "未优于持续性基线",
        }

    output = Path(args.output) if args.output else Path(args.weights).parent / f"metrics_{args.split}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"独立集合：{args.split}（{len(x)} 个样本）")
    print("时效距离误差（km）：")
    for hour, value in report["model"]["key_hours_km"].items():
        baseline_value = report["persistence_baseline"]["key_hours_km"].get(hour)
        print(f"  {hour:>3}h  模型 {value:8.2f}   基线 {baseline_value:8.2f}")
    print(f"ADE: 模型 {report['model']['ade_km']} km，基线 {report['persistence_baseline']['ade_km']} km")
    print(f"FDE: 模型 {report['model']['fde_km']} km，基线 {report['persistence_baseline']['fde_km']} km")
    print(f"风速 MAE: {report['model']['wind_mae_mps']} m/s；气压 MAE: {report['model']['pressure_mae_hpa']} hPa")
    print(f"强度等级准确率: {report['model']['category_accuracy']}")
    if "comparison_24h" in report:
        print(f"24 小时对比：{report['comparison_24h']['verdict']}")
    print(f"指标已写入：{output}")
    return 0


def metrics_for(predicted: np.ndarray, actual: np.ndarray) -> dict:
    count = predicted.shape[0]
    flat_pred = predicted.reshape(-1, schema.FEATURE_DIM)
    flat_act = actual.reshape(-1, schema.FEATURE_DIM)
    hours = list(schema.FORECAST_HOURS[: predicted.shape[1]])
    summary = summarize(
        flat_pred[:, :2],
        flat_act[:, :2],
        predicted_wind=flat_pred[:, 2],
        actual_wind=flat_act[:, 2],
        predicted_pressure=flat_pred[:, 3],
        actual_pressure=flat_act[:, 3],
        predicted_category=[classify_intensity(value)[0] for value in flat_pred[:, 2]],
        actual_category=[classify_intensity(value)[0] for value in flat_act[:, 2]],
        forecast_hours=hours * count,
    )
    return summary


if __name__ == "__main__":
    raise SystemExit(main())
