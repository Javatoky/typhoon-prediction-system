"""项目集中配置。

设计要点（对应 NFR-009 / NFR-010）：
- 所有路径、阈值、限制都集中在本文件与 instance/config.py，页面与业务代码不得硬编码。
- 优先级：真实操作系统环境变量 > backend/.env > instance/config.py > 本文件默认值。
- 需求编号溯源：每个配置项在注释中标注对应的 FR/NFR/UC 编号。
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

try:  # python-dotenv 为运行期依赖，但允许缺失以便最小化环境也能启动
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - 仅在未安装依赖时触发
    def load_dotenv(*_args, **_kwargs):  # type: ignore[misc]
        return False

# ---------------------------------------------------------------------------
# 目录常量
# ---------------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
MODELS_OUTPUT_DIR = DATA_DIR / "models"
SAMPLES_DIR = DATA_DIR / "samples"

INSTANCE_DIR = BACKEND_DIR / "instance"
UPLOAD_DIR = INSTANCE_DIR / "uploads"
MODEL_STORE_DIR = INSTANCE_DIR / "models"
EXPORT_DIR = INSTANCE_DIR / "exports"
LOG_DIR = INSTANCE_DIR / "logs"
FRONTEND_DIST_DIR = BACKEND_DIR / "app" / "static" / "app"

# .env 必须在使用 os.environ 之前加载
load_dotenv(BACKEND_DIR / ".env")


def _env(key: str, default: str) -> str:
    value = os.environ.get(key)
    return default if value is None or value == "" else value


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(key: str, default: list[str]) -> list[str]:
    raw = os.environ.get(key)
    if raw is None or raw.strip() == "":
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


class Config:
    """全部环境共用的基础配置。"""

    # --- 运行模式 ---
    ENV_NAME = _env("DSH_APP_ENV", "development")
    DEBUG = False
    TESTING = False

    # --- 会话与安全（NFR-008） ---
    SECRET_KEY = _env("SECRET_KEY", "dev-only-change-me")
    # 需求：会话 30 分钟无操作失效。使用 Flask 的 PERMANENT_SESSION_LIFETIME 实现滑动过期。
    SESSION_IDLE_MINUTES = _env_int("SESSION_IDLE_MINUTES", 30)
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=_env_int("SESSION_IDLE_MINUTES", 30))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False  # 生产走 SSH 端口转发（http），如需 https 可置 True
    SESSION_REFRESH_EACH_REQUEST = True  # 每次请求刷新有效期 => 空闲超时才失效

    # 登录失败锁定策略（UC-01 扩展流程）
    LOGIN_MAX_FAILURES = _env_int("LOGIN_MAX_FAILURES", 5)
    LOGIN_LOCK_MINUTES = _env_int("LOGIN_LOCK_MINUTES", 15)

    # 密码策略（UC-06、NFR-008）
    PASSWORD_MIN_LENGTH = _env_int("PASSWORD_MIN_LENGTH", 8)
    PASSWORD_HASH_METHOD = _env("PASSWORD_HASH_METHOD", "pbkdf2:sha256:600000")

    # --- 文件上传限制（FR-004 / FR-014 / NFR-004） ---
    MAX_UPLOAD_MB = _env_int("MAX_UPLOAD_MB", 20)
    MAX_MODEL_MB = _env_int("MAX_MODEL_MB", 200)
    MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024
    ALLOWED_UPLOAD_SUFFIXES = _env_list("ALLOWED_UPLOAD_SUFFIXES", [".csv"])
    ALLOWED_MODEL_SUFFIXES = _env_list("ALLOWED_MODEL_SUFFIXES", [".pt", ".pth"])
    ALLOWED_METADATA_SUFFIXES = _env_list("ALLOWED_METADATA_SUFFIXES", [".json"])
    UPLOAD_CHUNK_SIZE = 1024 * 1024

    # --- 数据库（NFR-010：路径可配置） ---
    SQLALCHEMY_DATABASE_URI = _env(
        "DATABASE_URL", f"sqlite:///{(INSTANCE_DIR / 'typhoon.sqlite').as_posix()}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # --- 目录（统一在 app/__init__.py 中创建） ---
    INSTANCE_DIR = str(INSTANCE_DIR)
    UPLOAD_DIR = str(UPLOAD_DIR)
    MODEL_STORE_DIR = str(MODEL_STORE_DIR)
    EXPORT_DIR = str(EXPORT_DIR)
    LOG_DIR = str(LOG_DIR)
    FRONTEND_DIST_DIR = str(FRONTEND_DIST_DIR)

    # --- 日志（FR-015 的补充：运行日志与审计日志分离） ---
    LOG_LEVEL = _env("LOG_LEVEL", "INFO")
    LOG_FILE = _env("LOG_FILE", "")
    LOG_MAX_BYTES = _env_int("LOG_MAX_BYTES", 5 * 1024 * 1024)
    LOG_BACKUP_COUNT = _env_int("LOG_BACKUP_COUNT", 5)

    # --- 服务启动 ---
    HOST = _env("HOST", "127.0.0.1")
    PORT = _env_int("PORT", 5000)
    WAITRESS_THREADS = _env_int("WAITRESS_THREADS", 4)

    # --- CORS（前端 Vite 开发服务器跨端口调试用；同源部署时无需开启） ---
    DEV_CORS_ORIGINS = _env_list(
        "DEV_CORS_ORIGINS", ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    # ------------------------------------------------------------------
    # 业务与算法配置（模块 3 数据需求、模块 11 算法分析）
    # ------------------------------------------------------------------
    # 数据字典字段顺序（3.4 / 11.3）：标准化器与模型输入输出都依赖该顺序，不得随意调整
    FEATURE_ORDER = ["lat", "lon", "max_wind", "central_pressure"]
    FEATURE_DIM = len(FEATURE_ORDER)
    FEATURE_UNITS = {"lat": "degree", "lon": "degree", "max_wind": "m/s", "central_pressure": "hPa"}

    # 时序窗口（3.2 / 11.1）
    INPUT_LENGTH = _env_int("INPUT_LENGTH", 5)
    OUTPUT_LENGTH = _env_int("OUTPUT_LENGTH", 12)
    TIME_STEP_HOURS = _env_int("TIME_STEP_HOURS", 6)
    FORECAST_HOURS = [6 * (i + 1) for i in range(_env_int("OUTPUT_LENGTH", 12))]
    KEY_FORECAST_HOURS = [6, 12, 24, 48, 72]
    MAX_FORECAST_HOUR = TIME_STEP_HOURS * OUTPUT_LENGTH

    # 物理与合理范围（DQ-02 / DQ-03 / 11.6 物理范围修正）
    LAT_RANGE = (-90.0, 90.0)
    LON_RANGE = (-180.0, 180.0)  # 入库前统一转换为 -180~180
    LON_SOURCE_RANGE = (0.0, 360.0)  # IBTrACS 原始经度口径
    # 上限依据实测（docs/data-card.md D-08）：WP 子集 USA_WIND 换算后最大 95.17 m/s
    WIND_RANGE_MPS = (0.0, 110.0)
    PRESSURE_RANGE_HPA = (850.0, 1020.0)

    # 训练/展示区域（3.1：训练用整个 WP 盆地，展示重点关注中国近海）
    BASIN_PRIMARY = "WP"
    FOCUS_AREA = {"lon_min": 105.0, "lon_max": 135.0, "lat_min": 5.0, "lat_max": 45.0}

    # 单位换算（3.3 / 11.3）
    KNOT_TO_MPS = 0.514444

    # 数据集划分比例（DQ-06：按 SID 划分，禁止同一台风跨集合）
    SPLIT_RATIOS = {"train": 0.7, "val": 0.15, "test": 0.15}
    RANDOM_SEED = _env_int("RANDOM_SEED", 20250901)

    # 强度等级阈值（GB/T 19201-2006，按最大持续风速 m/s，闭区间下界）
    # 注意：本表用于教学展示，报告中必须注明等级依据与风速平均时段口径。
    INTENSITY_LEVELS = [
        {"code": "TD", "name_cn": "热带低压", "min_wind_mps": 10.8},
        {"code": "TS", "name_cn": "热带风暴", "min_wind_mps": 17.2},
        {"code": "STS", "name_cn": "强热带风暴", "min_wind_mps": 24.5},
        {"code": "TY", "name_cn": "台风", "min_wind_mps": 32.7},
        {"code": "STY", "name_cn": "强台风", "min_wind_mps": 41.5},
        {"code": "SuperTY", "name_cn": "超强台风", "min_wind_mps": 51.0},
    ]

    # 模型结构默认值（11.2 建议初值）
    MODEL_DEFAULTS = {
        "arch": "transformer_encoder",
        "d_model": 64,
        "nhead": 4,
        "num_encoder_layers": 2,
        "dim_feedforward": 128,
        "dropout": 0.1,
        "input_length": INPUT_LENGTH,
        "output_length": OUTPUT_LENGTH,
        "feature_dim": FEATURE_DIM,
    }
    MODEL_CACHE_KEY = "active"
    # 任务失败时保存的错误摘要长度上限（10.3：不保存堆栈等敏感信息）
    TASK_ERROR_SUMMARY_MAX_CHARS = 500

    # 上传/导入事务的批量写入行数
    DB_BULK_BATCH = 2000
    # 单次预测最多保留的历史任务查询条数（分页上限）
    PAGE_SIZE_DEFAULT = 20
    PAGE_SIZE_MAX = 100


class DevelopmentConfig(Config):
    ENV_NAME = "development"
    DEBUG = True
    SECRET_KEY = _env("SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_ECHO = False


class TestingConfig(Config):
    ENV_NAME = "testing"
    TESTING = True
    DEBUG = False
    SECRET_KEY = "testing-secret-key"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    LOGIN_MAX_FAILURES = 5
    LOGIN_LOCK_MINUTES = 15


class ProductionConfig(Config):
    ENV_NAME = "production"
    DEBUG = False
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", False)
    # 生产环境必须显式提供密钥，否则启动即报错（见 create_app 的校验）
    SECRET_KEY = _env("SECRET_KEY", "")


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(env_name: str | None = None):
    """按名称返回配置类。"""
    name = env_name or os.environ.get("DSH_APP_ENV", "development")
    return CONFIG_MAP.get(name, DevelopmentConfig)


def ensure_directories(config=Config) -> list[Path]:
    """创建运行期所需目录，返回实际创建的目录列表。"""
    created: list[Path] = []
    for raw in (
        config.INSTANCE_DIR,
        config.UPLOAD_DIR,
        config.MODEL_STORE_DIR,
        config.EXPORT_DIR,
        config.LOG_DIR,
        str(RAW_DATA_DIR),
        str(PROCESSED_DATA_DIR),
        str(MODELS_OUTPUT_DIR),
        str(SAMPLES_DIR),
    ):
        path = Path(raw)
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created.append(path)
    return created
