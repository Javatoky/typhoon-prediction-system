"""特征与字段口径的唯一来源（3.4 核心数据字典、附录A CSV 示例）。

任何需要知道“四个特征叫什么、CSV 表头怎么写、阈值在哪”的代码都应该引用本模块，
避免预处理、模型、校验器和前端三处各写一份。

注意：本模块不得导入 flask / sqlalchemy，以保证离线训练脚本可以独立复用。
"""

from __future__ import annotations

# 四个特征，顺序即模型输入输出张量的通道顺序，不得随意调整（11.3）
FEATURE_COLUMNS: tuple[str, ...] = ("lat", "lon", "max_wind_mps", "central_pressure_hpa")
FEATURE_ORDER: tuple[str, ...] = ("lat", "lon", "max_wind", "central_pressure")
FEATURE_DIM = len(FEATURE_COLUMNS)
FEATURE_UNITS: dict[str, str] = {
    "lat": "degree",
    "lon": "degree",
    "max_wind_mps": "m/s",
    "central_pressure_hpa": "hPa",
}
FEATURE_LABELS_CN: dict[str, str] = {
    "lat": "纬度",
    "lon": "经度",
    "max_wind_mps": "最大持续风速",
    "central_pressure_hpa": "最低中心气压",
}

# CSV 必填表头（附录A）：sid, iso_time, lat, lon, max_wind_mps, pressure_hpa
REQUIRED_CSV_COLUMNS: tuple[str, ...] = ("sid", "iso_time", "lat", "lon", "max_wind_mps", "pressure_hpa")
OPTIONAL_CSV_COLUMNS: tuple[str, ...] = ("pressure_hpa", "name", "season", "basin")

# 允许的表头别名：兼容常见写法与 IBTrACS 原始列名（导入数据集时使用）
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "sid": ("sid", "storm_id", "id", "tc_id"),
    "iso_time": ("iso_time", "time", "datetime", "date", "timestamp"),
    "lat": ("lat", "latitude", "usa_lat", "wmo_lat", "lat_dd"),
    "lon": ("lon", "longitude", "usa_lon", "wmo_lon", "lon_dd"),
    "max_wind_mps": ("max_wind_mps", "max_wind", "wind_mps", "wind", "usa_wind", "wmo_wind"),
    "pressure_hpa": (
        "pressure_hpa",
        "central_pressure",
        "pressure",
        "pres",
        "min_pressure",
        "usa_pres",
        "wmo_pres",
    ),
    "name": ("name", "storm_name", "typhoon_name"),
    "season": ("season", "year"),
    "basin": ("basin", "usa_basin", "wmo_basin"),
    # IBTrACS 原始列：NATURE（性质）、SUBBASIN（子盆地）、TRACK_TYPE（轨迹类型）
    "nature": ("nature", "usa_nature"),
    "subbasin": ("subbasin", "usa_subbasin"),
    "track_type": ("track_type", "usa_track_type"),
    "dist2land": ("dist2land",),
}

# 时序窗口默认值（3.2）
INPUT_LENGTH = 5
OUTPUT_LENGTH = 12
TIME_STEP_HOURS = 6
FORECAST_HOURS: tuple[int, ...] = tuple(TIME_STEP_HOURS * (i + 1) for i in range(OUTPUT_LENGTH))
KEY_FORECAST_HOURS: tuple[int, ...] = (6, 12, 24, 48, 72)

# 数据质量规则阈值（DQ-02 / DQ-03 / DQ-04 / DQ-05）
LAT_RANGE = (-90.0, 90.0)
LON_RANGE = (-180.0, 180.0)
# 上限依据实测（docs/data-card.md D-08）：WP 子集 USA_WIND 换算后最大 95.17 m/s，
# 原上限 62 m/s 过紧会误删真实极值；110 m/s 既容纳极值又能拦截单位错误。
WIND_RANGE_MPS = (0.0, 110.0)
PRESSURE_RANGE_HPA = (850.0, 1020.0)
MAX_INTERPOLATE_GAP = 1  # 单个缺口最多允许补 1 个点（即两点间隔 12 小时）
SUSPICIOUS_JUMP_KM_PER_6H = 500.0  # 6 小时位移超过该值提示可疑（用于质量报告，不阻断）
# 风速超过该值即认为原始单位很可能是 knot（有观测记录以来最强台风的中心风速约 60~65 m/s），
# 此时自动按 knot 换算为 m/s 并给出警告（DQ-03）。
KNOT_AUTO_THRESHOLD_MPS = 65.0
KNOT_MAX_PLAUSIBLE = 260.0

# ---------------------------------------------------------------------------
# 数据集构建口径（冻结依据：docs/data-card.md D-01 ~ D-07）
# ---------------------------------------------------------------------------
# D-01 风速列：USA_WIND 覆盖率最高（54.42%），单位 knot
PRIMARY_WIND_COLUMN = "USA_WIND"
WIND_COLUMN_UNIT = "kt"
# D-02 气压列：USA_PRES 为主，缺失时按顺序回退填补（样本量 6,253 -> 6,584，见数据卡片第 5 节缺陷 3）
PRIMARY_PRESSURE_COLUMN = "USA_PRES"
FALLBACK_PRESSURE_COLUMNS: tuple[str, ...] = ("WMO_PRES", "CMA_PRES", "HKO_PRES")
# D-03 取样口径：只保留 synoptic 时刻，禁止插值降频
SYNOPTIC_HOURS: tuple[int, ...] = (0, 6, 12, 18)
# D-04 年份下限：2000 年以前无可用样本（1960s/1970s/1990s 合计 0）
MIN_YEAR = 2000
# D-05 跨盆地：按 SID 整体取舍，保留生成于 WP 台风的全部记录
ALLOW_MULTI_BASIN_TRACK = True
# D-06 性质过滤：仅热带气旋
TC_NATURE = "TS"
TRAINING_NATURE = TC_NATURE


def feature_dict_from_values(values) -> dict[str, float]:
    """把长度 4 的序列映射为 {特征列名: 值}。"""
    if len(values) != FEATURE_DIM:
        raise ValueError(f"特征向量长度必须为 {FEATURE_DIM}，实际为 {len(values)}")
    return {name: float(value) for name, value in zip(FEATURE_COLUMNS, values)}


def canonical_column(name: str) -> str | None:
    """把任意表头名归一化为标准列名，无法识别返回 None。

    注意：IBTrACS 的 usa_wind 单位为 knot，需要调用方额外换算，
    这里只做列名归一化，不猜测单位。
    """
    key = str(name).strip().lower().replace(" ", "_").replace("-", "_")
    for canonical, aliases in COLUMN_ALIASES.items():
        if key in aliases:
            return canonical
    return None


__all__ = [
    "FEATURE_COLUMNS",
    "FEATURE_ORDER",
    "FEATURE_DIM",
    "FEATURE_UNITS",
    "FEATURE_LABELS_CN",
    "REQUIRED_CSV_COLUMNS",
    "OPTIONAL_CSV_COLUMNS",
    "COLUMN_ALIASES",
    "INPUT_LENGTH",
    "OUTPUT_LENGTH",
    "TIME_STEP_HOURS",
    "FORECAST_HOURS",
    "KEY_FORECAST_HOURS",
    "LAT_RANGE",
    "LON_RANGE",
    "WIND_RANGE_MPS",
    "PRESSURE_RANGE_HPA",
    "MAX_INTERPOLATE_GAP",
    "SUSPICIOUS_JUMP_KM_PER_6H",
    "KNOT_AUTO_THRESHOLD_MPS",
    "KNOT_MAX_PLAUSIBLE",
    "PRIMARY_WIND_COLUMN",
    "WIND_COLUMN_UNIT",
    "PRIMARY_PRESSURE_COLUMN",
    "FALLBACK_PRESSURE_COLUMNS",
    "SYNOPTIC_HOURS",
    "MIN_YEAR",
    "ALLOW_MULTI_BASIN_TRACK",
    "TC_NATURE",
    "TRAINING_NATURE",
    "feature_dict_from_values",
    "canonical_column",
]
