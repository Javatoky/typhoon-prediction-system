"""Flask 应用工厂（9.1 单体分层架构、9.2 部署与运行过程）。

分层：
    app/api（控制层）→ app/services（业务层）→ app/models（数据访问层）
    app/ml（模型层：预处理器 + PyTorch 推理）
    app/core、app/utils（横切与纯函数工具）
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, g, jsonify, request, send_from_directory
from sqlalchemy import event
from sqlalchemy.engine import Engine
from werkzeug.exceptions import HTTPException

from app.core.errors import AppError
from app.core.logging_setup import configure_logging
from app.core.responses import failure, success
from app.extensions import db

logger = logging.getLogger(__name__)


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    """SQLite 默认不校验外键，这里显式打开，保证 CASCADE / SET NULL 生效。"""
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    except Exception:  # pragma: no cover - 非 SQLite 或不支持时忽略
        pass


def create_app(env_name: str | None = None) -> Flask:
    from config import ensure_directories, get_config

    config_class = get_config(env_name)
    ensure_directories(config_class)

    app = Flask(__name__, static_folder=None)
    app.config.from_object(config_class)

    if app.config["ENV_NAME"] == "production" and not app.config.get("SECRET_KEY"):
        raise RuntimeError(
            "生产环境必须设置 SECRET_KEY（可在 backend/.env 中配置）。"
            ' 生成方式：python -c "import secrets; print(secrets.token_hex(32))"'
        )

    configure_logging(app)

    # 数据访问层
    db.init_app(app)

    # 蓝图（控制层）
    from app.api import ALL_BLUEPRINTS

    for blueprint in ALL_BLUEPRINTS:
        app.register_blueprint(blueprint)

    _register_session_lifecycle(app)
    _register_dev_cors(app)
    _register_error_handlers(app)
    _register_spa_routes(app)
    _register_cli(app)

    with app.app_context():
        # 建表使用 create_all 以满足课程阶段“无 root 权限、零迁移工具”的约束；
        # 表结构变更请参考 docs/development.md 的升级说明。
        db.create_all()

    @app.before_request
    def _preload_model_registry():
        """Waitress 启动时预加载当前模型，避免每次请求重复读盘（9.2）。"""
        if app.extensions.get("model_registry") is not None:
            return None
        from app.ml.registry import ModelRegistry

        registry = ModelRegistry()
        app.extensions["model_registry"] = registry
        try:
            with app.app_context():
                registry.load_active()
        except Exception as exc:  # pragma: no cover
            app.logger.warning("预加载模型失败，将在请求时重试或降级为基线：%s", exc)
        return None

    app.logger.info(
        "应用初始化完成：env=%s database=%s", app.config["ENV_NAME"], app.config["SQLALCHEMY_DATABASE_URI"]
    )
    return app


# ---------------------------------------------------------------------------
# 会话生命周期（NFR-008：30 分钟无操作失效）
# ---------------------------------------------------------------------------
def _register_session_lifecycle(app: Flask) -> None:
    from app.api.deps import SESSION_LAST_SEEN, SESSION_USER_KEY

    @app.before_request
    def _refresh_session_idle():
        from flask import session

        if request.endpoint is None:
            return None
        if SESSION_USER_KEY not in session:
            return None
        idle_minutes = int(app.config.get("SESSION_IDLE_MINUTES", 30))
        raw = session.get(SESSION_LAST_SEEN)
        now = datetime.now(timezone.utc)
        if raw:
            try:
                last_seen = datetime.fromisoformat(raw)
                if last_seen.tzinfo is None:
                    last_seen = last_seen.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                last_seen = now
            if (now - last_seen) > timedelta(minutes=idle_minutes):
                session.clear()
                return failure(
                    {
                        "code": "session_expired",
                        "message": f"会话已因 {idle_minutes} 分钟无操作失效，请重新登录。",
                    },
                    status=401,
                )
        session[SESSION_LAST_SEEN] = now.isoformat()
        return None

    @app.teardown_appcontext
    def _cleanup_session(exception=None):  # noqa: ARG001
        db.session.remove()


# ---------------------------------------------------------------------------
# 开发期 CORS（前端 Vite dev server 跨端口调试）
# ---------------------------------------------------------------------------
def _register_dev_cors(app: Flask) -> None:
    if app.config["ENV_NAME"] not in {"development", "testing"}:
        return
    origins = set(app.config.get("DEV_CORS_ORIGINS", []))

    @app.after_request
    def _add_cors_headers(response):
        origin = request.headers.get("Origin")
        if origin and origin in origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Requested-With"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
            response.headers["Vary"] = "Origin"
        return response

    @app.route("/api/<path:_any>", methods=["OPTIONS"])
    def _cors_preflight(_any):  # pragma: no cover
        return ("", 204)


# ---------------------------------------------------------------------------
# 统一错误处理（12.1：不返回服务器路径或堆栈）
# ---------------------------------------------------------------------------
def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(AppError)
    def _handle_app_error(error: AppError):
        if error.status_code >= 500:
            app.logger.error("业务异常 %s: %s", error.code, error.message)
        return failure(error.to_dict(), status=error.status_code)

    @app.errorhandler(HTTPException)
    def _handle_http_error(error: HTTPException):
        mapping = {
            400: ("bad_request", "请求格式不正确，请检查提交的数据。"),
            401: ("unauthorized", "登录状态已失效，请重新登录。"),
            403: ("forbidden", "没有访问该资源的权限。"),
            404: ("not_found", "请求的资源不存在，请检查地址是否正确。"),
            405: ("method_not_allowed", "该地址不支持此请求方法。"),
            413: (
                "payload_too_large",
                f"上传内容超过 {app.config.get('MAX_UPLOAD_MB')} MB 限制，请裁剪后重试。",
            ),
            429: ("too_many_requests", "操作过于频繁，请稍后重试。"),
        }
        code, message = mapping.get(
            error.code or 500, ("http_error", error.description or "请求处理失败。")
        )
        return failure({"code": code, "message": message}, status=error.code or 500)

    @app.errorhandler(Exception)
    def _handle_unexpected(error: Exception):  # pragma: no cover
        app.logger.exception("未处理异常：%s", error)
        return failure(
            {
                "code": "internal_error",
                "message": "服务器内部错误，请稍后重试；若持续出现请联系管理员查看应用日志。",
            },
            status=500,
        )

    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response


# ---------------------------------------------------------------------------
# 前端静态资源（前后端分离：Flask 仅托管构建产物）
# ---------------------------------------------------------------------------
def _register_spa_routes(app: Flask) -> None:
    dist_dir = Path(app.config["FRONTEND_DIST_DIR"])
    index_file = dist_dir / "index.html"

    @app.get("/")
    def index():
        if index_file.exists():
            return send_from_directory(dist_dir, "index.html")
        return success(
            {
                "message": "后端 API 已启动，但尚未构建前端页面。",
                "frontend_dev": "在 frontend/ 目录执行 pnpm install && pnpm dev，然后访问 http://localhost:5173",
                "frontend_build": "在 frontend/ 目录执行 pnpm build，产物会输出到 backend/app/static/app/",
                "api_examples": ["/api/system/health", "/api/auth/login", "/api/typhoons"],
            }
        )

    @app.get("/assets/<path:filename>")
    def spa_assets(filename: str):
        assets_dir = dist_dir / "assets"
        if not assets_dir.exists():
            return failure({"code": "not_found", "message": "前端资源尚未构建。"}, status=404)
        return send_from_directory(assets_dir, filename)

    @app.get("/<path:path>")
    def spa_fallback(path: str):
        """SPA 路由回退：非 /api 前缀的路径统一返回 index.html。"""
        if path.startswith("api/"):
            return failure({"code": "not_found", "message": f"接口 /{path} 不存在。"}, status=404)
        candidate = dist_dir / path
        if candidate.is_file():
            return send_from_directory(dist_dir, path)
        if index_file.exists():
            return send_from_directory(dist_dir, "index.html")
        return failure(
            {
                "code": "frontend_not_built",
                "message": "前端尚未构建，请先执行 pnpm build 或使用 pnpm dev 开发服务器。",
            },
            status=404,
        )


# ---------------------------------------------------------------------------
# 常用 CLI（flask --app app run / flask --app app routes 等）
# ---------------------------------------------------------------------------
def _register_cli(app: Flask) -> None:
    @app.shell_context_processor
    def _shell_context():
        from app.models import (
            AuditLog,
            DatasetImport,
            ModelVersion,
            PredictionPoint,
            PredictionTask,
            TrackPoint,
            Typhoon,
            Upload,
            UploadPoint,
            User,
        )

        return {
            "db": db,
            "User": User,
            "Typhoon": Typhoon,
            "TrackPoint": TrackPoint,
            "DatasetImport": DatasetImport,
            "ModelVersion": ModelVersion,
            "PredictionTask": PredictionTask,
            "PredictionPoint": PredictionPoint,
            "Upload": Upload,
            "UploadPoint": UploadPoint,
            "AuditLog": AuditLog,
        }


__all__ = ["create_app"]
