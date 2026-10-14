"""持续性预测基线（13.1 第2步、NFR-005）。

作用：
1. 训练完成前的默认推理实现，使 Web 闭环（任务→结果→地图→评估）可以先行验收。
2. 作为 Transformer 的评价对照基线：24 小时平均误差目标不劣于该基线。

实现：把最后一个观测点的位置与强度重复填充到全部 12 个时效。
不做任何拟合，因此“模型不可用”时也可以安全兜底。
"""

from __future__ import annotations

from typing import Sequence

from app.ml import schema

BASELINE_KIND = "rule_based_persistence"
BASELINE_NAME = "持续性预测基线"
BASELINE_VERSION = "0.1.0"


class PersistenceBaseline:
    """无需权重文件的内置基线模型。"""

    kind = BASELINE_KIND
    arch = "persistence"
    name = BASELINE_NAME
    version = BASELINE_VERSION

    def __init__(self, *, input_length: int = schema.INPUT_LENGTH, output_length: int = schema.OUTPUT_LENGTH) -> None:
        self.input_length = int(input_length)
        self.output_length = int(output_length)
        self.hyperparams = {
            "arch": "persistence",
            "input_length": self.input_length,
            "output_length": self.output_length,
            "feature_dim": schema.FEATURE_DIM,
            "note": "规则基线，不使用神经网络参数",
        }

    def predict(self, window: Sequence[Sequence[float]]) -> list[list[float]]:
        """window 为标准化前的原始值 [[lat, lon, wind, pres], ...]，返回 12 个原始值预测点。"""
        if not window:
            raise ValueError("持续性基线需要至少 1 个观测点。")
        last = [float(value) for value in window[-1]]
        if len(last) != schema.FEATURE_DIM:
            raise ValueError(f"观测点长度必须为 {schema.FEATURE_DIM}。")
        return [list(last) for _ in range(self.output_length)]

    def eval(self) -> "PersistenceBaseline":  # 与 nn.Module 接口保持一致，便于统一调用
        return self

    def to(self, *_args, **_kwargs) -> "PersistenceBaseline":
        return self


def predict_from_window(window: Sequence[Sequence[float]], *, output_length: int = schema.OUTPUT_LENGTH) -> list[list[float]]:
    """函数式调用，便于训练评估脚本直接使用。"""
    return PersistenceBaseline(output_length=output_length).predict(window)


__all__ = [
    "BASELINE_KIND",
    "BASELINE_NAME",
    "BASELINE_VERSION",
    "PersistenceBaseline",
    "predict_from_window",
]
