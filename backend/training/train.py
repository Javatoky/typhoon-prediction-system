"""Transformer 训练脚本（11.4 损失函数、11.5 离线训练过程）。

特性：
- 固定随机种子，记录数据版本、特征顺序、标准化参数、模型结构与指标；
- 损失由位置（经纬度 SmoothL1）、风速、气压三部分加权组成；
- 早停 + 梯度裁剪，输出 best.pt、last.pt 与 metadata.json；
- 同时输出持续性基线的验证集指标，便于判断模型是否真的优于基线（NFR-005）。

用法：
    python training/prepare_data.py --input data/raw/ibtracs.WP.list.v04r01.csv
    python training/train.py                       # CPU 即可运行
    python training/train.py --epochs 80 --device cuda
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.ml import schema  # noqa: E402
from app.ml.preprocess import FeatureScaler  # noqa: E402
from training.config import TrainConfig  # noqa: E402


def require_torch():
    try:
        import torch
        from torch import nn
        from torch.utils.data import DataLoader, TensorDataset
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "未安装 PyTorch，无法训练模型。请执行：pip install -r backend/requirements-ml.txt"
        ) from exc
    return torch, nn, DataLoader, TensorDataset


def resolve_device(torch, requested: str):
    if requested == "cpu":
        return torch.device("cpu")
    if requested == "cuda":
        if not torch.cuda.is_available():
            raise SystemExit("指定了 --device cuda，但当前环境没有可用的 CUDA 设备。")
        return torch.device("cuda")
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def weighted_loss(prediction, target, weights: dict):
    """位置/风速/气压三部分加权 SmoothL1（11.4）。"""
    import torch.nn.functional as functional

    position = functional.smooth_l1_loss(prediction[..., :2], target[..., :2])
    wind = functional.smooth_l1_loss(prediction[..., 2], target[..., 2])
    pressure = functional.smooth_l1_loss(prediction[..., 3], target[..., 3])
    total = (
        weights.get("position", 1.0) * position
        + weights.get("wind", 1.0) * wind
        + weights.get("pressure", 1.0) * pressure
    )
    return total, {
        "position": float(position.detach().cpu()),
        "wind": float(wind.detach().cpu()),
        "pressure": float(pressure.detach().cpu()),
    }


def load_dataset(processed_dir: Path):
    npz_path = processed_dir / "dataset.npz"
    if not npz_path.exists():
        raise SystemExit(
            f"找不到 {npz_path}。请先运行：python training/prepare_data.py --input <IBTrACS CSV>"
        )
    data = np.load(npz_path)
    return {key: data[key] for key in data.files}


def main() -> int:
    parser = argparse.ArgumentParser(description="训练 Transformer 台风路径预测模型")
    parser.add_argument("--processed-dir", default=str(TrainConfig().processed_dir))
    parser.add_argument("--output-dir", default=str(TrainConfig().output_dir))
    parser.add_argument("--epochs", type=int, default=TrainConfig().epochs)
    parser.add_argument("--batch-size", type=int, default=TrainConfig().batch_size)
    parser.add_argument("--learning-rate", type=float, default=TrainConfig().learning_rate)
    parser.add_argument("--device", default=TrainConfig().device)
    parser.add_argument("--seed", type=int, default=TrainConfig().seed)
    parser.add_argument("--d-model", type=int, default=TrainConfig().d_model)
    parser.add_argument("--nhead", type=int, default=TrainConfig().nhead)
    parser.add_argument("--layers", type=int, default=TrainConfig().num_encoder_layers)
    parser.add_argument("--ff", type=int, default=TrainConfig().dim_feedforward)
    parser.add_argument("--patience", type=int, default=TrainConfig().early_stop_patience)
    args = parser.parse_args()

    torch, nn, DataLoader, TensorDataset = require_torch()
    from app.ml.models.transformer import TyphoonTransformer

    config = TrainConfig(
        processed_dir=Path(args.processed_dir),
        output_dir=Path(args.output_dir),
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        device=args.device,
        seed=args.seed,
        d_model=args.d_model,
        nhead=args.nhead,
        num_encoder_layers=args.layers,
        dim_feedforward=args.ff,
        early_stop_patience=args.patience,
    )

    torch.manual_seed(config.seed)
    np.random.seed(config.seed)

    data = load_dataset(Path(config.processed_dir))
    device = resolve_device(torch, config.device)
    print(f"使用设备：{device}")

    def make_loader(x, y, *, shuffle: bool):
        dataset = TensorDataset(
            torch.tensor(x, dtype=torch.float32), torch.tensor(y, dtype=torch.float32)
        )
        return DataLoader(dataset, batch_size=config.batch_size, shuffle=shuffle)

    train_loader = make_loader(data["x_train"], data["y_train"], shuffle=True)
    val_loader = make_loader(data["x_val"], data["y_val"], shuffle=False)

    model = TyphoonTransformer(
        feature_dim=schema.FEATURE_DIM,
        input_length=config.input_length,
        output_length=config.output_length,
        d_model=config.d_model,
        nhead=config.nhead,
        num_encoder_layers=config.num_encoder_layers,
        dim_feedforward=config.dim_feedforward,
        dropout=config.dropout,
    ).to(device)

    parameter_count = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"模型参数量：{parameter_count:,}")

    optimizer = torch.optim.AdamW(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=max(2, config.early_stop_patience // 3)
    )

    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    best_path = output_dir / "best.pt"
    last_path = output_dir / "last.pt"
    history: list[dict] = []
    best_val = float("inf")
    best_epoch = -1
    patience_left = config.early_stop_patience

    for epoch in range(1, config.epochs + 1):
        model.train()
        train_total = 0.0
        for xb, yb in train_loader:
            xb, yb = xb.to(device), yb.to(device)
            optimizer.zero_grad(set_to_none=True)
            prediction = model(xb)
            loss, _ = weighted_loss(prediction, yb, config.loss_weights)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), config.grad_clip)
            optimizer.step()
            train_total += float(loss.detach().cpu()) * xb.size(0)
        train_loss = train_total / max(1, len(data["x_train"]))

        model.eval()
        val_total = 0.0
        with torch.no_grad():
            for xb, yb in val_loader:
                xb, yb = xb.to(device), yb.to(device)
                loss, _ = weighted_loss(model(xb), yb, config.loss_weights)
                val_total += float(loss.detach().cpu()) * xb.size(0)
        val_loss = val_total / max(1, len(data["x_val"]))

        scheduler.step(val_loss)
        history.append({"epoch": epoch, "train_loss": train_loss, "val_loss": val_loss})
        improved = val_loss < best_val - config.min_delta
        print(
            f"epoch {epoch:03d} | train {train_loss:.5f} | val {val_loss:.5f} | "
            f"lr {optimizer.param_groups[0]['lr']:.2e} | {'*' if improved else ''}"
        )

        if improved:
            best_val = val_loss
            best_epoch = epoch
            patience_left = config.early_stop_patience
            torch.save(model.state_dict(), best_path)
        else:
            patience_left -= 1
            if patience_left <= 0:
                print(f"验证集连续 {config.early_stop_patience} 轮未改善，提前停止训练。")
                break

        torch.save(model.state_dict(), last_path)
        # 训练中每次改善都刷新元数据，保证中断时也能交付可用权重
        write_metadata(output_dir, config, model, parameter_count, history, best_val, best_epoch, data)

    if not best_path.exists():  # 极端情况：从未改善
        torch.save(model.state_dict(), best_path)

    baseline = persistence_val_loss(data, config)
    print(f"验证集最佳损失 {best_val:.5f}（epoch {best_epoch}）；持续性基线验证损失 {baseline:.5f}")
    if best_val >= baseline:
        print(
            "注意：当前模型未优于持续性基线。按 6.1 要求仍可完成软件功能验收，"
            "但报告必须如实披露模型限制，并优先检查数据泄漏、缺失处理与单位口径（11.5）。"
        )

    write_metadata(
        output_dir,
        config,
        model,
        parameter_count,
        history,
        best_val,
        best_epoch,
        data,
        baseline_val_loss=baseline,
    )
    print(f"训练完成。best.pt: {best_path}\n元数据: {output_dir / 'metadata.json'}")
    return 0


def persistence_val_loss(data, config: TrainConfig) -> float:
    """持续性基线在验证集上的损失（同一损失函数），用于 NFR-005 对照。"""
    from app.utils.metrics import mae  # noqa: F401  (保持指标口径引用)

    x_val, y_val = data["x_val"], data["y_val"]
    if len(x_val) == 0:
        return float("nan")
    prediction = np.repeat(x_val[:, -1:, :], config.output_length, axis=1)
    position = np.mean(np.abs(prediction[..., :2] - y_val[..., :2]))
    wind = np.mean(np.abs(prediction[..., 2] - y_val[..., 2]))
    pressure = np.mean(np.abs(prediction[..., 3] - y_val[..., 3]))
    weight = config.loss_weights
    return float(
        weight.get("position", 1.0) * position
        + weight.get("wind", 1.0) * wind
        + weight.get("pressure", 1.0) * pressure
    )


def write_metadata(
    output_dir: Path,
    config: TrainConfig,
    model,
    parameter_count: int,
    history: list[dict],
    best_val: float,
    best_epoch: int,
    data,
    *,
    baseline_val_loss: float | None = None,
) -> Path:
    """输出 metadata.json：交付给 Web 系统上传模型时使用（11.5）。"""
    scaler_path = Path(config.processed_dir) / "scaler.json"
    scaler_payload = None
    if scaler_path.exists():
        scaler_payload = json.loads(scaler_path.read_text(encoding="utf-8"))

    meta = {
        "name": config.model_name,
        "version": config.version,
        "arch": "transformer_encoder",
        "kind": "transformer",
        "feature_order": list(schema.FEATURE_COLUMNS),
        "input_length": config.input_length,
        "output_length": config.output_length,
        "feature_dim": schema.FEATURE_DIM,
        "training_data_version": config.data_version,
        "hyperparams": model.hyperparams,
        "parameter_count": parameter_count,
        "scaler": scaler_payload,
        "train_config": config.as_dict(),
        "history": history,
        "best_val_loss": best_val,
        "best_epoch": best_epoch,
        "baseline_val_loss": baseline_val_loss,
        "sample_counts": {
            "train": int(len(data["x_train"])),
            "val": int(len(data["x_val"])),
            "test": int(len(data["x_test"])),
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "notes": config.notes,
        "disclaimer": "课程教学原型，结果不得作为官方防灾决策依据。",
    }
    path = output_dir / "metadata.json"
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


if __name__ == "__main__":
    raise SystemExit(main())
