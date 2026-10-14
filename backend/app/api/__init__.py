"""api 包：控制层（蓝图注册见 app.create_app）。"""

from app.api import admin, auth, predictions, system, typhoons, uploads

ALL_BLUEPRINTS = (
    auth.bp,
    typhoons.bp,
    uploads.bp,
    predictions.bp,
    admin.bp,
    system.bp,
)

__all__ = ["admin", "auth", "predictions", "system", "typhoons", "uploads", "ALL_BLUEPRINTS"]
