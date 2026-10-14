"""统一 JSON 响应构造。

前端（Vue 3）只解析一种响应结构，降低联调成本：
    成功：{"ok": true,  "data": <payload>, "meta": {...}?}
    失败：{"ok": false, "error": {"code": ..., "message": ..., "details": ...?}}
"""

from __future__ import annotations

from typing import Any

from flask import jsonify


def success(data: Any = None, *, meta: dict[str, Any] | None = None, status: int = 200):
    payload: dict[str, Any] = {"ok": True, "data": data}
    if meta is not None:
        payload["meta"] = meta
    return jsonify(payload), status


def failure(error: dict[str, Any], *, status: int = 400):
    return jsonify({"ok": False, "error": error}), status


def paginated(items: list[Any], *, page: int, page_size: int, total: int):
    return success(
        items,
        meta={
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": (total + page_size - 1) // page_size if page_size else 0,
        },
    )


__all__ = ["success", "failure", "paginated"]
