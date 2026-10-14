"""ml.models 包：推理实现（Transformer 与持续性基线）。"""

from app.ml.models import baseline

__all__ = ["baseline"]

try:  # PyTorch 为可选项：未安装时仍可使用基线模型
    from app.ml.models import transformer  # noqa: F401

    __all__.append("transformer")
except ImportError:  # pragma: no cover
    transformer = None  # type: ignore[assignment]
