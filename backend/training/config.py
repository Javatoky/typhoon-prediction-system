"""训练管线配置。

与 backend/config.py 保持同一口径：离线训练脚本通过 sys.path 复用 app.ml.schema，
避免“训练时特征顺序与 Web 推理不一致”这类致命错误（11.3 / 11.5）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent

DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw"
DEFAULT_PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
DEFAULT_MODEL_DIR = PROJECT_ROOT / "data" / "models"


@dataclass
class TrainConfig:
    # 数据
    input_path: Path = DEFAULT_RAW_DIR / "ibtracs.WP.list.v04r01.csv"
    processed_dir: Path = DEFAULT_PROCESSED_DIR
    output_dir: Path = DEFAULT_MODEL_DIR
    basin: str = "WP"
    input_length: int = 5
    output_length: int = 12
    step_hours: int = 6
    # 冻结口径（docs/data-card.md D-03/D-04）
    synoptic_only: bool = True  # 只保留 00/06/12/18 UTC 记录，禁止插值降频
    min_year: int = 2000  # 年份下限（2000 年前无可用样本）
    # 划分比例（DQ-06：按 SID 划分）
    train_ratio: float = 0.7
    val_ratio: float = 0.15
    test_ratio: float = 0.15
    seed: int = 20250901
    # 模型（11.2 建议初值）
    d_model: int = 64
    nhead: int = 4
    num_encoder_layers: int = 2
    dim_feedforward: int = 128
    dropout: float = 0.1
    # 训练
    batch_size: int = 64
    epochs: int = 60
    learning_rate: float = 1e-3
    weight_decay: float = 1e-4
    grad_clip: float = 1.0
    early_stop_patience: int = 10
    min_delta: float = 1e-4
    num_workers: int = 0
    device: str = "auto"  # auto / cpu / cuda
    # 损失权重（11.4：位置、风速、气压三部分加权）
    loss_weights: dict[str, float] = field(
        default_factory=lambda: {"position": 1.0, "wind": 0.6, "pressure": 0.4}
    )
    # 元数据
    model_name: str = "TyphoonTransformer"
    version: str = "0.1.0"
    data_version: str = "ibtracs-v04r01-wp-2000plus-synoptic"
    notes: str = "课程原型：单次多步输出，PyTorch 内置 TransformerEncoder。"

    def as_dict(self) -> dict:
        payload = dict(self.__dict__)
        for key, value in payload.items():
            if isinstance(value, Path):
                payload[key] = str(value)
        return payload


__all__ = ["TrainConfig", "DEFAULT_RAW_DIR", "DEFAULT_PROCESSED_DIR", "DEFAULT_MODEL_DIR", "PROJECT_ROOT", "BACKEND_DIR"]
