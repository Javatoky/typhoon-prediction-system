"""阶段 3 组长抽查包：原始记录 vs 处理结果逐行对照（docs/data-workflow.md 第 2 节）。

为什么需要它：阶段 3 的完成标志是"组长抽查 ≥5 个台风的原始数据与处理结果是否一致"。
dataset.npz 里只有标准化后的窗口，无法直接和原始 CSV 对照；本脚本对抽中的每个台风
列出**每一条原始记录**的去向（保留 / 被哪条口径剔除）和处理后的值，组长只需打开原始 CSV
按 SID 搜索、逐行核对即可。

抽样方式：
- 默认从三个集合（train/val/test）中按固定种子随机抽取，保证每个集合至少 1 个，结果可复现；
- --include-flagged 额外加入"跨 180° 经度跳变"和"气压回退最多"的台风各 1 个（需要重点看的）；
- --sid 可直接指定台风。

自检：每条记录的剔除原因由本脚本按口径独立判定，再与 prepare_data.clean_frame 的
实际输出比对；两者不一致时会在报告中标红（说明诊断逻辑或管线有一方出错）。

用法（在 backend 目录）：
    python scripts/spot_check.py                       # 随机 5 个
    python scripts/spot_check.py --n 6 --include-flagged
    python scripts/spot_check.py --sid 2018250N12170
产出：data/review/spot_check/README.md（核对清单）+ 每个台风一个 <SID>.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
for path in (str(BACKEND_DIR),):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.ml import schema  # noqa: E402
from app.utils.units import KNOT_TO_MPS, normalize_lon  # noqa: E402
from training import prepare_data  # noqa: E402
from training.config import TrainConfig  # noqa: E402

DEFAULT_OUT = PROJECT_ROOT / "data" / "review" / "spot_check"
SPLITS = ("train", "val", "test")


# ---------------------------------------------------------------------------
# 抽样
# ---------------------------------------------------------------------------
def pick_sids(
    meta: dict, n: int, seed: int, exclude: set[str] | frozenset[str] = frozenset()
) -> list[tuple[str, str]]:
    """按集合分层随机抽样，返回 [(sid, split)]；每个集合至少 1 个。

    exclude 中的台风（例如 --include-flagged 加入的）不参与随机抽取，保证随机部分足额。
    """
    rng = np.random.default_rng(seed)
    pools = {
        split: [sid for sid in meta["splits"][f"{split}_sids"] if sid not in exclude]
        for split in SPLITS
    }
    picked: list[tuple[str, str]] = []
    for split in SPLITS:
        if pools[split]:
            sid = str(rng.choice(pools[split]))
            picked.append((sid, split))
            pools[split].remove(sid)
    rest = [(sid, split) for split in SPLITS for sid in pools[split]]
    extra = max(0, n - len(picked))
    if extra and rest:
        for index in rng.choice(len(rest), size=min(extra, len(rest)), replace=False):
            picked.append(rest[int(index)])
    return picked


def split_of(meta: dict, sid: str) -> str:
    for split in SPLITS:
        if sid in meta["splits"][f"{split}_sids"]:
            return split
    return "未进入样本"


# ---------------------------------------------------------------------------
# 逐行诊断
# ---------------------------------------------------------------------------
def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def diagnose(raw: pd.DataFrame) -> pd.DataFrame:
    """对单个台风的原始记录逐行判定去向（按 clean_frame 的步骤顺序，取第一个命中的原因）。"""
    table = pd.DataFrame(index=raw.index)
    time = pd.to_datetime(raw["iso_time"], errors="coerce", utc=True).dt.tz_localize(None)
    table["iso_time"] = time
    table["nature"] = raw.get("nature", pd.Series("", index=raw.index)).astype(str).str.strip()
    table["basin"] = raw.get("basin", pd.Series("", index=raw.index)).astype(str).str.strip()
    table["lat"] = _num(raw["lat"])
    table["lon_raw"] = _num(raw["lon"])
    table["lon"] = table["lon_raw"].map(normalize_lon)
    table["wind_kt"] = _num(raw["max_wind_mps"])  # load_raw 只改列名，此时仍是原始 knot
    table["wind_mps"] = (table["wind_kt"] * KNOT_TO_MPS).round(2)
    table["usa_pres"] = _num(raw["pressure_hpa"]) if "pressure_hpa" in raw else np.nan

    # 与 clean_frame 顺序一致：主列不做量程过滤就参与判断（越界的主列值不会触发回退），
    # 回退列先过量程再填补，最后整体过一次量程
    pressure = table["usa_pres"].copy()
    source = pd.Series(np.where(pressure.notna(), "primary", ""), index=raw.index)
    for fallback in schema.FALLBACK_PRESSURE_COLUMNS:
        column = f"pressure_fallback__{fallback.upper()}"
        table[fallback.lower()] = _num(raw[column]) if column in raw else np.nan
        candidate = table[fallback.lower()].where(
            table[fallback.lower()].between(*schema.PRESSURE_RANGE_HPA)
        )
        need = pressure.isna() & candidate.notna()
        pressure = pressure.where(~need, candidate)
        source = source.where(~need, f"fallback:{fallback}")
    table["pressure"] = pressure.where(pressure.between(*schema.PRESSURE_RANGE_HPA))
    table["pressure_source"] = source

    reasons = pd.Series("", index=raw.index)

    def mark(condition: pd.Series, reason: str) -> None:
        reasons.loc[(reasons == "") & condition.fillna(True)] = reason

    mark(time.isna(), "时间不可解析")
    mark(time.dt.year < schema.MIN_YEAR, f"D-04 年份 < {schema.MIN_YEAR}")
    mark(~time.dt.hour.isin(schema.SYNOPTIC_HOURS), "D-03 非 synoptic 时刻")
    mark(table["lat"].isna() | table["lon"].isna(), "经纬度缺失")
    mark(~table["lat"].between(*schema.LAT_RANGE), "纬度越界")
    mark(table["nature"] != schema.TRAINING_NATURE, f"D-06 NATURE≠{schema.TRAINING_NATURE}")
    mark(~table["wind_mps"].between(*schema.WIND_RANGE_MPS), "D-01 风速缺失或越界")
    mark(table["pressure"].isna(), "D-02 气压缺失（含回退列）")
    duplicated = time.duplicated(keep="first") & (reasons == "")
    mark(duplicated, "同一时刻重复")
    table["reason"] = reasons
    return table


def describe_storm(
    sid: str, raw_all: pd.DataFrame, cleaned_all: pd.DataFrame, config: TrainConfig
) -> dict:
    raw = raw_all[raw_all["sid"].astype(str).str.strip() == sid]
    table = diagnose(raw)
    cleaned = cleaned_all[cleaned_all["sid"] == sid].sort_values("iso_time")

    # 进入样本的点：所在连续段长度 ≥ 输入+输出
    window = config.input_length + config.output_length
    in_sample_times: set = set()
    sample_count = 0
    for segment in prepare_data.split_continuous_segments(cleaned, config.step_hours):
        if len(segment) >= window:
            in_sample_times.update(segment["iso_time"].tolist())
            sample_count += len(segment) - window + 1
    table["in_sample"] = table["iso_time"].isin(in_sample_times)

    # 自检：本脚本判定"保留"的时刻 == clean_frame 实际保留的时刻
    diag_kept = set(table.loc[table["reason"] == "", "iso_time"])
    pipe_kept = set(cleaned["iso_time"])
    mismatch = sorted(diag_kept ^ pipe_kept)

    # 保留点的处理后数值也与管线逐项比对
    value_mismatch = 0
    if not mismatch and len(cleaned):
        merged = table[table["reason"] == ""].merge(
            cleaned[["iso_time", "lon", "max_wind_mps", "central_pressure_hpa", "pressure_source"]],
            on="iso_time",
            suffixes=("", "_pipe"),
        )
        value_mismatch = int(
            (~np.isclose(merged["lon"], merged["lon_pipe"])).sum()
            + (~np.isclose(merged["wind_mps"], merged["max_wind_mps"], atol=0.01)).sum()
            + (~np.isclose(merged["pressure"], merged["central_pressure_hpa"])).sum()
            + (merged["pressure_source"] != merged["pressure_source_pipe"]).sum()
        )

    lon = cleaned["lon"].to_numpy()
    crosses_dateline = bool(len(lon) > 1 and (np.abs(np.diff(lon)) > 180).any())
    name = ""
    if "name" in raw and len(raw):
        name = str(raw["name"].iloc[0]).strip()
    return {
        "sid": sid,
        "name": name,
        "table": table,
        "raw_rows": len(raw),
        "kept_rows": len(cleaned),
        "in_sample_rows": int(table["in_sample"].sum()),
        "sample_count": sample_count,
        "fallback_rows": int((cleaned["pressure_source"] != "primary").sum()) if len(cleaned) else 0,
        "crosses_dateline": crosses_dateline,
        "mismatch": mismatch,
        "value_mismatch": value_mismatch,
    }


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def _fmt(value, digits: int = 1) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_storm(info: dict, split: str, raw_path: Path) -> str:
    table = info["table"]
    ok = not info["mismatch"] and not info["value_mismatch"]
    lines = [
        f"# {info['sid']} {info['name']}（{split}）",
        "",
        f"- 原始文件：`{raw_path.name}`（用编辑器或 Excel 搜索 `{info['sid']}`）",
        f"- 原始记录 {info['raw_rows']} 条 → 保留 {info['kept_rows']} 条 → 进入样本 {info['in_sample_rows']} 条，"
        f"构成样本 {info['sample_count']} 个",
        f"- 气压非主列填补：{info['fallback_rows']} 条；跨 180° 经度跳变：{'**是**' if info['crosses_dateline'] else '否'}",
        f"- 自检（本脚本判定 vs prepare_data 实际输出）：{'一致' if ok else '**不一致，先找 AI/代码排查，不要签字**'}",
    ]
    if info["mismatch"]:
        lines.append(f"  - 去向不一致的时刻：{[str(t) for t in info['mismatch'][:10]]}")
    if info["value_mismatch"]:
        lines.append(f"  - 处理后数值不一致：{info['value_mismatch']} 处")
    lines += [
        "",
        "## 核对要点（建议至少核对 3 行保留记录 + 2 行被剔除记录）",
        "",
        "1. 保留行：原始 `USA_WIND`(kt) × 0.514444 = 风速(m/s)；气压取 `USA_PRES`，缺失时依次取 WMO/CMA/HKO",
        "2. 保留行：经度 > 180 的应换算为 经度 − 360",
        "3. 剔除行：原因与原始值相符（如 NATURE 确实不是 TS、时刻确实不是 00/06/12/18）",
        "",
        "## 逐行对照",
        "",
        "| # | ISO_TIME | NATURE | BASIN | LAT | LON(原始) | USA_WIND(kt) | USA_PRES | WMO/CMA/HKO_PRES "
        "| 去向 | LON(处理) | 风速(m/s) | 气压(hPa) | 气压来源 | 进样本 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for number, (_index, row) in enumerate(table.iterrows(), start=1):
        kept = row["reason"] == ""
        fallbacks = "/".join(_fmt(row[f.lower()], 0) for f in schema.FALLBACK_PRESSURE_COLUMNS)
        lines.append(
            "| "
            + " | ".join(
                [
                    str(number),
                    str(row["iso_time"]),
                    row["nature"],
                    row["basin"],
                    _fmt(row["lat"]),
                    _fmt(row["lon_raw"]),
                    _fmt(row["wind_kt"], 0),
                    _fmt(row["usa_pres"], 0),
                    fallbacks,
                    "保留" if kept else f"剔除：{row['reason']}",
                    _fmt(row["lon"]) if kept else "",
                    _fmt(row["wind_mps"], 2) if kept else "",
                    _fmt(row["pressure"], 0) if kept else "",
                    row["pressure_source"] if kept else "",
                    "✓" if row["in_sample"] else "",
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def render_index(results: list[tuple[dict, str]], raw_path: Path, seed: int) -> str:
    lines = [
        "# 阶段 3 抽查清单（组长填写）",
        "",
        f"- 原始文件：`{raw_path}`",
        f"- 抽样种子：{seed}（同一份数据、同一种子会抽到同样的台风）",
        "- 每个台风的逐行对照见同目录 `<SID>.md`；核对完在下表填写结论。",
        "- 全部一致后，在 `docs/data-workflow.md` 阶段 3 状态处更新为完成，并注明抽查日期。",
        "",
        "| SID | 名称 | 集合 | 原始→保留→进样本 | 样本数 | 填补点 | 跨 180° | 自检 | 组长结论 | 备注 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for info, split in results:
        ok = not info["mismatch"] and not info["value_mismatch"]
        lines.append(
            f"| [{info['sid']}]({info['sid']}.md) | {info['name']} | {split} | "
            f"{info['raw_rows']}→{info['kept_rows']}→{info['in_sample_rows']} | {info['sample_count']} | "
            f"{info['fallback_rows']} | {'是' if info['crosses_dateline'] else '否'} | "
            f"{'一致' if ok else '**不一致**'} | ☐ 一致 ☐ 不一致 | |"
        )
    lines += ["", "抽查人：__________　日期：__________", ""]
    return "\n".join(lines)


def flagged_sids(meta: dict, cleaned: pd.DataFrame) -> list[str]:
    """需要重点看的台风：跨 180°（若有）与气压回退点最多的那个。"""
    in_samples = {sid for split in SPLITS for sid in meta["splits"][f"{split}_sids"]}
    extra: list[str] = []
    dateline = meta.get("stats", {}).get("dateline_crossing", {}).get("sids", [])
    if dateline:
        extra.append(dateline[0])
    fallback = cleaned[(cleaned["pressure_source"] != "primary") & cleaned["sid"].isin(in_samples)]
    if not fallback.empty:
        extra.append(str(fallback["sid"].value_counts().index[0]))
    return extra


def main() -> int:
    parser = argparse.ArgumentParser(description="阶段 3 抽查包：原始记录 vs 处理结果逐行对照")
    parser.add_argument("--input", default=str(TrainConfig().input_path))
    parser.add_argument("--processed-dir", default=str(TrainConfig().processed_dir))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--n", type=int, default=5, help="随机抽取的台风数（默认 5，阶段 3 要求 ≥5）")
    parser.add_argument("--seed", type=int, default=20261023)
    parser.add_argument("--sid", action="append", default=[], help="直接指定台风，可重复")
    parser.add_argument("--include-flagged", action="store_true", help="额外加入跨 180° / 气压回退最多的台风")
    args = parser.parse_args()

    config = TrainConfig(input_path=Path(args.input), processed_dir=Path(args.processed_dir))
    meta_path = Path(args.processed_dir) / "dataset_meta.json"
    if not meta_path.exists():
        print(f"找不到 {meta_path}，请先运行 python training/prepare_data.py")
        return 2
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    raw_all, wind_unit, _columns = prepare_data.load_raw(Path(args.input))
    if wind_unit != "kt":
        print(f"警告：风速单位判定为 {wind_unit}，本脚本的 knot 换算列将不适用。")
    cleaned_all = prepare_data.clean_frame(raw_all, config, wind_unit)

    picked = [(sid, split_of(meta, sid)) for sid in args.sid]
    if args.include_flagged:
        picked += [(sid, split_of(meta, sid)) for sid in flagged_sids(meta, cleaned_all)]
    if not args.sid:
        picked += pick_sids(meta, args.n, args.seed, exclude={sid for sid, _ in picked})
    seen: set[str] = set()
    picked = [item for item in picked if not (item[0] in seen or seen.add(item[0]))]

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for sid, split in picked:
        info = describe_storm(sid, raw_all, cleaned_all, config)
        if info["raw_rows"] == 0:
            print(f"跳过 {sid}：原始文件中不存在")
            continue
        (out / f"{sid}.md").write_text(render_storm(info, split, Path(args.input)), encoding="utf-8")
        results.append((info, split))
        status = "一致" if not info["mismatch"] and not info["value_mismatch"] else "不一致"
        print(
            f"{sid} {info['name']:<12} {split:<5} 原始 {info['raw_rows']:>3} → 保留 {info['kept_rows']:>3}"
            f" → 样本 {info['sample_count']:>3}  自检 {status}"
        )
    (out / "README.md").write_text(render_index(results, Path(args.input), args.seed), encoding="utf-8")
    print(f"抽查包已生成：{out / 'README.md'}")
    return 0 if all(not i["mismatch"] and not i["value_mismatch"] for i, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
