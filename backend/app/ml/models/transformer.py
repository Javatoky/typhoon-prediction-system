"""Transformer Encoder 多步预测模型（11.1 / 11.2）。

结构（使用 PyTorch 内置组件，避免手写注意力机制出错）：
    输入 (B, L_in=5, F=4)
      -> 线性投影到 d_model
      -> 可学习位置编码
      -> TransformerEncoder(num_layers=2, nhead=4, dim_feedforward=128, dropout=0.1)
      -> 取最后时刻表示（拼接均值池化，提升小模型稳定性）
      -> MLP 输出头 (B, L_out=12, F=4)

输出为一次性多步预测（非自回归），与 11.1 “一次输出未来 12 个 6 小时时效点”一致。

本模块需要 PyTorch；未安装时 import 会失败，调用方必须使用
app.ml.forecaster 中的可用性判断，不要直接硬导入。
"""

from __future__ import annotations

import torch
from torch import nn


class PositionalEncoding(nn.Module):
    """可学习位置编码：比正弦编码更简单，适合固定窗口长度。"""

    def __init__(self, d_model: int, max_len: int = 64, dropout: float = 0.1) -> None:
        super().__init__()
        self.dropout = nn.Dropout(dropout)
        self.encoding = nn.Parameter(torch.zeros(1, max_len, d_model))
        nn.init.trunc_normal_(self.encoding, std=0.02)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        length = x.size(1)
        if length > self.encoding.size(1):
            raise ValueError(
                f"序列长度 {length} 超过位置编码上限 {self.encoding.size(1)}，请调整 input_length。"
            )
        return self.dropout(x + self.encoding[:, :length, :])


class TyphoonTransformer(nn.Module):
    """需求 11.2 的入门级 Transformer 编码器模型。"""

    def __init__(
        self,
        *,
        feature_dim: int = 4,
        input_length: int = 5,
        output_length: int = 12,
        d_model: int = 64,
        nhead: int = 4,
        num_encoder_layers: int = 2,
        dim_feedforward: int = 128,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        if d_model % nhead != 0:
            raise ValueError(f"d_model({d_model}) 必须能被注意力头数({nhead})整除。")

        self.feature_dim = int(feature_dim)
        self.input_length = int(input_length)
        self.output_length = int(output_length)
        self.d_model = int(d_model)
        self.hyperparams = {
            "arch": "transformer_encoder",
            "feature_dim": self.feature_dim,
            "input_length": self.input_length,
            "output_length": self.output_length,
            "d_model": int(d_model),
            "nhead": int(nhead),
            "num_encoder_layers": int(num_encoder_layers),
            "dim_feedforward": int(dim_feedforward),
            "dropout": float(dropout),
        }

        self.input_projection = nn.Linear(self.feature_dim, self.d_model)
        self.positional_encoding = PositionalEncoding(self.d_model, max_len=64, dropout=dropout)

        encoder_layer = nn.TransformerEncoderLayer(
            d_model=self.d_model,
            nhead=int(nhead),
            dim_feedforward=int(dim_feedforward),
            dropout=float(dropout),
            activation="gelu",
            batch_first=True,
            norm_first=False,
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=int(num_encoder_layers))
        self.norm = nn.LayerNorm(self.d_model)

        # 输出头：把表示映射到 output_length × feature_dim 的连续值
        self.head = nn.Sequential(
            nn.Linear(self.d_model * 2, self.d_model),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(self.d_model, self.output_length * self.feature_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x.dim() != 3:
            raise ValueError(f"输入张量必须为 (B, L, F) 三维，实际维度 {x.dim()}。")
        batch, length, features = x.shape
        if features != self.feature_dim:
            raise ValueError(f"输入特征维度应为 {self.feature_dim}，实际为 {features}。")
        if length != self.input_length:
            raise ValueError(
                f"输入序列长度应为 {self.input_length}（最近 {self.input_length} 个 6 小时观测点），实际为 {length}。"
            )

        hidden = self.input_projection(x)
        hidden = self.positional_encoding(hidden)
        encoded = self.encoder(hidden)
        encoded = self.norm(encoded)

        last = encoded[:, -1, :]
        pooled = encoded.mean(dim=1)
        representation = torch.cat([last, pooled], dim=-1)

        output = self.head(representation)
        return output.view(batch, self.output_length, self.feature_dim)


def build_model(hyperparams: dict) -> TyphoonTransformer:
    """按元数据中的超参数构造模型实例。"""
    params = dict(hyperparams or {})
    params.pop("arch", None)
    return TyphoonTransformer(**params)


__all__ = ["PositionalEncoding", "TyphoonTransformer", "build_model"]
