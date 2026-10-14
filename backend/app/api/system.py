"""系统信息接口（7 外部接口需求、7.1 用户界面要求）。

前端启动时调用 /api/system/config 获取特征口径、时效、强度阈值与免责声明，
避免这些常量在前端硬编码（NFR-009）。
"""

from __future__ import annotations

from flask import Blueprint, current_app

from app.api.deps import login_required
from app.core.responses import success
from app.ml import schema
from app.ml.registry import get_registry
from app.utils.intensity import threshold_table

bp = Blueprint("system", __name__, url_prefix="/api/system")

DISCLAIMER = (
    "本系统为《软件工程》课程教学原型，预测结果仅用于教学研究与系统演示，"
    "不得作为官方防灾减灾决策依据。"
)


@bp.get("/health")
def health():
    """健康检查：不要求登录，供部署与稳定性测试使用。"""
    return success({"status": "ok", "env": current_app.config.get("ENV_NAME")})


@bp.get("/config")
@login_required
def config():
    return success(
        {
            "env": current_app.config.get("ENV_NAME"),
            "disclaimer": DISCLAIMER,
            "features": [
                {
                    "name": name,
                    "label": schema.FEATURE_LABELS_CN[name],
                    "unit": schema.FEATURE_UNITS[name],
                }
                for name in schema.FEATURE_COLUMNS
            ],
            "input_length": current_app.config.get("INPUT_LENGTH", schema.INPUT_LENGTH),
            "output_length": current_app.config.get("OUTPUT_LENGTH", schema.OUTPUT_LENGTH),
            "time_step_hours": current_app.config.get("TIME_STEP_HOURS", schema.TIME_STEP_HOURS),
            "forecast_hours": list(current_app.config.get("FORECAST_HOURS", list(schema.FORECAST_HOURS))),
            "key_forecast_hours": list(
                current_app.config.get("KEY_FORECAST_HOURS", list(schema.KEY_FORECAST_HOURS))
            ),
            "intensity_thresholds": threshold_table(),
            "focus_area": current_app.config.get("FOCUS_AREA"),
            "limits": {
                "max_upload_mb": current_app.config.get("MAX_UPLOAD_MB"),
                "max_model_mb": current_app.config.get("MAX_MODEL_MB"),
                "csv_columns": list(schema.REQUIRED_CSV_COLUMNS),
            },
            "security": {
                "session_idle_minutes": current_app.config.get("SESSION_IDLE_MINUTES"),
                "login_max_failures": current_app.config.get("LOGIN_MAX_FAILURES"),
                "password_min_length": current_app.config.get("PASSWORD_MIN_LENGTH"),
            },
        }
    )


@bp.get("/model-status")
@login_required
def model_status():
    """当前推理器状态（不含敏感信息，普通用户也可查看自己任务所用的模型标识）。"""
    return success(get_registry().status())


__all__ = ["bp", "DISCLAIMER"]
