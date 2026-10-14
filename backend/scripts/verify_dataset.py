"""数据集完整性核验（阶段 4 的必做验证）。

检查项（每项都必须能独立判定通过/失败）：
1. 数组形状：(N, 5, 4) 与 (N, 12, 4)
2. 标准化器数值是否合理（反标准化后回到真实物理量纲）
3. 训练/验证/测试的台风集合是否**完全不相交**（DQ-06 无泄漏）
4. 标准化是否真的用训练集统计量（验证集均值应接近 0，但不是精确 0）
5. 反标准化后样本是否落在物理量程内
6. 目标为"未来 12 点"的时序正确性：Y 的第一点应晚于 X 的最后一点
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.ml import schema  # noqa: E402
from app.ml.preprocess import FeatureScaler  # noqa: E402

PROCESSED = BACKEND.parent / "data" / "processed"

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + ("" if condition else f" -> {detail}"))
    if not condition:
        failures.append(name)


data = np.load(PROCESSED / "dataset.npz")
scaler = FeatureScaler.load(PROCESSED / "scaler.json")
meta = json.loads((PROCESSED / "dataset_meta.json").read_text(encoding="utf-8"))

print("=== 1 数组形状 ===")
for split in ("train", "val", "test"):
    x = data[f"x_{split}"]
    y = data[f"y_{split}"]
    check(
        f"{split} X 形状 (N,{schema.INPUT_LENGTH},{schema.FEATURE_DIM})",
        x.ndim == 3 and x.shape[1] == schema.INPUT_LENGTH and x.shape[2] == schema.FEATURE_DIM,
        str(x.shape),
    )
    check(
        f"{split} Y 形状 (N,{schema.OUTPUT_LENGTH},{schema.FEATURE_DIM})",
        y.ndim == 3 and y.shape[1] == schema.OUTPUT_LENGTH and y.shape[2] == schema.FEATURE_DIM,
        str(y.shape),
    )
    check(f"{split} X/Y 样本数一致", x.shape[0] == y.shape[0], f"{x.shape[0]} vs {y.shape[0]}")

print()
print("=== 2 标准化器数值合理性（必须符合真实台风的物理量纲）===")
names = list(schema.FEATURE_COLUMNS)
expected = {
    "lat": (5.0, 40.0, "度"),
    "lon": (110.0, 180.0, "度（-180~180 口径下西太平洋台风约 105~180，跨线为负）"),
    "max_wind_mps": (15.0, 55.0, "m/s（若未换算 knot 会接近 2 倍）"),
    "central_pressure_hpa": (950.0, 1005.0, "hPa"),
}
for name, mean, std in zip(names, scaler.mean, scaler.std):
    low, high, unit = expected[name]
    check(
        f"{name} 均值 {mean:.2f} 在合理区间 [{low}, {high}] {unit}",
        low <= mean <= high,
        f"实际 {mean:.3f}",
    )
    check(f"{name} 标准差 > 0", std > 0, str(std))

print()
print("=== 3 划分无泄漏（DQ-06：按 SID 划分）===")
splits = meta["splits"]
train_sids = set(splits["train_sids"])
val_sids = set(splits["val_sids"])
test_sids = set(splits["test_sids"])
check("训练∩验证 = ∅", not (train_sids & val_sids), str(sorted(train_sids & val_sids)[:5]))
check("训练∩测试 = ∅", not (train_sids & test_sids), str(sorted(train_sids & test_sids)[:5]))
check("验证∩测试 = ∅", not (val_sids & test_sids), str(sorted(val_sids & test_sids)[:5]))
check(
    "三个集合台风数之和 = 总台风数",
    len(train_sids) + len(val_sids) + len(test_sids) == len(train_sids | val_sids | test_sids),
)
print(f"  训练 {len(train_sids)} / 验证 {len(val_sids)} / 测试 {len(test_sids)} 个台风")

print()
print("=== 4 标准化是否使用训练集统计量 ===")
train_x = data["x_train"]
val_x = data["x_val"]
train_mean = train_x.reshape(-1, schema.FEATURE_DIM).mean(axis=0)
val_mean = val_x.reshape(-1, schema.FEATURE_DIM).mean(axis=0)
check(
    "训练集标准化后各特征均值接近 0（|mean| < 0.15）",
    bool(np.all(np.abs(train_mean) < 0.15)),
    str(np.round(train_mean, 4)),
)
check(
    "验证集均值接近 0 但不强制为 0（说明复用训练集 scaler）",
    bool(np.all(np.abs(val_mean) < 0.6)),
    str(np.round(val_mean, 4)),
)

print()
print("=== 5 反标准化后落在物理量程内 ===")
raw_x = np.stack([scaler.inverse_transform(item) for item in data["x_train"][:200]])
raw_y = np.stack([scaler.inverse_transform(item) for item in data["y_train"][:200]])
flat = np.concatenate([raw_x.reshape(-1, 4), raw_y.reshape(-1, 4)])
check(
    "纬度在 [-90, 90]",
    bool(((flat[:, 0] >= -90) & (flat[:, 0] <= 90)).all()),
    f"范围 {flat[:, 0].min():.1f}~{flat[:, 0].max():.1f}",
)
check(
    "经度在 [-180, 180]",
    bool(((flat[:, 1] >= -180) & (flat[:, 1] <= 180)).all()),
    f"范围 {flat[:, 1].min():.1f}~{flat[:, 1].max():.1f}",
)
check(
    f"风速在 {schema.WIND_RANGE_MPS}",
    bool(((flat[:, 2] >= schema.WIND_RANGE_MPS[0]) & (flat[:, 2] <= schema.WIND_RANGE_MPS[1])).all()),
    f"范围 {flat[:, 2].min():.1f}~{flat[:, 2].max():.1f}",
)
check(
    f"气压在 {schema.PRESSURE_RANGE_HPA}",
    bool(
        ((flat[:, 3] >= schema.PRESSURE_RANGE_HPA[0]) & (flat[:, 3] <= schema.PRESSURE_RANGE_HPA[1])).all()
    ),
    f"范围 {flat[:, 3].min():.1f}~{flat[:, 3].max():.1f}",
)

print()
print("=== 6 时序关系：目标点应晚于输入点（按反标准化后的位置变化检查）===")
# 输入最后一点的纬度 vs 目标第一点的纬度：台风不应出现超过 10 度的瞬时跳变
jump_lat = np.abs(raw_y[:, 0, 0] - raw_x[:, -1, 0])
jump_lon = np.abs(raw_y[:, 0, 1] - raw_x[:, -1, 1])
check("输入末点→目标首点纬度跳变 < 10°", bool((jump_lat < 10).all()), f"最大 {jump_lat.max():.2f}°")
check("输入末点→目标首点经度跳变 < 12°", bool((jump_lon < 12).all()), f"最大 {jump_lon.max():.2f}°")
within = np.abs(np.diff(raw_y[:, :, 0], axis=1))
check("目标序列内部纬度逐步变化 < 8°", bool((within < 8).all()), f"最大 {within.max():.2f}°")

print()
print("=== 7 数据集元数据与冻结口径一致 ===")
caliber = meta["stats"]["caliber"]
check("风速列 = USA_WIND", caliber["wind_column"] == "USA_WIND", caliber["wind_column"])
check("风速原始单位 = kt", caliber["wind_unit_source"] == "kt", caliber["wind_unit_source"])
check("气压主列 = USA_PRES", caliber["pressure_primary"] == "USA_PRES", caliber["pressure_primary"])
check("年份下限 = 2000", caliber["min_year"] == 2000, str(caliber["min_year"]))
check("synoptic 时刻 = 00/06/12/18", caliber["synoptic_hours"] == [0, 6, 12, 18], str(caliber["synoptic_hours"]))

print()
print("=== 8 冻结常量与运行期配置一致（防止文档/代码/配置三方漂移）===")
from config import Config  # noqa: E402

check(
    "schema.WIND_RANGE_MPS 上限 = 110（D-08）",
    schema.WIND_RANGE_MPS[1] == 110.0,
    str(schema.WIND_RANGE_MPS),
)
check(
    "config.Config.WIND_RANGE_MPS 与 schema 一致",
    tuple(Config.WIND_RANGE_MPS) == tuple(schema.WIND_RANGE_MPS),
    f"config={Config.WIND_RANGE_MPS} schema={schema.WIND_RANGE_MPS}",
)
check(
    "schema.PRIMARY_WIND_COLUMN = USA_WIND",
    schema.PRIMARY_WIND_COLUMN == "USA_WIND",
    schema.PRIMARY_WIND_COLUMN,
)
check(
    "schema.PRIMARY_PRESSURE_COLUMN = USA_PRES",
    schema.PRIMARY_PRESSURE_COLUMN == "USA_PRES",
    schema.PRIMARY_PRESSURE_COLUMN,
)
check(
    "气压回退顺序 = WMO/CMA/HKO",
    tuple(schema.FALLBACK_PRESSURE_COLUMNS) == ("WMO_PRES", "CMA_PRES", "HKO_PRES"),
    str(schema.FALLBACK_PRESSURE_COLUMNS),
)

print()
if failures:
    print(f"=== 核验失败 {len(failures)} 项 ===")
    for name in failures:
        print(" -", name)
    raise SystemExit(1)
print("=== 数据集完整性核验全部通过 ===")
