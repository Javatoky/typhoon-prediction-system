"""模型注册表（9.2 部署与运行过程、11.6）。

- 进程启动或管理员切换版本时加载模型，避免每次请求重复读盘。
- 新模型试加载失败时不得替换当前模型，继续使用旧模型，保证系统可用。
- 没有任何已启用模型时降级为持续性基线，使系统仍可完整走通主流程。
"""

from __future__ import annotations

import logging

from app.ml import schema
from app.ml.forecaster import (
    Forecaster,
    KIND_BASELINE,
    build_baseline_forecaster,
    build_transformer_forecaster,
)

logger = logging.getLogger(__name__)


class ModelRegistry:
    """按 app 实例保存当前推理器。"""

    def __init__(self) -> None:
        self._forecaster: Forecaster | None = None
        self._source: str | None = None  # database / baseline / test
        self._error: str | None = None

    # ------------------------------------------------------------------
    @property
    def current(self) -> Forecaster:
        """返回当前推理器；首次访问时自动加载。"""
        if self._forecaster is None:
            self.load_active()
        assert self._forecaster is not None
        return self._forecaster

    @property
    def source(self) -> str | None:
        return self._source

    @property
    def load_error(self) -> str | None:
        return self._error

    def status(self) -> dict:
        forecaster = self.current
        return {
            **forecaster.describe(),
            "source": self._source,
            "load_error": self._error,
            "degraded": forecaster.kind == KIND_BASELINE,
        }

    # ------------------------------------------------------------------
    def load_active(self) -> Forecaster:
        """按数据库中的 is_active 记录加载模型。"""
        from app.models import ModelVersion

        self._error = None
        try:
            record = (
                ModelVersion.query.filter_by(is_active=True)
                .order_by(ModelVersion.id.desc())
                .first()
            )
        except Exception as exc:  # pragma: no cover - 数据库不可用属于部署问题
            logger.warning("读取模型版本失败，降级为基线：%s", exc)
            self._error = str(exc)
            return self._install_baseline("模型元数据读取失败。")

        if record is None:
            return self._install_baseline("尚未上传并启用任何模型。")

        if record.kind == KIND_BASELINE or not record.weights_path:
            return self._install_baseline(
                f"当前启用的是内置基线（{record.name} {record.version}）。"
            )

        try:
            import json

            hyperparams = {}
            if record.hyperparams_json:
                hyperparams = json.loads(record.hyperparams_json)
            forecaster = build_transformer_forecaster(
                weights_path=record.weights_path,
                hyperparams=hyperparams,
                scaler_path=record.scaler_path,
                label=f"{record.name} {record.version}",
                model_version_id=record.id,
                training_data_version=record.training_data_version,
            )
        except Exception as exc:
            message = str(exc)[:500]
            logger.error("加载当前模型失败，继续使用上一个可用模型：%s", message)
            self._error = message
            if self._forecaster is not None:
                return self._forecaster
            return self._install_baseline(f"当前模型加载失败：{message}")

        self._forecaster = forecaster
        self._source = "database"
        logger.info("已加载模型：%s（id=%s）", forecaster.label, record.id)
        return forecaster

    def set_forecaster(self, forecaster: Forecaster, *, source: str = "test") -> Forecaster:
        """直接注入推理器（测试或离线脚本使用）。"""
        self._forecaster = forecaster
        self._source = source
        self._error = None
        return forecaster

    def reset(self) -> None:
        self._forecaster = None
        self._source = None
        self._error = None

    def _install_baseline(self, note: str) -> Forecaster:
        forecaster = build_baseline_forecaster(note=note)
        self._forecaster = forecaster
        self._source = "baseline"
        logger.info("使用持续性基线推理：%s", note)
        return forecaster


def get_registry(app=None) -> ModelRegistry:
    """从 Flask app 或当前应用上下文中取得注册表。"""
    if app is None:
        from flask import current_app

        app = current_app
    registry = app.extensions.get("model_registry")
    if registry is None:
        registry = ModelRegistry()
        app.extensions["model_registry"] = registry
    return registry


__all__ = ["ModelRegistry", "get_registry", "schema"]
