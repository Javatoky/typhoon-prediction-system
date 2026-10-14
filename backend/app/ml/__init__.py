"""ml 包：数据校验、预处理与模型推理。

该包尽量保持与 Flask 解耦：schema / validator / preprocess 可在离线训练脚本中直接复用。
"""

from app.ml import preprocess, schema, validator

__all__ = ["preprocess", "schema", "validator"]
