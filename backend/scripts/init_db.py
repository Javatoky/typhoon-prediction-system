"""初始化数据库：建表 + 创建初始管理员 + （可选）登记内置基线模型。

用法（在 backend 目录）：
    python scripts/init_db.py --seed
    python scripts/init_db.py --seed --admin-user admin --admin-password 'YourPassw0rd!'
    python scripts/init_db.py --reset --seed        # 危险：删除所有表后重建

说明：
- 表结构由 SQLAlchemy 模型定义，本脚本使用 db.create_all()，适合课程阶段零迁移工具的约束。
- 需要真正的迁移能力时（例如字段变更后保留数据），见 docs/development.md 的升级约定。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="初始化台风预测系统数据库")
    parser.add_argument("--seed", action="store_true", help="创建初始管理员账号与内置基线模型")
    parser.add_argument("--admin-user", default="admin")
    parser.add_argument("--admin-password", default=None, help="不提供时读取 .env 的 SEED_ADMIN_PASSWORD")
    parser.add_argument("--reset", action="store_true", help="删除所有表后重建（会丢失数据）")
    parser.add_argument("--env", dest="env_name", default=None, help="运行环境名")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    app = create_app(args.env_name)

    with app.app_context():
        if args.reset:
            confirm = input("将删除所有表并重建，数据不可恢复。输入 yes 继续：").strip().lower()
            if confirm != "yes":
                print("已取消。")
                return 1
            db.drop_all()
            print("已删除全部表。")
        db.create_all()
        print(f"建表完成：{app.config['SQLALCHEMY_DATABASE_URI']}")

        if args.seed:
            import os

            from app.services import model_service, user_service

            username = args.admin_user or os.environ.get("SEED_ADMIN_USERNAME", "admin")
            password = args.admin_password or os.environ.get("SEED_ADMIN_PASSWORD") or "Adm1n@2024"
            user, created = user_service.ensure_admin(username, password)
            print(
                f"{'已创建' if created else '已存在'}管理员账号：{user.username}"
                f"（密码：{'来自参数/.env' if created else '保持不变'}）"
            )
            baseline = model_service.ensure_baseline_version(user, activate=True)
            print(f"已登记并启用内置模型：{baseline.name} {baseline.version}")
            print("提示：请用该账号登录后立即修改密码（FR-002）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
