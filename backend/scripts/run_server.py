"""生产/联调启动入口（9.2：Waitress 监听本地端口，用户通过 SSH 端口转发访问）。

用法（在 backend 目录，先激活虚拟环境）：
    python scripts/run_server.py                    # 使用 .env / 默认配置
    python scripts/run_server.py --port 8000        # 指定端口
    DSH_APP_ENV=production python scripts/run_server.py   # 生产模式（必须设置 SECRET_KEY）

开发阶段也可以直接用 Flask 自带服务器：
    flask --app app:create_app run --debug
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app import create_app  # noqa: E402


def parse_args() -> argparse.Namespace:
    from config import Config

    parser = argparse.ArgumentParser(description="启动台风路径预测系统后端服务")
    parser.add_argument("--host", default=Config.HOST, help=f"监听地址，默认 {Config.HOST}")
    parser.add_argument("--port", type=int, default=Config.PORT, help=f"监听端口，默认 {Config.PORT}")
    parser.add_argument(
        "--threads", type=int, default=Config.WAITRESS_THREADS, help="Waitress 线程数"
    )
    parser.add_argument(
        "--dev",
        action="store_true",
        help="使用 Flask 调试服务器（仅本机开发用，不要用于部署）",
    )
    parser.add_argument(
        "--env",
        dest="env_name",
        default=None,
        help="运行环境名：development / testing / production（默认读取 DSH_APP_ENV）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    app = create_app(args.env_name)

    logging.getLogger(__name__).info(
        "启动服务：http://%s:%s（环境 %s）", args.host, args.port, app.config["ENV_NAME"]
    )
    if app.config["ENV_NAME"] == "production" and args.host in {"127.0.0.1", "localhost"}:
        app.logger.info(
            "服务仅监听本机地址，客户端需通过 SSH 端口转发访问：ssh -L %d:127.0.0.1:%d 用户名@服务器",
            args.port,
            args.port,
        )

    if args.dev:
        app.run(host=args.host, port=args.port, debug=True)
        return 0

    from waitress import serve

    serve(app, host=args.host, port=args.port, threads=args.threads, ident="typhoon-forecast")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
