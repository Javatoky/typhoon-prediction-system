"""IBTrACS → 训练样本构建（11.3 数据预处理、DQ-06 按台风划分）。

流程：
1. 读取 IBTrACS v4 CSV（或本项目标准 CSV），筛选 BASIN=WP；
2. 按 SID 分组、按时间排序，统一经纬度与单位（knot → m/s）；
3. 去重、越界值剔除并保留质量标志；
4. 按 6 小时连续性切分连续段，段内滑动切出 (X: 5×4, Y: 12×4) 样本；
5. 用训练集均值和标准差拟合标准化器，验证/测试复用同一样化器；
6. 落盘 npz + scaler.json + dataset_meta.json，保证实验可复现。

用法：
    python training/prepare_data.py --input data/raw/ibtracs.WP.list.v04r01.csv
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
for path in (str(BACKEND_DIR),):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.ml import schema  # noqa: E402
from app.ml.preprocess import FeatureScaler  # noqa: E402
from app.utils.units import KNOT_TO_MPS, normalize_lat, normalize_lon  # noqa: E402
from training.config import TrainConfig  # noqa: E402

logger = logging.getLogger(__name__)


@dataclass
class SplitBundle:
    x_train: np.ndarray
    y_train: np.ndarray
    x_val: np.ndarray
    y_val: np.ndarray
    x_test: np.ndarray
    y_test: np.ndarray
    train_sids: list[str]
    val_sids: list[str]
    test_sids: list[str]
    scaler: FeatureScaler
    stats: dict


# ---------------------------------------------------------------------------
# 读取与清洗
# ---------------------------------------------------------------------------
def detect_wind_unit(raw_columns: list[str]) -> str:
    """判断风速列单位：本项目标准列是 m/s，IBTrACS 原始 USA_WIND/WMO_WIND 是 knot。

    必须用**归一化之前的原始表头**判断：归一化会把 USA_WIND 映射成 max_wind_mps，
    届时已无法区分"标准格式的 m/s 列"与"IBTrACS 的 knot 列"。
    这个区分很关键——弄错会让风速整体偏差 1.94 倍（knot 未换算成 m/s）。
    """
    lowered = {str(column).strip().lower() for column in raw_columns}
    if "max_wind_mps" in lowered or "wind_mps" in lowered:
        return "mps"
    # 各家机构的原始风速列均为 knot（IBTrACS 文档：USA_WIND 单位为 kts）
    agency_columns = {
        "usa_wind",
        "wmo_wind",
        "cma_wind",
        "hko_wind",
        "tokyo_wind",
        "newdelhi_wind",
        "reunion_wind",
        "bom_wind",
        "nadi_wind",
        "wellington_wind",
        "ds824_wind",
        "td9636_wind",
        "td9635_wind",
        "mlc_wind",
        "neumann_wind",
    }
    if lowered & agency_columns:
        return "kt"
    # 兜底：无法识别时按 knot 处理并给出提示（宁可换算也不要漏换算）
    logger.warning("无法从表头判断风速单位，按 knot 处理；若来源是 m/s 请检查数据格式。")
    return "kt"


# IBTrACS 各机构的同义列：如果沿用"先到先得"的别名映射，
# 会因为列序问题把 USA_WIND 错认成 WMO_WIND、把 USA_PRES 错认成 TOKYO_PRES。
# 因此这里用**显式映射**，不依赖别名的匹配顺序。
AGENCY_COLUMN_MAP: dict[str, str] = {
    "SID": "sid",
    "SEASON": "season",
    "BASIN": "basin",
    "SUBBASIN": "subbasin",
    "NAME": "name",
    "ISO_TIME": "iso_time",
    "NATURE": "nature",
    "LAT": "lat",
    "LON": "lon",
    "TRACK_TYPE": "track_type",
    "DIST2LAND": "dist2land",
}


def build_rename_map(raw_columns: list[str]) -> tuple[dict[str, str], list[str]]:
    """构造列名映射：机构同义列按冻结口径（docs/data-card.md D-01/D-02）显式指定。

    返回 (rename 映射, 冲突说明列表)。
    """
    rename: dict[str, str] = {}
    notes: list[str] = []
    upper_to_raw = {str(column).strip().upper(): column for column in raw_columns}

    # 1) 通用列（无歧义）
    for upper, canonical in AGENCY_COLUMN_MAP.items():
        raw = upper_to_raw.get(upper)
        if raw is not None:
            rename[raw] = canonical

    # 2) 风速列：只取冻结口径指定的那一列；其余机构的列**不映射**（留给交叉校验）
    wind_upper = schema.PRIMARY_WIND_COLUMN.upper()
    wind_raw = upper_to_raw.get(wind_upper)
    if wind_raw is not None:
        rename[wind_raw] = "max_wind_mps"
        others = [
            column
            for upper, column in upper_to_raw.items()
            if upper.endswith("_WIND") and upper != wind_upper
        ]
        if others:
            notes.append(
                f"风速列按 D-01 采用 {wind_upper}；未映射其他机构列 {len(others)} 个（可用于交叉校验）"
            )

    # 3) 气压列：主列映射为 pressure_hpa，回退列映射为 pressure__<机构>__ 前缀，避免同名冲突
    pres_upper = schema.PRIMARY_PRESSURE_COLUMN.upper()
    pres_raw = upper_to_raw.get(pres_upper)
    if pres_raw is not None:
        rename[pres_raw] = "pressure_hpa"
    for fallback in schema.FALLBACK_PRESSURE_COLUMNS:
        raw = upper_to_raw.get(fallback.upper())
        if raw is not None and raw not in rename:
            # 回退列用独立名字保存，按 D-02 的顺序在主列缺失时填补
            rename[raw] = f"pressure_fallback__{fallback.upper()}"
    return rename, notes


def load_raw(path: Path) -> tuple[pd.DataFrame, str, list[str]]:
    """读取 IBTrACS CSV 并归一化列名。

    返回 (归一化后的表, 风速单位, 原始表头)。风速单位必须在归一化之前判定。
    列名映射是**显式**的（见 build_rename_map），避免多家机构同义列互相覆盖。
    """
    if not path.exists():
        raise FileNotFoundError(
            f"找不到数据文件：{path}\n"
            "请从 NOAA/NCEI 下载 IBTrACS v4 的 WP 盆地子集（ibtracs.WP.list.v04r01.csv），"
            "放到 data/raw/ 目录，或在命令中用 --input 指定路径。"
        )
    # IBTrACS 官方 CSV 第 2 行是单位行，直接读入会把单位当数据
    preview = pd.read_csv(path, nrows=3, dtype=str, low_memory=False)
    second_row = preview.iloc[0].tolist() if len(preview) else []
    has_units_row = sum(
        1
        for value in second_row
        if str(value).strip() and any(
            marker in str(value).lower()
            for marker in ("mb", "kts", "knots", "deg", "degc", "hpa", "m/s", "km", "day", "hour")
        )
    ) >= 5

    frame = pd.read_csv(
        path, low_memory=False, skiprows=[1] if has_units_row else None,
        na_values=["", " ", "NA", "NaN", "nan"],
    )
    raw_columns = list(frame.columns)
    if has_units_row:
        logger.info("检测到 IBTrACS 单位行（第 2 行），已跳过。")

    wind_unit = detect_wind_unit(raw_columns)
    logger.info("风速单位判定：%s（依据原始表头）", wind_unit)

    rename, notes = build_rename_map(raw_columns)
    frame = frame.rename(columns=rename)
    for note in notes:
        logger.info("列映射：%s", note)
    logger.info(
        "列映射结果：风速列 %s -> max_wind_mps，气压主列 %s -> pressure_hpa，回退列 %s",
        schema.PRIMARY_WIND_COLUMN,
        schema.PRIMARY_PRESSURE_COLUMN,
        [name for name in frame.columns if name.startswith("pressure_fallback__")],
    )

    required = {"sid", "iso_time", "lat", "lon", "max_wind_mps"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(
            f"数据缺少必需列：{sorted(missing)}；实际列（前 20 个）：{raw_columns[:20]}"
        )
    return frame, wind_unit, raw_columns


def clean_frame(frame: pd.DataFrame, config: TrainConfig, wind_unit: str) -> pd.DataFrame:
    """按冻结口径清洗（docs/data-card.md D-01 ~ D-08）。

    关键步骤与依据：
      D-05 盆地：按 SID 整体保留（不做按行过滤，避免截断跨 180° 的轨迹）
      D-04 年份：只保留 >= schema.MIN_YEAR
      D-03 采样：只保留 synoptic 时刻（00/06/12/18 UTC），禁止插值降频
      D-02 气压：主列缺失时按 FALLBACK_PRESSURE_COLUMNS 依次填补并记录来源
      D-01 风速：USA_WIND（knot）换算为 m/s
    """
    frame = frame.copy()
    frame = frame[frame["sid"].notna()]
    frame["sid"] = frame["sid"].astype(str).str.strip()
    frame = frame[frame["sid"] != ""]

    # --- D-05：按 SID 整体判定盆地（先算每个台风涉及哪些盆地，再整体取舍）---
    if "basin" in frame.columns:
        basin = frame["basin"].astype(str).str.upper().str.strip()
        frame["_basin"] = basin
        target = config.basin.upper()
        sids_in_target = set(frame.loc[frame["_basin"] == target, "sid"])
        # 没有盆地信息的台风（全部为空/NaN）予以保留，交由后续条件筛选
        unknown_basin_sids = set(
            frame.loc[frame["_basin"].isin({"", "NAN", "NONE"}), "sid"]
        )
        keep_sids = sids_in_target | unknown_basin_sids
        dropped = frame["sid"].nunique() - len(keep_sids)
        if dropped:
            logger.info("D-05 按 SID 剔除不属于 %s 的台风 %d 个", target, dropped)
        frame = frame[frame["sid"].isin(keep_sids)]

    frame["iso_time"] = pd.to_datetime(frame["iso_time"], errors="coerce", utc=True)
    frame = frame[frame["iso_time"].notna()]
    frame["iso_time"] = frame["iso_time"].dt.tz_localize(None)

    # --- D-04：年份下限 ---
    before = frame["sid"].nunique()
    frame = frame[frame["iso_time"].dt.year >= schema.MIN_YEAR]
    after = frame["sid"].nunique()
    if before != after:
        logger.info("D-04 年份下限 %d：台风数 %d → %d", schema.MIN_YEAR, before, after)

    # --- D-03：synoptic 时刻过滤（不插值）---
    rows_before = len(frame)
    if config.synoptic_only:
        frame = frame[frame["iso_time"].dt.hour.isin(schema.SYNOPTIC_HOURS)]
    logger.info(
        "D-03 synoptic 重采样%s：记录 %d → %d（保留 %.1f%%）",
        "" if config.synoptic_only else "（已关闭）",
        rows_before,
        len(frame),
        len(frame) / max(rows_before, 1) * 100,
    )

    frame["lat"] = pd.to_numeric(frame["lat"], errors="coerce")
    frame["lon"] = pd.to_numeric(frame["lon"], errors="coerce")
    frame = frame[frame["lat"].notna() & frame["lon"].notna()]
    frame = frame[frame["lat"].between(*schema.LAT_RANGE)]
    frame["lon"] = frame["lon"].map(normalize_lon)
    frame = frame[frame["lon"].notna()]
    logger.info("经纬度过滤后：%d 行 / %d 台风", len(frame), frame["sid"].nunique())

    # --- D-06：性质过滤（只保留热带气旋）---
    if "nature" in frame.columns:
        nature = frame["nature"].astype(str).str.strip()
        before_nature = len(frame)
        frame = frame[nature == schema.TRAINING_NATURE]
        logger.info(
            "D-06 NATURE==%s：%d → %d 行", schema.TRAINING_NATURE, before_nature, len(frame)
        )

    # --- D-01：风速（knot → m/s）---
    if "max_wind_mps" in frame.columns:
        frame["max_wind_mps"] = pd.to_numeric(frame["max_wind_mps"], errors="coerce")
    else:
        frame["max_wind_mps"] = np.nan
    if wind_unit == "kt":
        frame["max_wind_mps"] = frame["max_wind_mps"] * KNOT_TO_MPS
    frame["max_wind_mps"] = frame["max_wind_mps"].where(
        frame["max_wind_mps"].between(*schema.WIND_RANGE_MPS)
    )
    logger.info(
        "D-01 风速（%s，原始单位 %s）可用：%d 行",
        schema.PRIMARY_WIND_COLUMN,
        wind_unit,
        int(frame["max_wind_mps"].notna().sum()),
    )

    # --- D-02：气压主列 + 回退列填补，并记录来源 ---
    primary_column = "pressure_hpa"
    if primary_column in frame.columns:
        frame["central_pressure_hpa"] = pd.to_numeric(frame[primary_column], errors="coerce")
    else:
        frame["central_pressure_hpa"] = np.nan
    frame["pressure_source"] = np.where(
        frame["central_pressure_hpa"].notna(), "primary", ""
    )

    for fallback in schema.FALLBACK_PRESSURE_COLUMNS:
        # 显式映射后的列名形如 pressure_fallback__WMO_PRES
        candidate_column = f"pressure_fallback__{fallback.upper()}"
        if candidate_column not in frame.columns:
            continue
        candidate = pd.to_numeric(frame[candidate_column], errors="coerce").where(
            lambda series: series.between(*schema.PRESSURE_RANGE_HPA)
        )
        need = frame["central_pressure_hpa"].isna() & candidate.notna()
        filled = int(need.sum())
        if filled:
            frame.loc[need, "central_pressure_hpa"] = candidate[need]
            frame.loc[need, "pressure_source"] = f"fallback:{fallback}"
            logger.info("D-02 气压回退：%s 填补 %d 个点", fallback, filled)

    frame["central_pressure_hpa"] = frame["central_pressure_hpa"].where(
        frame["central_pressure_hpa"].between(*schema.PRESSURE_RANGE_HPA)
    )
    logger.info(
        "D-02 气压可用：%d 行（其中主列 %d 行）",
        int(frame["central_pressure_hpa"].notna().sum()),
        int((frame["pressure_source"] == "primary").sum()),
    )

    if "name" in frame.columns:
        frame["name"] = frame["name"].astype(str).str.strip().replace({"nan": ""})

    # 同一 SID 同一时刻去重，保留首个非空值
    frame = frame.sort_values(["sid", "iso_time"])
    frame = frame.drop_duplicates(subset=["sid", "iso_time"], keep="first")

    # 训练样本要求四特征齐全且时间连续（DQ-05：不做插值）
    before_dropna = len(frame)
    frame = frame.dropna(subset=["max_wind_mps", "central_pressure_hpa"])
    logger.info(
        "四特征齐全过滤：%d → %d 行（损失 %d 行，占 %.1f%%）",
        before_dropna,
        len(frame),
        before_dropna - len(frame),
        (before_dropna - len(frame)) / max(before_dropna, 1) * 100,
    )
    frame = frame.reset_index(drop=True)
    logger.info(
        "清洗完成：%d 个台风、%d 个可用观测点", frame["sid"].nunique(), len(frame)
    )
    return frame


# ---------------------------------------------------------------------------
# 连续段切分与滑窗
# ---------------------------------------------------------------------------
def split_continuous_segments(frame: pd.DataFrame, step_hours: int) -> list[pd.DataFrame]:
    segments: list[pd.DataFrame] = []
    for _sid, group in frame.groupby("sid", sort=False):
        group = group.sort_values("iso_time")
        deltas = group["iso_time"].diff().dt.total_seconds().div(3600.0)
        breaks = (deltas != step_hours) | deltas.isna()
        group = group.assign(_segment=breaks.cumsum())
        for _segment, chunk in group.groupby("_segment"):
            if len(chunk) >= schema.INPUT_LENGTH + 1:
                segments.append(chunk)
    return segments


def build_samples(segments: list[pd.DataFrame], config: TrainConfig) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    xs: list[np.ndarray] = []
    ys: list[np.ndarray] = []
    sids: list[str] = []
    end_times: list[np.datetime64] = []
    window = config.input_length + config.output_length
    for chunk in segments:
        values = chunk[list(schema.FEATURE_COLUMNS)].to_numpy(dtype=np.float32)
        sid = str(chunk["sid"].iloc[0])
        times = chunk["iso_time"].to_numpy()
        for start in range(0, len(chunk) - window + 1):
            xs.append(values[start : start + config.input_length])
            ys.append(values[start + config.input_length : start + window])
            sids.append(sid)
            end_times.append(times[start + config.input_length - 1])
    if not xs:
        raise ValueError(
            "没有构造出任何样本：请确认数据中存在连续 "
            f"{config.input_length + config.output_length} 个 6 小时观测点（当前只找到 {len(segments)} 个连续段）。"
        )
    return (
        np.stack(xs),
        np.stack(ys),
        np.array(sids),
        np.array(end_times),
    )


def split_by_sid(sids: np.ndarray, config: TrainConfig) -> dict[str, np.ndarray]:
    """按台风划分集合，禁止同一台风跨集合（DQ-06）。"""
    unique = np.array(sorted(set(sids.tolist())))
    rng = np.random.default_rng(config.seed)
    rng.shuffle(unique)
    n_total = len(unique)
    if n_total < 3:
        raise ValueError(
            f"数据集只包含 {n_total} 个台风，无法同时构成训练/验证/测试集。"
            "请使用整个 WP 盆地数据（3.1：不要只截取中国近海）。"
        )
    n_train = max(1, int(round(n_total * config.train_ratio)))
    n_val = max(1, int(round(n_total * config.val_ratio)))
    if n_train + n_val >= n_total:
        n_train = max(1, n_total - 2)
        n_val = 1
    train_sids = set(unique[:n_train].tolist())
    val_sids = set(unique[n_train : n_train + n_val].tolist())
    test_sids = set(unique[n_train + n_val :].tolist())
    return {
        "train": np.array([sid in train_sids for sid in sids]),
        "val": np.array([sid in val_sids for sid in sids]),
        "test": np.array([sid in test_sids for sid in sids]),
        "train_sids": np.array(sorted(train_sids)),
        "val_sids": np.array(sorted(val_sids)),
        "test_sids": np.array(sorted(test_sids)),
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def prepare(config: TrainConfig) -> SplitBundle:
    frame, wind_unit, raw_columns = load_raw(Path(config.input_path))
    raw_row_count = int(len(frame))
    frame = clean_frame(frame, config, wind_unit)
    if frame.empty:
        raise ValueError("清洗后数据为空，请检查筛选条件与字段单位。")

    segments = split_continuous_segments(frame, config.step_hours)
    x, y, sids, _end_times = build_samples(segments, config)
    masks = split_by_sid(sids, config)

    scaler = FeatureScaler.from_numpy(x[masks["train"]].reshape(-1, schema.FEATURE_DIM))
    x_scaled = np.stack([scaler.transform_window(item) for item in x])
    y_scaled = np.stack([scaler.transform_window(item) for item in y])

    stats = {
        "raw_rows": raw_row_count,
        "cleaned_rows": int(len(frame)),
        "typhoon_count": int(frame["sid"].nunique()),
        "segment_count": len(segments),
        "sample_count": int(len(x)),
        "wind_unit_detected": wind_unit,
        "scaler_fitted_samples": scaler.fitted_sample_count,
        "feature_columns": list(schema.FEATURE_COLUMNS),
        "mean": scaler.mean,
        "std": scaler.std,
        # 冻结口径留痕（docs/data-card.md）：报告需要如实披露这些数字
        "caliber": {
            "wind_column": schema.PRIMARY_WIND_COLUMN,
            "wind_unit_source": wind_unit,
            "pressure_primary": schema.PRIMARY_PRESSURE_COLUMN,
            "pressure_fallbacks": list(schema.FALLBACK_PRESSURE_COLUMNS),
            "synoptic_hours": list(schema.SYNOPTIC_HOURS),
            "min_year": schema.MIN_YEAR,
            "wind_range_mps": list(schema.WIND_RANGE_MPS),
            "pressure_range_hpa": list(schema.PRESSURE_RANGE_HPA),
            "basin_rule": "按 SID 整体保留（不按行过滤）",
            "nature_filter": schema.TRAINING_NATURE,
        },
        "split": {
            "train": int(masks["train"].sum()),
            "val": int(masks["val"].sum()),
            "test": int(masks["test"].sum()),
        },
    }
    return SplitBundle(
        x_train=x_scaled[masks["train"]],
        y_train=y_scaled[masks["train"]],
        x_val=x_scaled[masks["val"]],
        y_val=y_scaled[masks["val"]],
        x_test=x_scaled[masks["test"]],
        y_test=y_scaled[masks["test"]],
        train_sids=masks["train_sids"].tolist(),
        val_sids=masks["val_sids"].tolist(),
        test_sids=masks["test_sids"].tolist(),
        scaler=scaler,
        stats=stats,
    )


def save(bundle: SplitBundle, config: TrainConfig) -> Path:
    processed_dir = Path(config.processed_dir)
    processed_dir.mkdir(parents=True, exist_ok=True)
    npz_path = processed_dir / "dataset.npz"
    np.savez_compressed(
        npz_path,
        x_train=bundle.x_train,
        y_train=bundle.y_train,
        x_val=bundle.x_val,
        y_val=bundle.y_val,
        x_test=bundle.x_test,
        y_test=bundle.y_test,
    )
    bundle.scaler.save(processed_dir / "scaler.json")
    meta = {
        "config": config.as_dict(),
        "stats": bundle.stats,
        "splits": {
            "train_sids": bundle.train_sids,
            "val_sids": bundle.val_sids,
            "test_sids": bundle.test_sids,
        },
    }
    (processed_dir / "dataset_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return npz_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="构建 Transformer 训练样本（IBTrACS → npz）",
        epilog="口径依据 docs/data-card.md（D-01 ~ D-08），改动口径需先更新数据卡片。",
    )
    parser.add_argument("--input", default=str(TrainConfig().input_path), help="IBTrACS 或标准格式 CSV 路径")
    parser.add_argument("--processed-dir", default=str(TrainConfig().processed_dir))
    parser.add_argument("--basin", default=TrainConfig().basin)
    parser.add_argument("--seed", type=int, default=TrainConfig().seed)
    parser.add_argument(
        "--min-year",
        type=int,
        default=None,
        help=f"年份下限（默认 {schema.MIN_YEAR}，见数据卡片 D-04）",
    )
    parser.add_argument(
        "--no-synoptic-filter",
        action="store_true",
        help="关闭 synoptic 时刻过滤（仅用于对照实验；真实数据下会导致样本数接近 0）",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S"
    )
    args = parse_args()
    config = TrainConfig(input_path=Path(args.input), processed_dir=Path(args.processed_dir))
    config.basin = args.basin
    config.seed = args.seed
    if args.min_year is not None:
        config.min_year = args.min_year
    if args.no_synoptic_filter:
        config.synoptic_only = False

    # 让 clean_frame 使用配置里的年份下限（而不是 schema 默认值）
    if config.min_year != schema.MIN_YEAR:
        schema.MIN_YEAR = config.min_year

    bundle = prepare(config)
    npz_path = save(bundle, config)
    print("数据准备完成：")
    print(f"  原始记录数        : {bundle.stats['raw_rows']:,}")
    print(f"  清洗后记录数      : {bundle.stats['cleaned_rows']:,}")
    print(f"  参与建模台风数    : {bundle.stats['typhoon_count']:,}")
    print(f"  连续段数          : {bundle.stats['segment_count']:,}")
    print(f"  样本数            : {bundle.stats['sample_count']:,}")
    print(f"  划分（train/val/test）: {bundle.stats['split']}")
    print(f"  风速列            : {bundle.stats['caliber']['wind_column']}"
          f"（原始单位 {bundle.stats['caliber']['wind_unit_source']}）")
    print(f"  气压列            : {bundle.stats['caliber']['pressure_primary']}"
          f" + 回退 {'/'.join(bundle.stats['caliber']['pressure_fallbacks'])}")
    print(f"  年份下限 / synoptic: {bundle.stats['caliber']['min_year']} / "
          f"{bundle.stats['caliber']['synoptic_hours']}")
    print(f"样本文件：{npz_path}")
    print(f"标准化器：{Path(config.processed_dir) / 'scaler.json'}")
    print(f"数据集元数据：{Path(config.processed_dir) / 'dataset_meta.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
