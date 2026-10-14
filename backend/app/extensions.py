"""Flask 扩展实例集中定义，避免循环导入。

对应 9.1 分层架构：控制层（api）→ 业务层（services）→ 数据访问层（models / SQLAlchemy）。
"""

from __future__ import annotations

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

__all__ = ["db"]
