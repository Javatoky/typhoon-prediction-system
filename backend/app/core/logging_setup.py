"""日志初始化：控制台 + 可选滚动文件（NFR-006 稳定性排查、FR-015 日志溯源）。"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_SENSITIVE_KEYS = ("password", "passwd", "secret", "token", "dsn")


def configure_logging(app) -> None:
    """为应用配置日志。

    安全要求（6.2）：密码不得写入日志。此处提供 scrub 工具函数，
    并在请求日志中只记录方法、路径与状态码。
    """
    level = getattr(logging, str(app.config.get("LOG_LEVEL", "INFO")).upper(), logging.INFO)

    app.logger.handlers.clear()
    app.logger.setLevel(level)
    app.logger.propagate = False

    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    # Windows 控制台默认使用 GBK 代码页会把中文日志变成乱码，这里显式改为 UTF-8
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover
                pass

    console = logging.StreamHandler(stream=sys.stdout)
    console.setFormatter(formatter)
    console.setLevel(level)
    app.logger.addHandler(console)

    log_file = app.config.get("LOG_FILE") or ""
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path,
            maxBytes=int(app.config.get("LOG_MAX_BYTES", 5 * 1024 * 1024)),
            backupCount=int(app.config.get("LOG_BACKUP_COUNT", 5)),
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        app.logger.addHandler(file_handler)

    # 让 werkzeug 的开发服务器日志与业务日志格式一致
    werkzeug_logger = logging.getLogger("werkzeug")
    werkzeug_logger.setLevel(level)

    app.logger.info("日志初始化完成：level=%s file=%s", logging.getLevelName(level), log_file or "(仅控制台)")


def scrub(mapping: dict) -> dict:
    """脱敏工具：日志输出前移除敏感字段。"""
    return {
        key: ("***" if any(flag in key.lower() for flag in _SENSITIVE_KEYS) else value)
        for key, value in mapping.items()
    }


__all__ = ["configure_logging", "scrub"]
