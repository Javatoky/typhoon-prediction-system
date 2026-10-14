"""业务异常与统一错误响应。

对应 12.1：任何层的异常都由服务层转换为统一业务错误，
不向浏览器返回服务器路径或堆栈。
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """所有可预期业务异常的基类。

    属性：
        message: 面向用户的中文提示（NFR-007 要求提示包含原因与修正方法）。
        status_code: HTTP 状态码。
        code: 机器可读错误码，前端据此做分支处理。
        details: 结构化附加信息（例如行级校验错误列表）。
    """

    status_code = 400
    code = "bad_request"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            payload["details"] = self.details
        return payload


class ValidationError(AppError):
    """请求参数或上传数据不合法（FR-005）。"""

    status_code = 422
    code = "validation_error"


class AuthError(AppError):
    """未登录或会话失效（FR-001）。"""

    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    """已登录但权限不足（FR-012、7.1 无权限接口仍须拒绝）。"""

    status_code = 403
    code = "forbidden"


class NotFoundError(AppError):
    """资源不存在（例如任务不属于当前用户时统一按不存在处理，避免泄露他人任务存在性）。"""

    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    """状态冲突，例如用户名重复、并发启用模型。"""

    status_code = 409
    code = "conflict"


class PayloadTooLargeError(AppError):
    """文件超过大小限制（NFR-004：超限在解析前拒绝）。"""

    status_code = 413
    code = "payload_too_large"


class ModelUnavailableError(AppError):
    """当前无可用模型且基线不可用。"""

    status_code = 503
    code = "model_unavailable"


class TaskFailedError(AppError):
    """预测任务执行失败（UC-04 异常流程）。"""

    status_code = 502
    code = "task_failed"


__all__ = [
    "AppError",
    "ValidationError",
    "AuthError",
    "ForbiddenError",
    "NotFoundError",
    "ConflictError",
    "PayloadTooLargeError",
    "ModelUnavailableError",
    "TaskFailedError",
]
