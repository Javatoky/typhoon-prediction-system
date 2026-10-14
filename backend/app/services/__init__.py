"""services 包：业务编排层。

约定：
- 服务层可以访问数据库与 app.ml，但不得直接读写 flask.request；
  需要请求上下文信息（IP 等）时通过 audit_service.client_ip() 等辅助函数。
- 服务层负责事务边界与把异常转换为 AppError 子类（12.1）。
"""

from app.services import (
    audit_service,
    auth_service,
    dataset_service,
    model_service,
    prediction_service,
    typhoon_service,
    upload_service,
    user_service,
)

__all__ = [
    "audit_service",
    "auth_service",
    "dataset_service",
    "model_service",
    "prediction_service",
    "typhoon_service",
    "upload_service",
    "user_service",
]
