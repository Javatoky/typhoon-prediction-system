"""IBTrACS 数据侦察（阶段 0：数据体检表）。

目的：在写任何预处理代码之前，先回答"实际拿到的这份文件到底有什么"。
产出一份可写进报告的证据材料，而不是一堆临时 print。

它回答这些问题：
1. 文件结构：是否两行表头（第二行为单位行）、列清单、可用/缺失的关键列
2. 规模与覆盖：记录数、台风数、年份跨度、盆地与性质分布
3. 四个目标字段的缺失率，并**按年代分段**统计（早期年份缺失极多，直接决定样本量）
4. 各机构风速列的差异（USA / CMA / HKO / WMO ...）：同台风同时刻的差值分布
5. 时间间隔真实性：同一台风相邻记录是否严格 6 小时（含 3 小时与不定时占比）
6. 经度口径：是否为 0~360
7. 样本可造潜力：按 DQ-04/DQ-06 估算能切出多少个 (5→12) 训练样本
8. 中国近海关注区占比（3.1）
9. 数据质量红旗清单（需要在阶段 1 裁决的问题）

用法（在 backend 目录）：
    python scripts/data_recon.py                                  # 默认读 data/raw/ibtracs.WP.list.v04r01.csv
    python scripts/data_recon.py --input <路径>
    python scripts/data_recon.py --no-report                      # 只打印，不写文件

产物：
    docs/data-recon-report.md      人工阅读的体检表
    data/processed/recon_summary.json   机器可读摘要（供后续脚本与报告引用）
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
for path in (str(BACKEND_DIR),):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.ml import schema  # noqa: E402
from app.utils.units import KNOT_TO_MPS  # noqa: E402

RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
DOCS_DIR = PROJECT_ROOT / "docs"
DEFAULT_INPUT = RAW_DIR / "ibtracs.WP.list.v04r01.csv"

# 输入窗口 + 输出窗口（与 app/ml/schema.py 保持一致）
WINDOW = schema.INPUT_LENGTH + schema.OUTPUT_LENGTH

# 各机构风速列候选（按 IBTrACS v04 命名习惯；缺失的会自动跳过）
WIND_COLUMNS = [
    "USA_WIND",
    "CMA_WIND",
    "HKO_WIND",
    "WMO_WIND",
    "TOKYO_WIND",
    "NEWDELHI_WIND",
    "REUNION_WIND",
    "BOM_WIND",
    "NADI_WIND",
    "WELLINGTON_WIND",
]
PRESSURE_COLUMNS = [
    "USA_PRES",
    "CMA_PRES",
    "HKO_PRES",
    "WMO_PRES",
    "TOKYO_PRES",
    "NEWDELHI_PRES",
    "REUNION_PRES",
    "BOM_PRES",
]

# 热带气旋性质代码（IBTrACS NATURE 列）
TC_NATURE = "TS"
NON_TC_NATURE = {"NR", "MX", "ET", "SS", "DS", "WV", "MD", "LO", "DB", "IN", "PT"}


def fmt(value, digits: int = 0) -> str:
    """安全格式化数字（None / 缺失标记 / 字符串都不会抛异常）。"""
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, np.integer)):
        return f"{int(value):,}"
    if isinstance(value, (float, np.floating)):
        return f"{value:,.{digits}f}"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------
def select_columns(columns: list[str]) -> tuple[list[str], int]:
    """从 174 列中挑出体检需要的列，避免把整个文件按字符串读进内存。

    返回 (需要保留的列, 跳过的列数)。各机构列按前缀匹配，
    因此不同版本的 IBTrACS（列名略有差异）也能自动适配。

    被有意排除的大体积列：R34/R50/R64 风圈半径、海面高度/波高、阵风、
    各类辅助标识（ATCF_ID / AGENCY / 定位方法）——对本次数据体检没有信息增量。
    """
    keep: list[str] = []
    prefixes = (
        "USA_",
        "CMA_",
        "HKO_",
        "TOKYO_",
        "NEWDELHI_",
        "REUNION_",
        "BOM_",
        "NADI_",
        "WELLINGTON_",
        "WMO_",
        "DS824_",
        "TD9636_",
        "TD9635_",
        "MLC_",
        "NEUMANN_",
    )
    exact = {
        "SID",
        "SEASON",
        "BASIN",
        "SUBBASIN",
        "NAME",
        "ISO_TIME",
        "NATURE",
        "SOURCE",
        "LAT",
        "LON",
        "TRACK_TYPE",
        "DIST2LAND",
        "LANDFALL",
        "STORM_SPEED",
    }
    drop_markers = (
        "_R34_",
        "_R50_",
        "_R64_",
        "_GUST",
        "_SEAHGT",
        "_SEARAD_",
        "_ATCF_ID",
        "_AGENCY",
        "_POS_METHOD",
        "_PRES_METHOD",
        "_ROCI",
        "_POCI",
        "_RMW",
        "_EYE",
        "_RECORD",
        "_TNUM",
        "_CI",
        "_DP",
        "_SSHS",
    )
    for column in columns:
        text = str(column).strip()
        upper = text.upper()
        if text not in exact and not upper.startswith(prefixes):
            continue
        if text not in exact and any(marker in upper for marker in drop_markers):
            continue
        keep.append(column)
    return keep, len(columns) - len(keep)


def read_ibtracs(
    path: Path, *, sample_rows: int = 0, light: bool = True
) -> tuple[pd.DataFrame, dict]:
    """读取 IBTrACS CSV，自动识别并剥离第二行的单位行。

    sample_rows > 0 时只读取前若干行（脚本自检用，结果不具代表性）。
    light=True 时只读取体检所需列，显著降低内存占用（174 列全量约需 1.5 GB）。
    """
    if not path.exists():
        raise FileNotFoundError(
            f"找不到数据文件：{path}\n"
            "请先执行：python scripts/fetch_ibtracs.py --basin WP"
        )

    meta: dict = {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "size_mb": round(path.stat().st_size / 1024 / 1024, 1),
        "sampled_rows": int(sample_rows or 0),
    }

    # 先只看前几行判断是否存在单位行（第二行形如 " , , ,degC,mb,..."）
    preview = pd.read_csv(path, nrows=3, dtype=str, low_memory=False)
    raw_header = list(preview.columns)
    second_row = preview.iloc[0].tolist() if len(preview) else []

    def looks_like_units(values: list) -> bool:
        markers = ("mb", "m/s", "kts", "knots", "deg", "degC", "hPa", "km", "nm", "day", "hour")
        hits = 0
        for value in values:
            text = str(value).strip().lower()
            if not text or text in {"nan", "name"}:
                continue
            if any(marker in text for marker in markers):
                hits += 1
        return hits >= 5

    has_units_row = looks_like_units(second_row)
    meta["has_units_row"] = has_units_row
    meta["column_count"] = len(raw_header)
    meta["columns"] = raw_header
    meta["units_row"] = [str(v) for v in second_row] if has_units_row else None

    usecols = None
    if light:
        keep, skipped = select_columns(raw_header)
        meta["light_columns_kept"] = keep
        meta["light_columns_skipped"] = skipped
        usecols = keep or None

    frame = pd.read_csv(
        path,
        dtype=str,
        low_memory=False,
        skiprows=[1] if has_units_row else None,
        na_values=["", " ", "NA", "NaN", "nan"],
        nrows=sample_rows or None,
        usecols=usecols,
    )
    meta["row_count"] = int(len(frame))
    return frame, meta


# ---------------------------------------------------------------------------
# 各段统计
# ---------------------------------------------------------------------------
def numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(dtype="float64", index=frame.index)
    return pd.to_numeric(frame[column], errors="coerce")


def basic_profile(frame: pd.DataFrame, meta: dict) -> dict:
    result: dict = {}
    sid = frame["SID"].astype(str).str.strip()
    result["typhoon_count"] = int(sid.nunique())
    result["sid_missing"] = int(frame["SID"].isna().sum())

    iso = pd.to_datetime(frame["ISO_TIME"], errors="coerce", utc=True)
    result["time_unparsable"] = int(iso.isna().sum())
    valid_iso = iso.dropna()
    if len(valid_iso):
        result["time_min"] = valid_iso.min().strftime("%Y-%m-%d")
        result["time_max"] = valid_iso.max().strftime("%Y-%m-%d")
        result["year_span"] = [int(valid_iso.min().year), int(valid_iso.max().year)]

    for column in ("BASIN", "NATURE", "SOURCE", "SEASON", "NAME", "TRACK_TYPE", "DIST2LAND", "LANDfall"):
        if column in frame.columns:
            counts = frame[column].astype(str).str.strip().value_counts(dropna=False)
            result[f"{column.lower()}_top"] = {
                str(key): int(value) for key, value in counts.head(12).items()
            }
    return result


def availability_profile(frame: pd.DataFrame) -> dict:
    """四个目标字段 + 各机构列的可用率。"""
    total = len(frame)
    result: dict = {"row_count": total, "fields": {}}

    probes = {
        "lat": "LAT",
        "lon": "LON",
        "wmo_wind": "WMO_WIND",
        "usa_wind": "USA_WIND",
        "usa_pres": "USA_PRES",
        "wmo_pres": "WMO_PRES",
        "nature": "NATURE",
        "basin": "BASIN",
        "sid": "SID",
        "iso_time": "ISO_TIME",
    }
    for label, column in probes.items():
        if column not in frame.columns:
            result["fields"][label] = {"column": column, "present": False}
            continue
        series = frame[column]
        if label in {"lat", "lon", "wmo_wind", "usa_wind", "usa_pres", "wmo_pres"}:
            valid = pd.to_numeric(series, errors="coerce").notna()
        else:
            valid = series.notna() & (series.astype(str).str.strip() != "")
        result["fields"][label] = {
            "column": column,
            "present": True,
            "non_null": int(valid.sum()),
            "missing": int((~valid).sum()),
            "missing_pct": round(float((~valid).sum()) / max(total, 1) * 100, 2),
        }

    # 分年代缺失率：决定"训练数据年份下限"
    iso = pd.to_datetime(frame.get("ISO_TIME"), errors="coerce", utc=True)
    decade = (iso.dt.year // 10 * 10)
    buckets: dict = {}
    for label, column in (("usa_wind", "USA_WIND"), ("wmo_wind", "WMO_WIND"), ("usa_pres", "USA_PRES")):
        if column not in frame.columns:
            continue
        valid = pd.to_numeric(frame[column], errors="coerce").notna()
        grouped = valid.groupby(decade).agg(["size", "sum"])
        buckets[label] = {
            f"{int(key)}s": {
                "rows": int(row["size"]),
                "missing_pct": round((1 - row["sum"] / max(row["size"], 1)) * 100, 2),
            }
            for key, row in grouped.iterrows()
            if pd.notna(key)
        }
    result["missing_by_decade"] = buckets
    return result


def wind_agreement_profile(frame: pd.DataFrame) -> dict:
    """各机构风速列的口径差异：同为热带气旋性质的记录，按 SID+时间对齐比较。"""
    available = [column for column in WIND_COLUMNS if column in frame.columns]
    result: dict = {"available_columns": available, "coverage": {}, "pairwise": {}}
    if "USA_WIND" not in frame.columns or len(available) < 2:
        result["note"] = "可用风速列不足 2 个，无法比较机构差异。"
        return result

    work = pd.DataFrame(
        {
            "SID": frame["SID"].astype(str).str.strip(),
            "ISO_TIME": pd.to_datetime(frame["ISO_TIME"], errors="coerce", utc=True),
        }
    )
    for column in available:
        # knot -> m/s，便于直接用国标阈值理解
        work[column] = pd.to_numeric(frame[column], errors="coerce") * KNOT_TO_MPS

    for column in available:
        series = work[column]
        result["coverage"][column] = {
            "non_null": int(series.notna().sum()),
            "non_null_pct": round(float(series.notna().mean()) * 100, 2),
            "mean_mps": round(float(series.mean()), 2) if series.notna().any() else None,
            "max_mps": round(float(series.max()), 2) if series.notna().any() else None,
        }

    # 两两比较：只在两边都有值时计算差值
    for index, left in enumerate(available):
        for right in available[index + 1 :]:
            pair = work[[left, right]].dropna()
            if pair.empty:
                result["pairwise"][f"{left}_vs_{right}"] = {"overlap": 0}
                continue
            diff = pair[left] - pair[right]
            ratio = (pair[left] / pair[right].replace(0, np.nan)).dropna()
            result["pairwise"][f"{left}_vs_{right}"] = {
                "overlap": int(len(pair)),
                "mean_diff_mps": round(float(diff.mean()), 3),
                "median_diff_mps": round(float(diff.median()), 3),
                "mean_abs_diff_mps": round(float(diff.abs().mean()), 3),
                "p95_abs_diff_mps": round(float(diff.abs().quantile(0.95)), 3),
                "median_ratio": round(float(ratio.median()), 4) if not ratio.empty else None,
                "identical_pct": round(float((diff.abs() < 1e-9).mean()) * 100, 2),
            }

    # 分强度等级看分歧（强台风段分歧更大，直接影响等级标注）
    usa = work["USA_WIND"]
    bins = [0, 10.8, 17.2, 24.5, 32.7, 41.5, 51.0, 200]
    labels = ["<10.8", "TD", "TS", "STS", "TY", "STY", "SuperTY"]
    bucket = pd.cut(usa, bins=bins, labels=labels, right=False)
    detail: dict = {}
    for column in available:
        if column == "USA_WIND":
            continue
        diff = (work["USA_WIND"] - work[column]).abs()
        grouped = diff.groupby(bucket, observed=True).agg(["count", "mean"])
        detail[column] = {
            str(key): {"n": int(row["count"]), "mean_abs_diff_mps": round(float(row["mean"]), 3)}
            for key, row in grouped.iterrows()
            if row["count"] > 0
        }
    result["by_intensity_vs_usa"] = detail
    return result


def time_interval_profile(frame: pd.DataFrame, sample_limit: int = 4000) -> dict:
    """同一台风相邻记录的时间间隔分布（DQ-01 的真实性检查）。"""
    work = pd.DataFrame(
        {
            "SID": frame["SID"].astype(str).str.strip(),
            "ISO_TIME": pd.to_datetime(frame["ISO_TIME"], errors="coerce", utc=True),
        }
    ).dropna()
    work = work.sort_values(["SID", "ISO_TIME"])
    # 抽样以控制内存（间隔分布对全量不敏感）
    sids = work["SID"].drop_duplicates()
    if len(sids) > sample_limit:
        sids = sids.sample(sample_limit, random_state=42)
        work = work[work["SID"].isin(set(sids))]
    delta_hours = work.groupby("SID")["ISO_TIME"].diff().dt.total_seconds() / 3600.0
    delta_hours = delta_hours.dropna()
    if delta_hours.empty:
        return {"note": "无有效的时间差样本。"}

    rounded = delta_hours.round(3)
    counts = rounded.value_counts().head(12)
    total = len(delta_hours)
    exact_6 = int((delta_hours.abs() - 6.0).abs().lt(1e-6).sum())
    exact_3 = int((delta_hours.abs() - 3.0).abs().lt(1e-6).sum())
    exact_1 = int((delta_hours.abs() - 1.0).abs().lt(1e-6).sum())
    exact_2 = int((delta_hours.abs() - 2.0).abs().lt(1e-6).sum())

    # 按十年看间隔结构变化：早期多为 6 小时，2000 年后 3 小时（甚至 1 小时）增多
    return {
        "sampled_typhoons": int(len(sids)),
        "interval_count": int(total),
        "exact_6h_pct": round(exact_6 / total * 100, 2),
        "exact_3h_pct": round(exact_3 / total * 100, 2),
        "exact_2h_pct": round(exact_2 / total * 100, 2),
        "exact_1h_pct": round(exact_1 / total * 100, 2),
        "top_intervals_hours": {str(float(key)): int(value) for key, value in counts.items()},
        "note": "IBTrACS 主记录以 3 小时间隔为主；DQ-01/DQ-04 要求 6 小时步长，"
        "因此必须先按 synoptic 时刻重采样（见第 7 节的两种口径对比）。",
    }


def longitude_profile(frame: pd.DataFrame) -> dict:
    lon = numeric(frame, "LON").dropna()
    if lon.empty:
        return {"note": "无有效经度。"}
    return {
        "min": round(float(lon.min()), 3),
        "max": round(float(lon.max()), 3),
        "negative_count": int((lon < 0).sum()),
        "above_180_count": int((lon > 180).sum()),
        "convention": "0-360" if (lon >= 0).all() or (lon > 180).any() else "-180-180",
    }


def _segment_stats(
    timestamps: pd.Series, *, require_exact_6h: bool
) -> tuple[int, int, int, list[int], int]:
    """把一串已排序的时间戳切分成连续段，返回 (段数, 满足窗口的段数, 样本数, 段长列表, 参与台风数)。

    require_exact_6h=True 时才要求相邻记录恰好 6 小时（重采样后应当满足）。
    """
    deltas = timestamps.diff().dt.total_seconds() / 3600.0
    if require_exact_6h:
        breaks = (deltas - 6.0).abs().gt(1e-6) | deltas.isna()
    else:
        breaks = deltas.isna()
    segment_ids = breaks.cumsum()
    total_samples = 0
    segments_ge_window = 0
    lengths: list[int] = []
    for _segment, chunk in timestamps.groupby(segment_ids):
        lengths.append(len(chunk))
        if len(chunk) >= WINDOW:
            segments_ge_window += 1
            total_samples += len(chunk) - WINDOW + 1
    return len(lengths), segments_ge_window, total_samples, lengths, 0


def sample_potential_profile(frame: pd.DataFrame, meta: dict) -> dict:
    """按 DQ-04/DQ-06 估算可切出的训练样本数（这是“数据够不够”的直接答案）。

    关键：IBTrACS 原始记录以 3 小时间隔为主（见第 4 节），
    因此这里同时给出两种口径，避免用错误口径得出“样本数为 0”的结论：
      A. 原始记录直接切 6 小时连续段（会几乎全部落空，仅作对照）
      B. 先按 synoptic 时刻（00/06/12/18 UTC）重采样，再切 6 小时连续段（推荐基线）
    """
    work = pd.DataFrame(
        {
            "SID": frame["SID"].astype(str).str.strip(),
            "ISO_TIME": pd.to_datetime(frame["ISO_TIME"], errors="coerce", utc=True),
        }
    )
    nature = frame.get("NATURE", pd.Series(index=frame.index, dtype="object")).astype(str).str.strip()
    basin = frame.get("BASIN", pd.Series(index=frame.index, dtype="object")).astype(str).str.strip()

    lat = numeric(frame, "LAT")
    lon = numeric(frame, "LON")
    # 训练用风速列先取 USA_WIND（覆盖最全），阶段 1 裁决后可换
    wind = numeric(frame, "USA_WIND") * KNOT_TO_MPS
    pressure = numeric(frame, "USA_PRES")

    # 基础过滤：逐级累加的漏斗（每一步都在上一步基础上再收紧）
    mask_time = work["ISO_TIME"].notna()
    mask_latlon = mask_time & lat.notna() & lon.notna()
    mask_wind = mask_latlon & wind.notna()
    mask_wind_valid = mask_wind & wind.between(*schema.WIND_RANGE_MPS)
    mask_pres = mask_wind_valid & pressure.notna()
    mask_nature = mask_pres & (nature == TC_NATURE)
    mask_basin = mask_nature & basin.isin({"WP", ""})
    # 最终口径：时间 + 位置 + 风速（含量程）+ 气压 + 性质 + 盆地
    work = work[mask_basin].copy()

    funnel = {
        "rows_total": int(len(frame)),
        "after_time": int(mask_time.sum()),
        "after_latlon": int(mask_latlon.sum()),
        "after_usa_wind": int(mask_wind.sum()),
        "after_wind_in_range": int(mask_wind_valid.sum()),
        f"after_usa_pres": int(mask_pres.sum()),
        "after_nature_ts": int(mask_nature.sum()),
        "after_basin_wp": int(mask_basin.sum()),
    }

    result: dict = {
        "filter_rules": [
            "ISO_TIME 可解析",
            "LAT/LON 非空",
            f"USA_WIND 非空（已按 {KNOT_TO_MPS} 换算为 m/s）",
            "USA_PRES 非空",
            f"NATURE == {TC_NATURE}（热带气旋性质）",
            "BASIN 属于 WP",
        ],
        "filter_funnel": funnel,
    }
    if work.empty:
        result["note"] = "按上述规则过滤后没有可用记录。"
        return result

    work = work.sort_values(["SID", "ISO_TIME"])

    # 口径 A：原始记录直接要求 6 小时间隔（对照，说明为什么不能这么做）
    raw_segments = raw_ge_window = raw_samples = 0
    raw_lengths: list[int] = []
    raw_typhoons = 0
    for _sid, group in work.groupby("SID"):
        segments, ge_window, samples, lengths, _ = _segment_stats(
            group["ISO_TIME"], require_exact_6h=True
        )
        raw_segments += segments
        raw_ge_window += ge_window
        raw_samples += samples
        raw_lengths.extend(lengths)
        if samples:
            raw_typhoons += 1
    raw_lengths_array = np.array(raw_lengths) if raw_lengths else np.array([0])
    result["raw_6h_interval"] = {
        "continuous_segments": raw_segments,
        "segments_ge_window": raw_ge_window,
        "estimated_samples": raw_samples,
        "typhoons_with_samples": raw_typhoons,
        "segment_length_stats": {
            "min": int(raw_lengths_array.min()),
            "median": float(np.median(raw_lengths_array)),
            "mean": round(float(raw_lengths_array.mean()), 1),
            "max": int(raw_lengths_array.max()),
        },
    }

    # 口径 B：先重采样到 synoptic 时刻（00/06/12/18 UTC），再切窗口
    resampled = work[work["ISO_TIME"].dt.hour.isin([0, 6, 12, 18])].copy()
    result["resampled_rows"] = int(len(resampled))
    result["resampled_row_ratio_pct"] = round(
        float(len(resampled)) / max(len(work), 1) * 100, 2
    )
    result["resampled_typhoons"] = int(resampled["SID"].nunique())

    total_samples = 0
    total_segments = 0
    usable_typhoons = 0
    lengths: list[int] = []
    for _sid, group in resampled.groupby("SID"):
        _segments, ge_window, samples, seg_lengths, _ = _segment_stats(
            group["ISO_TIME"], require_exact_6h=True
        )
        total_segments += ge_window
        total_samples += samples
        lengths.extend(seg_lengths)
        if samples:
            usable_typhoons += 1

    lengths_array = np.array(lengths) if lengths else np.array([0])
    result.update(
        {
            "continuous_segments": int(len(lengths)),
            "segments_ge_window": int(total_segments),
            "estimated_samples": int(total_samples),
            "typhoons_with_samples": int(usable_typhoons),
            "usable_rows": int(len(resampled)),
            "usable_typhoons": int(resampled["SID"].nunique()),
            "segment_length_stats": {
                "min": int(lengths_array.min()),
                "median": float(np.median(lengths_array)),
                "mean": round(float(lengths_array.mean()), 1),
                "max": int(lengths_array.max()),
            },
            "window_required_points": WINDOW,
            "recommended_basis": "resampled_6h（synoptic 时刻 00/06/12/18 UTC）",
        }
    )
    # 按十年估算样本分布（帮助判断年份下限的代价）
    resampled = resampled.copy()
    resampled["decade"] = (resampled["ISO_TIME"].dt.year // 10 * 10).astype(int)
    by_decade = resampled.groupby("decade")["SID"].nunique()
    result["usable_typhoons_by_decade"] = {str(int(k)): int(v) for k, v in by_decade.items()}

    # 分年代的重采样后样本量：这才是"年份下限该取哪年"的真正依据
    samples_by_decade: dict[str, int] = {}
    for decade, group in resampled.groupby("decade"):
        subtotal = 0
        for _sid, storm in group.groupby("SID"):
            _segments, _ge, samples, _lengths, _ = _segment_stats(
                storm["ISO_TIME"], require_exact_6h=True
            )
            subtotal += samples
        samples_by_decade[str(int(decade))] = subtotal
    result["estimated_samples_by_decade"] = samples_by_decade

    # 气压来源替代方案：回答"换一列气压能救回多少样本"
    result["pressure_source_tradeoff"] = pressure_source_tradeoff(frame)
    return result


def pressure_source_tradeoff(frame: pd.DataFrame) -> dict:
    """USA_PRES 缺失是否是样本量的瓶颈？

    这里测算：改用其他机构气压列（WMO_PRES / CMA_PRES / HKO_PRES）分别能救回多少台风与样本。

    **重要**：必须与冻结口径保持一致——即先应用 年份下限(D-04) + synoptic 重采样(D-03)
    + NATURE 过滤(D-06) + SID 级盆地判定(D-05)，否则会严重高估（曾因此得出 20,063 的错误结论，
    实际为 6,584）。
    """
    iso = pd.to_datetime(frame.get("ISO_TIME"), errors="coerce", utc=True)
    sid = frame["SID"].astype(str).str.strip()
    nature = frame.get("NATURE", pd.Series(index=frame.index, dtype="object")).astype(str).str.strip()
    basin = frame.get("BASIN", pd.Series(index=frame.index, dtype="object")).astype(str).str.strip()
    lat = numeric(frame, "LAT")
    lon = numeric(frame, "LON")
    wind = numeric(frame, "USA_WIND") * KNOT_TO_MPS

    # D-05：按 SID 整体判定盆地
    basin_sids = set(sid[basin == "WP"])
    common = (
        iso.notna()
        & sid.ne("")
        & lat.notna()
        & lon.notna()
        & wind.notna()
        & wind.between(*schema.WIND_RANGE_MPS)
        & (nature == TC_NATURE)
        & sid.isin(basin_sids)
        & (iso.dt.year >= schema.MIN_YEAR)  # D-04
        & iso.dt.hour.isin(schema.SYNOPTIC_HOURS)  # D-03
    )

    result: dict = {
        "note": "已应用冻结口径（年份下限 + synoptic + NATURE + SID 级盆地），"
        "只替换气压来源列；与 prepare_data.py 的差异仅在于逐点连续性要求。",
        "baseline_rows_after_common_filters": int(common.sum()),
        "sources": {},
    }
    for column in ("USA_PRES", "WMO_PRES", "CMA_PRES", "HKO_PRES", "TOKYO_PRES"):
        if column not in frame.columns:
            continue
        pressure = numeric(frame, column)
        pressure = pressure.where(pressure.between(*schema.PRESSURE_RANGE_HPA))
        mask = common & pressure.notna()
        subset = pd.DataFrame({"SID": sid[mask], "ISO_TIME": iso[mask]})
        samples = 0
        typhoons = 0
        for _sid, storm in subset.sort_values("ISO_TIME").groupby("SID"):
            _segments, _ge, subtotal, _lengths, _ = _segment_stats(
                storm["ISO_TIME"], require_exact_6h=True
            )
            samples += subtotal
            if subtotal:
                typhoons += 1
        result["sources"][column] = {
            "rows": int(mask.sum()),
            "typhoons": int(subset["SID"].nunique()),
            "estimated_samples": int(samples),
            "typhoons_with_samples": int(typhoons),
        }

    # 合并策略：USA_PRES 缺失时用 WMO_PRES / CMA_PRES / HKO_PRES 补
    if "USA_PRES" in frame.columns:
        def valid(col):
            series = numeric(frame, col)
            return series.where(series.between(*schema.PRESSURE_RANGE_HPA))

        filled = valid("USA_PRES")
        filled_from: dict[str, int] = {}
        for column in ("WMO_PRES", "CMA_PRES", "HKO_PRES"):
            if column not in frame.columns:
                continue
            candidate = valid(column)
            need = filled.isna() & candidate.notna() & common
            filled_from[column] = int(need.sum())
            # 只在 common 范围内填补，避免统计口径漂移
            filled = filled.where(~(common & filled.isna() & candidate.notna()), candidate)
        mask = common & filled.notna()
        subset = pd.DataFrame({"SID": sid[mask], "ISO_TIME": iso[mask]})
        samples = 0
        typhoons = 0
        for _sid, storm in subset.sort_values("ISO_TIME").groupby("SID"):
            _segments, _ge, subtotal, _lengths, _ = _segment_stats(
                storm["ISO_TIME"], require_exact_6h=True
            )
            samples += subtotal
            if subtotal:
                typhoons += 1
        result["merged_strategy"] = {
            "order": "USA_PRES -> WMO_PRES -> CMA_PRES -> HKO_PRES",
            "filled_rows_by_source": filled_from,
            "rows": int(mask.sum()),
            "typhoons": int(subset["SID"].nunique()),
            "estimated_samples": int(samples),
            "typhoons_with_samples": int(typhoons),
        }
        primary = result["sources"].get("USA_PRES", {})
        gain = result["merged_strategy"]["estimated_samples"] - primary.get("estimated_samples", 0)
        base = max(primary.get("estimated_samples", 0), 1)
        result["fallback_gain"] = {
            "samples_gain": gain,
            "gain_pct": round(gain / base * 100, 1),
            "verdict": "回退填补收益有限，瓶颈不在气压列本身，而在与风速的联合可用性",
        }
    return result


def basin_consistency_profile(frame: pd.DataFrame, limit: int = 12) -> dict:
    """检查同一台风是否跨多个 BASIN 标签。

    为什么重要：IBTrACS 的 WP 文件里会出现 BASIN=EP/NI 的记录。
    若同一个 SID 在不同行被标成不同盆地，按行过滤会**截断台风轨迹**，
    把一条完整轨迹切成两半，直接影响输入窗口的连续性（DQ-04）。
    """
    work = pd.DataFrame(
        {
            "SID": frame["SID"].astype(str).str.strip(),
            "BASIN": frame.get("BASIN", pd.Series(index=frame.index, dtype="object"))
            .astype(str)
            .str.strip(),
        }
    )
    per_typhoon = work.groupby("SID")["BASIN"].nunique()
    multi = per_typhoon[per_typhoon > 1]
    mixed_rows = work[work["SID"].isin(set(multi.index))]

    # 非 WP 记录最多的台风，供人工核对是否为"生成于 WP、东传越过 180°"
    non_wp = work[~work["BASIN"].isin({"WP", ""})]
    top = (
        non_wp.groupby(["SID", "BASIN"]).size().reset_index(name="rows")
        .sort_values("rows", ascending=False)
        .head(limit)
    )
    return {
        "typhoons_total": int(per_typhoon.size),
        "typhoons_with_multiple_basins": int(multi.size),
        "rows_belonging_to_multi_basin_typhoons": int(len(mixed_rows)),
        "non_wp_rows": int(len(non_wp)),
        "non_wp_typhoons": int(non_wp["SID"].nunique()) if len(non_wp) else 0,
        "top_non_wp_examples": [
            {"SID": str(row["SID"]), "BASIN": str(row["BASIN"]), "rows": int(row["rows"])}
            for _, row in top.iterrows()
        ],
        "note": "若同一 SID 出现多个 BASIN，按行过滤盆地会截断轨迹；"
        "应按 SID 决定取舍（保留该台风全部记录或整条剔除）。",
    }


def focus_area_profile(frame: pd.DataFrame) -> dict:
    """中国近海关注区占比（3.1）。"""
    lat = numeric(frame, "LAT")
    lon = numeric(frame, "LON")
    lon_norm = ((lon + 180) % 360 + 360) % 360 - 180
    in_area = (
        (lat >= 5)
        & (lat <= 45)
        & (lon_norm >= 105)
        & (lon_norm <= 135)
    ).fillna(False)
    total = int(len(frame))
    typhoons_with_area = frame.loc[in_area, "SID"].astype(str).str.strip().nunique()
    return {
        "rows_in_focus_area": int(in_area.sum()),
        "rows_in_focus_area_pct": round(float(in_area.sum()) / max(total, 1) * 100, 2),
        "typhoons_reaching_focus_area": int(typhoons_with_area),
        "area": {"lon": [105, 135], "lat": [5, 45]},
    }


def physics_profile(frame: pd.DataFrame) -> dict:
    """物理量程检查（DQ-02/DQ-03 用真实数据校准阈值）。"""
    lat = numeric(frame, "LAT")
    lon = numeric(frame, "LON")
    wind = numeric(frame, "USA_WIND") * KNOT_TO_MPS
    pressure = numeric(frame, "USA_PRES")
    result: dict = {}
    for label, series, bounds in (
        ("lat", lat, schema.LAT_RANGE),
        ("lon", lon, (-180.0, 360.0)),
        ("wind_mps", wind, schema.WIND_RANGE_MPS),
        ("pressure_hpa", pressure, schema.PRESSURE_RANGE_HPA),
    ):
        series = series.dropna()
        if series.empty:
            result[label] = {"note": "无有效值"}
            continue
        out_of_range = ((series < bounds[0]) | (series > bounds[1])).sum()
        result[label] = {
            "min": round(float(series.min()), 3),
            "p01": round(float(series.quantile(0.01)), 3),
            "median": round(float(series.median()), 3),
            "p99": round(float(series.quantile(0.99)), 3),
            "max": round(float(series.max()), 3),
            "out_of_configured_range": int(out_of_range),
            "configured_range": list(bounds),
        }
    return result


def list_suspicious_points(frame: pd.DataFrame, limit: int = 15) -> list[dict]:
    """挑出需要在阶段 1 裁决的"可疑点"样本（供人工抽查）。"""
    lat = numeric(frame, "LAT")
    lon = numeric(frame, "LON")
    wind = numeric(frame, "USA_WIND") * KNOT_TO_MPS
    pressure = numeric(frame, "USA_PRES")
    suspicious = (
        ((lon > 180) | (lon < -180))
        | (wind > schema.WIND_RANGE_MPS[1])
        | (pressure < schema.PRESSURE_RANGE_HPA[0])
        | ((pressure - wind * 1.0) > 1010)  # 气压极高但风速也大，逻辑矛盾
    ).fillna(False)
    subset = frame[suspicious].head(limit)
    rows: list[dict] = []
    for _, row in subset.iterrows():
        rows.append(
            {
                "SID": str(row.get("SID")),
                "ISO_TIME": str(row.get("ISO_TIME")),
                "LAT": str(row.get("LAT")),
                "LON": str(row.get("LON")),
                "USA_WIND_kt": str(row.get("USA_WIND")),
                "USA_WIND_mps": None
                if pd.isna(pd.to_numeric(pd.Series([row.get("USA_WIND")]), errors="coerce").iloc[0])
                else round(
                    float(pd.to_numeric(pd.Series([row.get("USA_WIND")]), errors="coerce").iloc[0])
                    * KNOT_TO_MPS,
                    2,
                ),
                "USA_PRES": str(row.get("USA_PRES")),
                "NATURE": str(row.get("NATURE")),
                "BASIN": str(row.get("BASIN")),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# 红旗清单
# ---------------------------------------------------------------------------
def build_red_flags(profile: dict) -> list[dict]:
    """把体检结果转成"需要在阶段 1 裁决的问题"，而不是让组长自己从数字里找。"""
    flags: list[dict] = []
    fields = profile.get("availability", {}).get("fields", {})

    wmo = fields.get("wmo_wind", {})
    if wmo.get("present") and wmo.get("missing_pct", 0) > 20:
        flags.append(
            {
                "level": "高",
                "topic": "WMO_WIND 缺失率高",
                "evidence": f"WMO_WIND 缺失 {wmo.get('missing_pct')}%",
                "decision": "是否仍以 WMO_WIND（10 分钟平均，与国标口径一致）为主训练列？"
                "若缺失过多，需改用 USA_WIND 并在报告中注明平均时段差异。",
            }
        )

    intervals = profile.get("time_intervals", {})
    exact6 = intervals.get("exact_6h_pct")
    if exact6 is not None and exact6 < 95:
        flags.append(
            {
                "level": "高",
                "topic": "时间间隔不是 6 小时（必须先重采样）",
                "evidence": f"严格 6 小时间隔仅占 {exact6}%"
                f"（3 小时占 {intervals.get('exact_3h_pct')}%、"
                f"2 小时占 {intervals.get('exact_2h_pct')}%、"
                f"1 小时占 {intervals.get('exact_1h_pct')}%）",
                "decision": "DQ-01/DQ-04 要求 6 小时步长。需确认采用"
                "「按 synoptic 时刻 00/06/12/18 UTC 重采样」作为唯一取样口径"
                "（体检表第 7 节已给出该口径下的样本量），"
                "并明确不采用插值降频——避免制造假观测。",
            }
        )

    # 仅保留一个"机构分歧"红旗，避免 10 条同类信息淹没真正需要决策的问题
    agreement = profile.get("wind_agreement", {})
    pairwise = agreement.get("pairwise", {})
    worst_pair = None
    for key, value in pairwise.items():
        if value.get("overlap", 0) < 1000:
            continue
        if worst_pair is None or value.get("mean_abs_diff_mps", 0) > worst_pair[1].get(
            "mean_abs_diff_mps", 0
        ):
            worst_pair = (key, value)
    if worst_pair is not None:
        key, value = worst_pair
        by_intensity = agreement.get("by_intensity_vs_usa", {})
        strongest = None
        for column, buckets in by_intensity.items():
            if column.split("_vs_")[-1] in key:
                for band, info in buckets.items():
                    if band == "SuperTY" and info.get("n", 0) > 100:
                        strongest = info
        extra = (
            f"；强台风段（SuperTY）平均绝对差达 {strongest['mean_abs_diff_mps']} m/s"
            if strongest
            else ""
        )
        flags.append(
            {
                "level": "高",
                "topic": "各机构风速口径分歧显著",
                "evidence": f"最大分歧为 {key}：重叠 {value['overlap']} 条，"
                f"平均绝对差 {value['mean_abs_diff_mps']} m/s，"
                f"完全一致仅 {value['identical_pct']}%{extra}",
                "decision": "必须选定唯一训练列（其余仅作交叉校验）。"
                "强度等级分布会随选列明显变化，该选择必须写入数据卡片并在报告中披露。",
            }
        )

    # 训练必需的字段可用率：直接决定可用台风数与年份下限
    fields = profile.get("availability", {}).get("fields", {})
    usa_pres = fields.get("usa_pres", {})
    if usa_pres.get("present") and usa_pres.get("missing_pct", 0) > 50:
        flags.append(
            {
                "level": "高",
                "topic": "USA_PRES（气压）缺失率高，是样本量的主要瓶颈",
                "evidence": f"USA_PRES 缺失 {usa_pres.get('missing_pct')}%",
                "decision": "四个特征中有两个是风速与气压，二者同时可用才有样本。"
                "需决定：年份下限取哪一年（体检表第 7 节给出分年代样本量）、"
                "是否允许气压短缺失插值（并标记 quality_flag=interpolated）、"
                "或改用其他气压来源列（如 WMO_PRES / CMA_PRES）补全。",
            }
        )

    wind = profile.get("physics", {}).get("wind_mps", {})
    if wind.get("out_of_configured_range"):
        flags.append(
            {
                "level": "中",
                "topic": "风速超出当前配置上限",
                "evidence": f"{wind['out_of_configured_range']} 条超过 "
                f"{schema.WIND_RANGE_MPS[1]} m/s（最大值 {wind.get('max')} m/s）",
                "decision": "确认是真实极端值（如 2013 海燕）还是单位换算错误，"
                "据此调整 schema.WIND_RANGE_MPS 上限。",
            }
        )

    lon = profile.get("longitude", {})
    if lon.get("convention") == "0-360":
        flags.append(
            {
                "level": "中",
                "topic": "经度为 0~360 口径",
                "evidence": f"经度范围 {lon.get('min')}~{lon.get('max')}，"
                f">180 的记录 {lon.get('above_180_count')} 条",
                "decision": "系统统一为 -180~180（validator 已支持自动转换）。"
                "需确认跨 180° 经线的台风是否会出现经度跳变（如 179 -> -179），必要时做连续性处理。",
            }
        )

    potential = profile.get("sample_potential", {})
    samples = potential.get("estimated_samples")
    if samples is not None and samples < 5000:
        raw = potential.get("raw_6h_interval", {})
        flags.append(
            {
                "level": "高",
                "topic": "可用样本量偏少",
                "evidence": f"重采样（synoptic 时刻）口径下估算 {samples} 个样本，"
                f"{potential.get('typhoons_with_samples')} 个台风；"
                f"不重采样时仅 {raw.get('estimated_samples')} 个（口径错误）",
                "decision": "3.1 已预警该风险。需按体检表第 7 节的分年代样本量决定年份下限；"
                "若样本仍不足，再考虑放宽气压缺失处理或降低输入长度（后者需同步修改模型与接口口径）。",
            }
        )

    nature = profile.get("basic", {}).get("nature_top", {})
    non_tc_rows = sum(int(v) for k, v in nature.items() if k in NON_TC_NATURE)
    if non_tc_rows:
        flags.append(
            {
                "level": "中",
                "topic": "存在非热带气旋性质记录",
                "evidence": f"NATURE 分布中非 TS 记录合计约 {fmt(non_tc_rows)} 条",
                "decision": "训练样本是否只保留 NATURE == TS？"
                "保留会把温带变性阶段当成台风轨迹学习。",
            }
        )

    basin_profile = profile.get("basin_consistency", {})
    multi = basin_profile.get("typhoons_with_multiple_basins", 0)
    if multi:
        flags.append(
            {
                "level": "高",
                "topic": "同一台风被标记为多个盆地",
                "evidence": f"{fmt(multi)} 个台风跨盆地标签，涉及 "
                f"{fmt(basin_profile.get('rows_belonging_to_multi_basin_typhoons'))} 条记录；"
                f"非 WP 记录 {fmt(basin_profile.get('non_wp_rows'))} 条",
                "decision": "必须确认过滤规则是「按 SID 整体取舍」还是「按行过滤」。"
                "按行过滤会把生成于 WP、东传越过 180° 的台风轨迹切断，"
                "造成输入窗口不连续（DQ-04）。建议按 SID 保留全部记录。",
            }
        )

    return flags


# ---------------------------------------------------------------------------
# 报告输出
# ---------------------------------------------------------------------------
def render_markdown(profile: dict) -> str:
    meta = profile["meta"]
    basic = profile["basic"]
    availability = profile["availability"]
    lines: list[str] = []
    add = lines.append

    add("# IBTrACS 数据体检表（阶段 0 侦察结果）")
    add("")
    add(f"- 生成时间（UTC）：{profile['generated_at']}")
    add(f"- 数据文件：`{meta['path']}`")
    add(f"- 文件大小：{meta['size_mb']} MB")
    add(f"- 是否含单位行（第二行）：{'是' if meta['has_units_row'] else '否'}")
    add(f"- 列数：{meta['column_count']}，记录数：{fmt(meta['row_count'])}")
    add("")
    add("> 本文件由 `backend/scripts/data_recon.py` 自动生成，是阶段 1「口径裁决」的输入证据。")
    add("")

    add("## 1 规模与覆盖")
    add("")
    add("| 指标 | 值 |")
    add("| --- | --- |")
    add(f"| 台风数（SID 去重） | {fmt(basic.get('typhoon_count', '—'))} |")
    add(f"| 时间跨度 | {basic.get('time_min', '—')} ~ {basic.get('time_max', '—')} |")
    add(f"| 时间不可解析记录 | {fmt(basic.get('time_unparsable', 0))} |")
    add(f"| SID 缺失记录 | {fmt(basic.get('sid_missing', 0))} |")
    add("")

    for label, title in (("basin_top", "盆地分布"), ("nature_top", "NATURE（性质）分布"), ("source_top", "资料来源分布")):
        data = basic.get(label)
        if not data:
            continue
        add(f"### {title}")
        add("")
        add("| 取值 | 记录数 |")
        add("| --- | --- |")
        for key, value in data.items():
            add(f"| {key} | {fmt(value)} |")
        add("")

    add("## 2 字段可用率")
    add("")
    add("| 字段 | 列名 | 非空 | 缺失 | 缺失率 |")
    add("| --- | --- | --- | --- | --- |")
    for label, info in availability.get("fields", {}).items():
        if not info.get("present"):
            add(f"| {label} | `{info.get('column')}` | — | — | **列不存在** |")
            continue
        add(
            f"| {label} | `{info['column']}` | {fmt(info['non_null'])} | {fmt(info['missing'])} "
            f"| {info['missing_pct']}% |"
        )
    add("")

    add("### 分年代缺失率（决定训练数据年份下限）")
    add("")
    for field, buckets in availability.get("missing_by_decade", {}).items():
        add(f"**{field}**")
        add("")
        add("| 年代 | 记录数 | 缺失率 |")
        add("| --- | --- | --- |")
        for decade, info in buckets.items():
            add(f"| {decade} | {fmt(info['rows'])} | {info['missing_pct']}% |")
        add("")

    agreement = profile.get("wind_agreement", {})
    add("## 3 各机构风速列对比（已换算为 m/s）")
    add("")
    add(f"可用列：{', '.join('`' + c + '`' for c in agreement.get('available_columns', [])) or '无'}")
    add("")
    if agreement.get("coverage"):
        add("| 列 | 非空数 | 非空率 | 均值(m/s) | 最大值(m/s) |")
        add("| --- | --- | --- | --- | --- |")
        for column, info in agreement["coverage"].items():
            add(
                f"| `{column}` | {fmt(info['non_null'])} | {info['non_null_pct']}% "
                f"| {info['mean_mps']} | {info['max_mps']} |"
            )
        add("")
    if agreement.get("pairwise"):
        add("| 对比 | 重叠数 | 平均差(m/s) | 中位差(m/s) | 平均绝对差(m/s) | P95绝对差 | 完全一致占比 |")
        add("| --- | --- | --- | --- | --- | --- | --- |")
        for pair, info in agreement["pairwise"].items():
            if not info.get("overlap"):
                add(f"| {pair} | 0 | — | — | — | — | — |")
                continue
            add(
                f"| {pair} | {fmt(info['overlap'])} | {info['mean_diff_mps']} | {info['median_diff_mps']} "
                f"| {info['mean_abs_diff_mps']} | {info['p95_abs_diff_mps']} | {info['identical_pct']}% |"
            )
        add("")
    if agreement.get("by_intensity_vs_usa"):
        add("按 USA 强度分档的平均绝对差（m/s）：")
        add("")
        for column, buckets in agreement["by_intensity_vs_usa"].items():
            add(f"- 对比 `{column}`：" + "；".join(
                f"{key} n={fmt(value['n'])} 差{value['mean_abs_diff_mps']}" for key, value in buckets.items()
            ))
        add("")

    intervals = profile.get("time_intervals", {})
    add("## 4 时间间隔真实性（DQ-01 / DQ-04）")
    add("")
    add(f"- 抽样台风数：{intervals.get('sampled_typhoons', '—')}")
    add(f"- 间隔样本数：{fmt(intervals.get('interval_count', '—'))}" if intervals.get("interval_count") else "- 间隔样本数：—")
    add(f"- **严格 6 小时占比：{intervals.get('exact_6h_pct', '—')}%**")
    add(f"- 3 小时间隔占比：{intervals.get('exact_3h_pct', '—')}%")
    add(f"- 2 小时间隔占比：{intervals.get('exact_2h_pct', '—')}%")
    add(f"- 1 小时间隔占比：{intervals.get('exact_1h_pct', '—')}%")
    if intervals.get("note"):
        add("")
        add(f"> {intervals['note']}")
    add("")
    if intervals.get("top_intervals_hours"):
        add("| 间隔(小时) | 次数 |")
        add("| --- | --- |")
        for key, value in intervals["top_intervals_hours"].items():
            add(f"| {key} | {fmt(value)} |")
        add("")

    lon = profile.get("longitude", {})
    add("## 5 经度口径")
    add("")
    add(f"- 范围：{lon.get('min')} ~ {lon.get('max')}")
    add(f"- 判定口径：**{lon.get('convention')}**")
    add(f"- 负值记录：{fmt(lon.get('negative_count', 0))}；>180 记录：{fmt(lon.get('above_180_count', 0))}")
    add("")

    physics = profile.get("physics", {})
    add("## 6 物理量程（校准 DQ-02 / DQ-03 阈值）")
    add("")
    add("| 字段 | 最小 | P01 | 中位 | P99 | 最大 | 超出当前配置 |")
    add("| --- | --- | --- | --- | --- | --- | --- |")
    for label, info in physics.items():
        if "min" not in info:
            add(f"| {label} | — | — | — | — | — | {info.get('note', '')} |")
            continue
        add(
            f"| {label} | {info['min']} | {info['p01']} | {info['median']} | {info['p99']} "
            f"| {info['max']} | {fmt(info['out_of_configured_range'])} |"
        )
    add("")

    potential = profile.get("sample_potential", {})
    add("## 7 样本可造潜力（回答“数据够不够”）")
    add("")
    funnel = potential.get("filter_funnel", {})
    if funnel:
        add("**逐级过滤漏斗**（每一步都在上一步基础上再收紧，单调整减）")
        add("")
        add("| 步骤 | 剩余记录数 | 本步损失 |")
        add("| --- | --- | --- |")
        previous = None
        for key, label in (
            ("rows_total", "文件总记录"),
            ("after_time", "时间可解析"),
            ("after_latlon", "且 经纬度非空"),
            ("after_usa_wind", "且 USA_WIND 非空"),
            ("after_wind_in_range", f"且 风速在 {schema.WIND_RANGE_MPS[0]}~{schema.WIND_RANGE_MPS[1]} m/s"),
            ("after_usa_pres", "且 USA_PRES 非空（关键瓶颈）"),
            ("after_nature_ts", f"且 NATURE == {TC_NATURE}"),
            ("after_basin_wp", "且 BASIN == WP"),
        ):
            value = funnel.get(key)
            if value is None:
                continue
            loss = "—" if previous is None else f"−{fmt(previous - value)}"
            add(f"| {label} | {fmt(value)} | {loss} |")
            previous = value
        add("")
    add(f"- 过滤规则：{'；'.join(potential.get('filter_rules', []))}")
    add("")

    raw = potential.get("raw_6h_interval", {})
    add("### 口径 A：原始记录直接要求 6 小时间隔（对照，说明为什么不能这么做）")
    add("")
    add(f"- 连续段数：{fmt(raw.get('continuous_segments'))}")
    add(f"- 满足 {potential.get('window_required_points', WINDOW)} 点的段数：{fmt(raw.get('segments_ge_window'))}")
    add(f"- 估算样本数：**{fmt(raw.get('estimated_samples'))}**")
    add(
        f"- 段长：最小 {fmt((raw.get('segment_length_stats') or {}).get('min'))}，"
        f"中位 {fmt((raw.get('segment_length_stats') or {}).get('median'))}，"
        f"最大 {fmt((raw.get('segment_length_stats') or {}).get('max'))}"
    )
    add("")
    add("> 该口径下样本几乎为 0，因为 IBTrACS 原始记录以 3 小时间隔为主（见第 4 节），")
    add("> 直接按 6 小时切分会把每个台风打成 1~2 点的碎段。")
    add("")

    add("### 口径 B：先重采样到 synoptic 时刻（00/06/12/18 UTC）再切窗口 —— 推荐基线")
    add("")
    add(f"- 重采样后记录数：{fmt(potential.get('resampled_rows'))}"
        f"（占可用记录 {potential.get('resampled_row_ratio_pct', '—')}%）")
    add(f"- 重采样后台风数：{fmt(potential.get('resampled_typhoons'))}")
    add(f"- 连续段总数：{fmt(potential.get('continuous_segments'))}")
    add(
        f"- 长度 ≥ {potential.get('window_required_points', WINDOW)} 点的连续段："
        f"{fmt(potential.get('segments_ge_window'))}"
    )
    add(f"- **估算可造样本数：{fmt(potential.get('estimated_samples'))}**")
    add(f"- 其中有样本的台风数：{fmt(potential.get('typhoons_with_samples'))}")
    stats = potential.get("segment_length_stats")
    if stats:
        add(
            f"- 连续段长度：最小 {fmt(stats.get('min'))}，中位 {fmt(stats.get('median'))}，"
            f"均值 {stats.get('mean')}，最大 {fmt(stats.get('max'))}"
        )
    add("")
    samples_by_decade = potential.get("estimated_samples_by_decade", {})
    if samples_by_decade:
        add("**分年代样本量（决定年份下限的真正依据）**")
        add("")
        add("| 年代 | 估算样本数 | 重采样后台风数 |")
        add("| --- | --- | --- |")
        by_decade = potential.get("usable_typhoons_by_decade", {})
        for decade, samples in samples_by_decade.items():
            add(f"| {decade}s | {fmt(samples)} | {fmt(by_decade.get(decade))} |")
        add("")

    tradeoff = potential.get("pressure_source_tradeoff", {})
    if tradeoff.get("sources"):
        add("### 气压来源替代方案（回答“换一列气压能救回多少样本”）")
        add("")
        add(f"> {tradeoff.get('note', '')}")
        add("")
        add("| 气压列 | 可用记录 | 台风数 | 估算样本数 | 有样本的台风 |")
        add("| --- | --- | --- | --- | --- |")
        for column, info in tradeoff["sources"].items():
            add(
                f"| `{column}` | {fmt(info['rows'])} | {fmt(info['typhoons'])} "
                f"| {fmt(info['estimated_samples'])} | {fmt(info['typhoons_with_samples'])} |"
            )
        add("")
    merged = tradeoff.get("merged_strategy")
    if merged:
        add(
            f"**合并策略（{merged['order']}）**：可用记录 {fmt(merged['rows'])}，"
            f"台风 {fmt(merged['typhoons'])} 个，**估算样本 {fmt(merged['estimated_samples'])}**，"
            f"有样本的台风 {fmt(merged['typhoons_with_samples'])} 个"
        )
        add("")
        filled = merged.get("filled_rows_by_source", {})
        if filled:
            add("各行填补来源记录数：" + "；".join(f"`{k}` 补 {fmt(v)} 行" for k, v in filled.items()))
            add("")
        gain = tradeoff.get("fallback_gain", {})
        if gain:
            add(
                f"> **回退填补收益：样本 {gain.get('samples_gain'):+,}（{gain.get('gain_pct')}%）**。"
                f"{gain.get('verdict', '')}"
            )
            add("> 说明：真正限制样本量的是四个特征**同时**可用的观测点数量，而不是某一列单独缺失。")
            add("")
        add("> 跨机构填补会引入口径混合（不同机构的观测/分析方式不同），")
        add("> 若采用必须在数据卡片中记录，并对填补点标记 quality_flag。")
        add("")

    basin_profile = profile.get("basin_consistency", {})
    add("## 8 盆地标签一致性（影响轨迹连续性）")
    add("")
    add(f"- 台风总数：{fmt(basin_profile.get('typhoons_total'))}")
    add(
        f"- **同一台风出现多个 BASIN 标签的数量：{fmt(basin_profile.get('typhoons_with_multiple_basins'))}**"
    )
    add(f"- 涉及的记录数：{fmt(basin_profile.get('rows_belonging_to_multi_basin_typhoons'))}")
    add(
        f"- 非 WP 记录：{fmt(basin_profile.get('non_wp_rows'))} 条，"
        f"涉及 {fmt(basin_profile.get('non_wp_typhoons'))} 个台风"
    )
    add("")
    examples = basin_profile.get("top_non_wp_examples", [])
    if examples:
        add("| SID | BASIN | 记录数 |")
        add("| --- | --- | --- |")
        for row in examples:
            add(f"| {row['SID']} | {row['BASIN']} | {fmt(row['rows'])} |")
        add("")
    if basin_profile.get("note"):
        add(f"> {basin_profile['note']}")
        add("")

    focus = profile.get("focus_area", {})
    add("## 9 中国近海关注区（3.1）")
    add("")
    add(f"- 关注区：{focus.get('area')}")
    add(
        f"- 区内记录：{fmt(focus.get('rows_in_focus_area', 0))}"
        f"（{focus.get('rows_in_focus_area_pct', 0)}%）"
    )
    add(f"- 进入过关注区的台风数：{fmt(focus.get('typhoons_reaching_focus_area', 0))}")
    add("")

    flags = profile.get("red_flags", [])
    add("## 10 数据质量红旗清单（阶段 1 待裁决）")
    add("")
    if not flags:
        add("本次侦察未发现需要裁决的问题。")
    else:
        add("| 级别 | 问题 | 证据 | 需要你决定的事 |")
        add("| --- | --- | --- | --- |")
        for flag in flags:
            add(f"| {flag['level']} | {flag['topic']} | {flag['evidence']} | {flag['decision']} |")
    add("")

    samples = profile.get("suspicious_samples", [])
    if samples:
        add("## 11 可疑点抽样（请手工核对原始文件）")
        add("")
        add("| SID | ISO_TIME | LAT | LON | USA_WIND(kt) | 换算(m/s) | USA_PRES | NATURE | BASIN |")
        add("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for row in samples:
            add(
                f"| {row['SID']} | {row['ISO_TIME']} | {row['LAT']} | {row['LON']} "
                f"| {row['USA_WIND_kt']} | {row['USA_WIND_mps']} | {row['USA_PRES']} "
                f"| {row['NATURE']} | {row['BASIN']} |"
            )
        add("")

    add("## 12 复现方式")
    add("")
    add("```bash")
    add("cd backend")
    add("python scripts/fetch_ibtracs.py --basin WP      # 下载数据（可断点续传）")
    add("python scripts/data_recon.py                     # 生成本体检表")
    add("```")
    add("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="IBTrACS 数据侦察（生成数据体检表）")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="IBTrACS CSV 路径")
    parser.add_argument("--no-report", action="store_true", help="只打印摘要，不写报告文件")
    parser.add_argument(
        "--output",
        default=str(DOCS_DIR / "data-recon-report.md"),
        help="体检表输出路径",
    )
    parser.add_argument(
        "--sample-rows",
        type=int,
        default=0,
        help="只读取前 N 行做快速验证（0 表示全量）；结果不可用于报告",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = Path(args.input)
    print(f"读取数据：{path}")
    if args.sample_rows:
        print(f"  [抽样模式] 仅读取前 {args.sample_rows} 行，结果仅供脚本验证，不能写进报告")
    frame, meta = read_ibtracs(path, sample_rows=args.sample_rows)
    print(
        f"  记录 {fmt(len(frame))} 行 × {len(frame.columns)} 列，"
        f"单位行={'有' if meta['has_units_row'] else '无'}"
    )

    profile: dict = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "meta": {key: value for key, value in meta.items() if key not in {"columns", "units_row"}},
        "columns": meta["columns"],
        "units_row": meta["units_row"],
    }

    steps = (
        ("基础画像", lambda: basic_profile(frame, meta), "basic"),
        ("字段可用率", lambda: availability_profile(frame), "availability"),
        ("机构风速对比", lambda: wind_agreement_profile(frame), "wind_agreement"),
        ("时间间隔", lambda: time_interval_profile(frame), "time_intervals"),
        ("经度口径", lambda: longitude_profile(frame), "longitude"),
        ("物理量程", lambda: physics_profile(frame), "physics"),
        ("样本潜力", lambda: sample_potential_profile(frame, meta), "sample_potential"),
        ("盆地一致性", lambda: basin_consistency_profile(frame), "basin_consistency"),
        ("关注区占比", lambda: focus_area_profile(frame), "focus_area"),
    )
    for title, function, key in steps:
        print(f"  分析：{title} …", end="", flush=True)
        try:
            profile[key] = function()
            print(" 完成")
        except Exception as exc:  # 单项失败不应中断整份体检
            profile[key] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f" 失败（{exc}）")

    profile["suspicious_samples"] = list_suspicious_points(frame)
    profile["red_flags"] = build_red_flags(profile)

    print()
    print("=" * 72)
    print(f"记录数        : {fmt(meta['row_count'])}")
    print(f"台风数        : {fmt(profile['basic'].get('typhoon_count'))}")
    print(
        f"时间跨度      : {profile['basic'].get('time_min', '—')} ~ "
        f"{profile['basic'].get('time_max', '—')}"
    )
    print(f"严格 6h 占比  : {profile['time_intervals'].get('exact_6h_pct', '—')}%")
    print(f"估算样本数    : {fmt(profile['sample_potential'].get('estimated_samples'))}")
    print(f"可用台风数    : {fmt(profile['sample_potential'].get('usable_typhoons'))}")
    print(f"红旗数量      : {len(profile['red_flags'])}")
    print("=" * 72)

    if not args.no_report:
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        summary_path = PROCESSED_DIR / "recon_summary.json"
        summary_path.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"机器可读摘要：{summary_path}")

        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_markdown(profile), encoding="utf-8")
        print(f"数据体检表  ：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
