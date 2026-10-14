"""推理统一入口（11.6 在线推理过程）。

职责边界：
- 只做标准化 → 前向计算 → 反标准化 → 物理范围修正，绝不修改模型参数。
- 对上（服务层）暴露统一接口：predict(window) -> [[lat, lon, wind, pres], ...]
- 对下屏蔽“Transformer 权重”与“规则基线”的差异，使得没有权重文件时系统仍可闭环运行。

torch 为可选依赖：仅当确实需要加载 .pt 权重时才会 import。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from app.ml import schema
from app.ml.models.baseline import BASELINE_KIND, PersistenceBaseline
from app.ml.preprocess import FeatureScaler, correct_physical_range

KIND_TRANSFORMER = "transformer"
KIND_BASELINE = BASELINE_KIND


def torch_available() -> bool:
    """torch 是否可用（不影响基线推理）。"""
    try:
        import torch  # noqa: F401

        return True
    except ImportError:
        return False


@dataclass
class ForecastResult:
    """一次推理的输出。"""

    points: list[list[float]]  # 12 × 4，顺序同 schema.FEATURE_COLUMNS
    model_label: str = ""
    model_kind: str = KIND_BASELINE
    forecast_hours: list[int] = field(default_factory=lambda: list(schema.FORECAST_HOURS))
    notes: list[str] = field(default_factory=list)

    def to_rows(self) -> list[dict[str, float]]:
        rows: list[dict[str, float]] = []
        for hour, point in zip(self.forecast_hours, self.points):
            rows.append({"forecast_hour": hour, **schema.feature_dict_from_values(point)})
        return rows


class Forecaster:
    """包装可用的模型对象，统一执行标准化、推理与物理修正。"""

    def __init__(
        self,
        model: Any,
        *,
        kind: str,
        label: str,
        scaler: FeatureScaler | None = None,
        feature_order: Sequence[str] = schema.FEATURE_COLUMNS,
        input_length: int = schema.INPUT_LENGTH,
        output_length: int = schema.OUTPUT_LENGTH,
        model_version_id: int | None = None,
        training_data_version: str | None = None,
        note: str | None = None,
    ) -> None:
        if list(feature_order) != list(schema.FEATURE_COLUMNS):
            raise ValueError(
                "模型特征顺序与系统约定不一致："
                f"模型为 {list(feature_order)}，系统要求 {list(schema.FEATURE_COLUMNS)}"
            )
        self.model = model
        self.kind = kind
        self.label = label
        self.scaler = scaler
        self.feature_order = list(feature_order)
        self.input_length = int(input_length)
        self.output_length = int(output_length)
        self.model_version_id = model_version_id
        self.training_data_version = training_data_version
        self.note = note

    # ------------------------------------------------------------------
    # 推理
    # ------------------------------------------------------------------
    def predict(self, window: Sequence[Sequence[float]]) -> ForecastResult:
        if len(window) != self.input_length:
            raise ValueError(
                f"输入窗口必须包含 {self.input_length} 个观测点，实际为 {len(window)}。"
            )
        raw_window = [[float(value) for value in row] for row in window]
        for row in raw_window:
            if len(row) != schema.FEATURE_DIM:
                raise ValueError(f"每个观测点必须包含 {schema.FEATURE_DIM} 个特征。")

        if self.scaler is not None and not self.scaler.fixed_range:
            standardized = self.scaler.transform_window(raw_window)
        else:
            standardized = raw_window

        predicted = self._forward(standardized)

        if self.scaler is not None and not self.scaler.fixed_range:
            raw_prediction = self.scaler.inverse_transform(predicted)
        else:
            raw_prediction = predicted

        corrected = correct_physical_range(raw_prediction, previous_row=raw_window[-1])

        notes: list[str] = []
        if self.note:
            notes.append(self.note)
        if self.scaler is None:
            notes.append("该模型未附带标准化器，推理使用原始量纲。")
        elif self.scaler.fixed_range:
            notes.append("该模型使用物理量程兜底标准化（fixed_range），尚未经过正式训练。")
        if self.kind == KIND_BASELINE:
            notes.append("当前结果为持续性预测基线，用于打通系统闭环，不代表模型预报能力。")

        return ForecastResult(
            points=corrected,
            model_label=self.label,
            model_kind=self.kind,
            forecast_hours=list(schema.FORECAST_HOURS[: self.output_length]),
            notes=notes,
        )

    def _forward(self, standardized: Sequence[Sequence[float]]) -> list[list[float]]:
        if self.kind == KIND_BASELINE:
            return self.model.predict(standardized)

        import torch  # 延迟导入：仅真正使用 Transformer 时才需要

        tensor = torch.tensor([list(row) for row in standardized], dtype=torch.float32)
        if tensor.dim() == 2:
            tensor = tensor.unsqueeze(0)  # (1, L, F)
        self.model.eval()
        with torch.no_grad():
            output = self.model(tensor)
        return [[float(value) for value in row] for row in output[0].tolist()]

    # ------------------------------------------------------------------
    # 元数据
    # ------------------------------------------------------------------
    def describe(self) -> dict:
        return {
            "label": self.label,
            "kind": self.kind,
            "model_version_id": self.model_version_id,
            "input_length": self.input_length,
            "output_length": self.output_length,
            "feature_order": self.feature_order,
            "training_data_version": self.training_data_version,
            "scaler": self.scaler.to_dict() if self.scaler else None,
            "torch_available": torch_available(),
        }


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------
def build_baseline_forecaster(
    *, note: str = "系统尚未启用任何已训练模型。", output_length: int = schema.OUTPUT_LENGTH
) -> Forecaster:
    model = PersistenceBaseline(output_length=output_length)
    return Forecaster(
        model,
        kind=KIND_BASELINE,
        label=f"{model.name} {model.version}",
        scaler=None,
        output_length=output_length,
        note=note,
    )


def build_transformer_forecaster(
    *,
    weights_path: str | Path,
    hyperparams: dict,
    scaler_path: str | Path | None = None,
    label: str = "Transformer",
    model_version_id: int | None = None,
    training_data_version: str | None = None,
    map_location: str = "cpu",
) -> Forecaster:
    """加载 PyTorch 权重并构造推理器。

    权重文件支持两种保存格式：
    1. 纯 state_dict（训练脚本默认）
    2. {"state_dict": ..., "hyperparams": ...} 或 {"model_state": ...} 的检查点字典
    """
    if not torch_available():
        raise RuntimeError(
            "当前环境未安装 PyTorch，无法加载 Transformer 权重。"
            "请安装 backend/requirements-ml.txt，或改用持续性基线（清除当前启用模型）。"
        )

    import torch
    from app.ml.models.transformer import TyphoonTransformer

    checkpoint = torch.load(str(weights_path), map_location=map_location, weights_only=False)
    state_dict = checkpoint
    merged_params = dict(hyperparams or {})
    if isinstance(checkpoint, dict):
        for key in ("state_dict", "model_state_dict", "model_state"):
            if key in checkpoint and isinstance(checkpoint[key], dict):
                state_dict = checkpoint[key]
                break
        if isinstance(checkpoint.get("hyperparams"), dict):
            merged_params = {**checkpoint["hyperparams"], **merged_params}
    if not isinstance(state_dict, dict):
        raise ValueError("权重文件格式无法识别，应为 state_dict 或其包装字典。")

    model = TyphoonTransformer(
        feature_dim=int(merged_params.get("feature_dim", schema.FEATURE_DIM)),
        input_length=int(merged_params.get("input_length", schema.INPUT_LENGTH)),
        output_length=int(merged_params.get("output_length", schema.OUTPUT_LENGTH)),
        d_model=int(merged_params.get("d_model", 64)),
        nhead=int(merged_params.get("nhead", 4)),
        num_encoder_layers=int(merged_params.get("num_encoder_layers", 2)),
        dim_feedforward=int(merged_params.get("dim_feedforward", 128)),
        dropout=float(merged_params.get("dropout", 0.1)),
    )
    missing, unexpected = model.load_state_dict(state_dict, strict=False)
    if missing or unexpected:
        raise ValueError(
            "权重与模型结构不匹配："
            f"缺失参数 {list(missing)[:5]}（共 {len(missing)} 个），"
            f"多余参数 {list(unexpected)[:5]}（共 {len(unexpected)} 个）。"
            "请确认元数据中的模型结构与本系统 schema 一致。"
        )

    scaler = None
    if scaler_path:
        scaler = FeatureScaler.load(scaler_path)

    return Forecaster(
        model,
        kind=KIND_TRANSFORMER,
        label=label,
        scaler=scaler,
        input_length=int(merged_params.get("input_length", schema.INPUT_LENGTH)),
        output_length=int(merged_params.get("output_length", schema.OUTPUT_LENGTH)),
        model_version_id=model_version_id,
        training_data_version=training_data_version,
    )


def load_metadata(path: str | Path) -> dict:
    """读取模型元数据 JSON（11.5：训练脚本必须交付的元数据）。"""
    return json.loads(Path(path).read_text(encoding="utf-8"))


__all__ = [
    "KIND_TRANSFORMER",
    "KIND_BASELINE",
    "torch_available",
    "ForecastResult",
    "Forecaster",
    "build_baseline_forecaster",
    "build_transformer_forecaster",
    "load_metadata",
]
