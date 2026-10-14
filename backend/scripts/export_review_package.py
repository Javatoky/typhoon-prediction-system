"""导出模型复核包（供外部专家复查）。

为什么需要它：外部复查者手上不会有我们的对话记录，必须能从零开始独立判断
"模型为什么未优于持续性基线"。因此本脚本把**复现所需的一切**导出到
`data/review/`，包括可视化轨迹，而不只是一堆权重文件。

导出内容：
    README_REVIEW.md          复核说明：数据结构、复现命令、已知问题、待判断问题
    model/best.pt             模型权重（state_dict）+ last.pt
    model/metadata.json       训练超参、标准化器、训练历史（逐轮损失）
    model/metrics_test.json   测试集指标（含基线对比）
    model/scaler.json         标准化器参数
    data/dataset_meta.json    数据集口径留痕与划分清单
    data/summary.json         数据集统计（样本数、分布、口径）
    samples/predictions.csv   逐样本逐时效的 预测 vs 实测（可直接用 Excel 看）
    samples/worst_cases.json  模型误差最大的样本明细
    plots/*.svg               轨迹对比图、损失曲线、误差-时效曲线（矢量图，无需依赖）

用法（在 backend 目录）：
    python scripts/export_review_package.py
    python scripts/export_review_package.py --package-dir ../data/review
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
for path in (str(BACKEND_DIR),):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.ml import schema  # noqa: E402
from app.ml.preprocess import FeatureScaler  # noqa: E402
from app.utils.metrics import haversine_km  # noqa: E402

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
MODEL_DIR = PROJECT_ROOT / "data" / "models"
DEFAULT_PACKAGE = PROJECT_ROOT / "data" / "review"

FORECAST_HOURS = [6 * (i + 1) for i in range(schema.OUTPUT_LENGTH)]


# ---------------------------------------------------------------------------
# 绘图（纯 SVG，不引入 matplotlib 依赖）
# ---------------------------------------------------------------------------
def svg_line_chart(
    series: dict[str, list[float]],
    *,
    title: str,
    x_labels: list[str],
    y_label: str,
    width: int = 720,
    height: int = 380,
) -> str:
    pad_left, pad_right, pad_top, pad_bottom = 70, 20, 40, 55
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom
    all_values = [value for values in series.values() for value in values if value == value]
    y_max = max(all_values) * 1.1 if all_values else 1.0
    y_min = 0.0
    colors = ["#1f6feb", "#d1242f", "#1a7f37", "#bf8700", "#8250df"]
    n = max(len(values) for values in series.values())

    def x_at(index: int) -> float:
        return pad_left + (plot_w * index / max(n - 1, 1))

    def y_at(value: float) -> float:
        return pad_top + plot_h - plot_h * (value - y_min) / max(y_max - y_min, 1e-9)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="Segoe UI, Microsoft YaHei, sans-serif">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{pad_left}" y="24" font-size="15" font-weight="600">{title}</text>',
    ]
    # 网格与 y 轴刻度
    for tick in range(6):
        value = y_min + (y_max - y_min) * tick / 5
        y = y_at(value)
        parts.append(
            f'<line x1="{pad_left}" y1="{y:.1f}" x2="{pad_left + plot_w}" y2="{y:.1f}" '
            f'stroke="#e5e9ef" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{pad_left - 8}" y="{y + 4:.1f}" font-size="11" fill="#656d76" '
            f'text-anchor="end">{value:.0f}</text>'
        )
    # 曲线
    for index, (label, values) in enumerate(series.items()):
        color = colors[index % len(colors)]
        points = " ".join(
            f"{x_at(i):.1f},{y_at(v):.1f}" for i, v in enumerate(values) if v == v
        )
        parts.append(
            f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>'
        )
        for i, value in enumerate(values):
            if value == value:
                parts.append(
                    f'<circle cx="{x_at(i):.1f}" cy="{y_at(value):.1f}" r="3" fill="{color}"/>'
                )
        legend_x = pad_left + index * 150
        parts.append(
            f'<line x1="{legend_x}" y1="{height - 26}" x2="{legend_x + 20}" y2="{height - 26}" '
            f'stroke="{color}" stroke-width="2"/>'
        )
        parts.append(
            f'<text x="{legend_x + 26}" y="{height - 22}" font-size="12" fill="#1f2328">{label}</text>'
        )
    # x 轴标签
    for i, label in enumerate(x_labels[:n]):
        parts.append(
            f'<text x="{x_at(i):.1f}" y="{pad_top + plot_h + 18}" font-size="11" '
            f'fill="#656d76" text-anchor="middle">{label}</text>'
        )
    parts.append(
        f'<text x="16" y="{pad_top + plot_h / 2:.0f}" font-size="12" fill="#656d76" '
        f'transform="rotate(-90 16 {pad_top + plot_h / 2:.0f})" text-anchor="middle">{y_label}</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def svg_track_chart(
    tracks: list[dict],
    *,
    title: str,
    width: int = 760,
    height: int = 460,
) -> str:
    """经纬度轨迹对比图（x=经度，y=纬度）。"""
    pad_left, pad_right, pad_top, pad_bottom = 70, 20, 40, 40
    plot_w = width - pad_left - pad_right
    plot_h = height - pad_top - pad_bottom
    lons = [p[1] for track in tracks for p in track["points"]]
    lats = [p[0] for track in tracks for p in track["points"]]
    lon_min, lon_max = min(lons) - 2, max(lons) + 2
    lat_min, lat_max = min(lats) - 2, max(lats) + 2

    def x_at(lon: float) -> float:
        return pad_left + plot_w * (lon - lon_min) / max(lon_max - lon_min, 1e-9)

    def y_at(lat: float) -> float:
        return pad_top + plot_h - plot_h * (lat - lat_min) / max(lat_max - lat_min, 1e-9)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="Segoe UI, Microsoft YaHei, sans-serif">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{pad_left}" y="24" font-size="15" font-weight="600">{title}</text>',
    ]
    # 经纬网格
    for step in range(0, 6):
        lat = lat_min + (lat_max - lat_min) * step / 5
        parts.append(
            f'<line x1="{pad_left}" y1="{y_at(lat):.1f}" x2="{pad_left + plot_w}" '
            f'y2="{y_at(lat):.1f}" stroke="#e5e9ef"/>'
        )
        parts.append(
            f'<text x="{pad_left - 8}" y="{y_at(lat) + 4:.1f}" font-size="11" fill="#656d76" '
            f'text-anchor="end">{lat:.0f}</text>'
        )
        lon = lon_min + (lon_max - lon_min) * step / 5
        parts.append(
            f'<line x1="{x_at(lon):.1f}" y1="{pad_top}" x2="{x_at(lon):.1f}" '
            f'y2="{pad_top + plot_h}" stroke="#e5e9ef"/>'
        )
        parts.append(
            f'<text x="{x_at(lon):.1f}" y="{pad_top + plot_h + 18}" font-size="11" '
            f'fill="#656d76" text-anchor="middle">{lon:.0f}</text>'
        )
    # 轨迹
    for track in tracks:
        color = track["color"]
        coords = " ".join(f"{x_at(p[1]):.1f},{y_at(p[0]):.1f}" for p in track["points"])
        dash = ' stroke-dasharray="6,4"' if track.get("dashed") else ""
        parts.append(
            f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="2"{dash}/>'
        )
        parts.append(
            f'<circle cx="{x_at(track["points"][0][1]):.1f}" cy="{y_at(track["points"][0][0]):.1f}" '
            f'r="3.5" fill="{color}"/>'
        )
    legend = [
        ("实测轨迹", "#1f6feb", False),
        ("模型预测", "#d1242f", True),
    ]
    for index, (label, color, dashed) in enumerate(legend):
        lx = pad_left + index * 150
        dash = ' stroke-dasharray="6,4"' if dashed else ""
        parts.append(
            f'<line x1="{lx}" y1="{height - 14}" x2="{lx + 22}" y2="{height - 14}" '
            f'stroke="{color}" stroke-width="2"{dash}/>'
        )
        parts.append(
            f'<text x="{lx + 28}" y="{height - 10}" font-size="12" fill="#1f2328">{label}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="导出模型复核包")
    parser.add_argument("--package-dir", default=str(DEFAULT_PACKAGE))
    parser.add_argument("--worst-cases", type=int, default=12, help="导出多少个最差样本")
    return_args = parser.parse_args()

    package = Path(return_args.package_dir)
    for sub in ("model", "data", "samples", "plots"):
        (package / sub).mkdir(parents=True, exist_ok=True)

    # ---- 复制模型与元数据 ----
    for name in ("best.pt", "last.pt", "metadata.json", "scaler.json"):
        source = MODEL_DIR / name
        if source.exists():
            shutil.copy2(source, package / "model" / name)
            print(f"已复制 {name}")
    for name in ("metrics_test.json", "metrics_train.json"):
        source = MODEL_DIR / name
        if source.exists():
            shutil.copy2(source, package / "model" / name)
    for name in ("dataset_meta.json", "scaler.json", "recon_summary.json"):
        source = PROCESSED_DIR / name
        if source.exists():
            shutil.copy2(source, package / "data" / name)
    # 一并放入数据集本体，使复核者不依赖提供方即可完整复现推理
    dataset_npz = PROCESSED_DIR / "dataset.npz"
    if dataset_npz.exists():
        shutil.copy2(dataset_npz, package / "data" / "dataset.npz")
        print(f"已复制 dataset.npz（{dataset_npz.stat().st_size / 1024:.0f} KB）")

    # ---- 载入数据与模型做分析 ----
    data = np.load(PROCESSED_DIR / "dataset.npz")
    scaler = FeatureScaler.load(PROCESSED_DIR / "scaler.json")
    meta = json.loads((MODEL_DIR / "metadata.json").read_text(encoding="utf-8"))
    dataset_meta = json.loads((PROCESSED_DIR / "dataset_meta.json").read_text(encoding="utf-8"))

    import torch  # 延迟导入：仅在真正需要推理时依赖

    from app.ml.models.transformer import TyphoonTransformer

    params = meta["hyperparams"]
    model = TyphoonTransformer(
        feature_dim=int(params["feature_dim"]),
        input_length=int(params["input_length"]),
        output_length=int(params["output_length"]),
        d_model=int(params["d_model"]),
        nhead=int(params["nhead"]),
        num_encoder_layers=int(params["num_encoder_layers"]),
        dim_feedforward=int(params["dim_feedforward"]),
        dropout=float(params["dropout"]),
    )
    state = torch.load(MODEL_DIR / "best.pt", map_location="cpu", weights_only=False)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    model.load_state_dict(state)
    model.eval()

    x_test, y_test = data["x_test"], data["y_test"]
    with torch.no_grad():
        pred_std = model(torch.tensor(x_test, dtype=torch.float32)).numpy()
    pred = np.stack([scaler.inverse_transform(row) for row in pred_std])
    actual = np.stack([scaler.inverse_transform(row) for row in y_test])
    baseline = np.stack(
        [
            scaler.inverse_transform(row)
            for row in np.repeat(x_test[:, -1:, :], actual.shape[1], axis=1)
        ]
    )

    n, T, _ = actual.shape

    def per_hour_errors(p: np.ndarray) -> np.ndarray:
        return np.array(
            [
                np.mean(
                    [
                        haversine_km(p[i, t, 0], p[i, t, 1], actual[i, t, 0], actual[i, t, 1])
                        for i in range(n)
                    ]
                )
                for t in range(T)
            ]
        )

    model_hour = per_hour_errors(pred)
    base_hour = per_hour_errors(baseline)

    def ade_of(p: np.ndarray) -> np.ndarray:
        return np.array(
            [
                np.mean(
                    [
                        haversine_km(p[i, t, 0], p[i, t, 1], actual[i, t, 0], actual[i, t, 1])
                        for t in range(T)
                    ]
                )
                for i in range(n)
            ]
        )

    model_ade, base_ade = ade_of(pred), ade_of(baseline)

    # ---- 逐样本明细 CSV ----
    csv_path = package / "samples" / "predictions.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "sample_index",
                "forecast_hour",
                "pred_lat",
                "pred_lon",
                "pred_wind_mps",
                "pred_pressure_hpa",
                "actual_lat",
                "actual_lon",
                "actual_wind_mps",
                "actual_pressure_hpa",
                "baseline_lat",
                "baseline_lon",
                "model_error_km",
                "baseline_error_km",
                "error_diff_km",
            ]
        )
        for i in range(n):
            for t in range(T):
                model_err = haversine_km(pred[i, t, 0], pred[i, t, 1], actual[i, t, 0], actual[i, t, 1])
                base_err = haversine_km(
                    baseline[i, t, 0], baseline[i, t, 1], actual[i, t, 0], actual[i, t, 1]
                )
                writer.writerow(
                    [
                        i,
                        FORECAST_HOURS[t],
                        round(float(pred[i, t, 0]), 3),
                        round(float(pred[i, t, 1]), 3),
                        round(float(pred[i, t, 2]), 2),
                        round(float(pred[i, t, 3]), 1),
                        round(float(actual[i, t, 0]), 3),
                        round(float(actual[i, t, 1]), 3),
                        round(float(actual[i, t, 2]), 2),
                        round(float(actual[i, t, 3]), 1),
                        round(float(baseline[i, t, 0]), 3),
                        round(float(baseline[i, t, 1]), 3),
                        round(float(model_err), 2),
                        round(float(base_err), 2),
                        round(float(model_err - base_err), 2),
                    ]
                )
    print(f"已写出逐样本明细：{csv_path}（{n * T} 行）")

    # ---- 最差样本 ----
    order = np.argsort(-model_ade)
    worst = order[: return_args.worst_cases]
    worst_payload = []
    for i in worst:
        sample = {
            "sample_index": int(i),
            "model_ade_km": round(float(model_ade[i]), 1),
            "baseline_ade_km": round(float(base_ade[i]), 1),
            "input_window": [
                {
                    "lat": round(float(x_test[i, t, 0]), 2),
                    "lon": round(float(x_test[i, t, 1]), 2),
                    "wind_mps": round(float(x_test[i, t, 2]), 1),
                    "pressure_hpa": round(float(x_test[i, t, 3]), 1),
                }
                for t in range(x_test.shape[1])
            ],
            "predicted": [
                {"lat": round(float(pred[i, t, 0]), 2), "lon": round(float(pred[i, t, 1]), 2)}
                for t in range(T)
            ],
            "actual": [
                {"lat": round(float(actual[i, t, 0]), 2), "lon": round(float(actual[i, t, 1]), 2)}
                for t in range(T)
            ],
        }
        worst_payload.append(sample)
    (package / "samples" / "worst_cases.json").write_text(
        json.dumps(worst_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ---- 数据集统计 ----
    wind_all = np.concatenate([data["x_train"][:, :, 2].ravel(), data["y_train"][:, :, 2].ravel()])
    pres_all = np.concatenate([data["x_train"][:, :, 3].ravel(), data["y_train"][:, :, 3].ravel()])
    summary = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "dataset": {
            "samples_total": int(sum(len(data[f"x_{s}"]) for s in ("train", "val", "test"))),
            "samples": {s: int(len(data[f"x_{s}"])) for s in ("train", "val", "test")},
            "typhoons": {s: len(dataset_meta["splits"][f"{s}_sids"]) for s in ("train", "val", "test")},
            "array_layout": {
                "x": f"(N, {schema.INPUT_LENGTH}, {schema.FEATURE_DIM}) = 最近 {schema.INPUT_LENGTH} 个 "
                f"{schema.TIME_STEP_HOURS} 小时观测",
                "y": f"(N, {schema.OUTPUT_LENGTH}, {schema.FEATURE_DIM}) = 未来 "
                f"{schema.OUTPUT_LENGTH} 个 {schema.TIME_STEP_HOURS} 小时时效",
                "feature_order": list(schema.FEATURE_COLUMNS),
                "note": "npz 中的数值已是标准化后的值（均值 0、标准差 1）；"
                "用 model/scaler.json 反标准化即得物理量",
            },
            "train_wind_mps": {
                "mean": round(float(wind_all.mean()), 2),
                "min": round(float(wind_all.min()), 2),
                "max": round(float(wind_all.max()), 2),
            },
            "train_pressure_hpa": {
                "mean": round(float(pres_all.mean()), 2),
                "min": round(float(pres_all.min()), 2),
                "max": round(float(pres_all.max()), 2),
            },
        },
        "metrics_test": {
            "model_error_km_by_hour": {str(h): round(float(v), 1) for h, v in zip(FORECAST_HOURS, model_hour)},
            "baseline_error_km_by_hour": {str(h): round(float(v), 1) for h, v in zip(FORECAST_HOURS, base_hour)},
            "model_ade_mean_km": round(float(model_ade.mean()), 1),
            "model_ade_median_km": round(float(np.median(model_ade)), 1),
            "baseline_ade_mean_km": round(float(base_ade.mean()), 1),
            "baseline_ade_median_km": round(float(np.median(base_ade)), 1),
            "model_wins_pct": round(float((model_ade < base_ade).mean() * 100), 1),
        },
        "known_issues": [
            "2026-10-14 修复：distance_errors_by_hour 用 dict(zip(hours, errors))，"
            "批量评估时同一时效重复出现导致后值覆盖前值，指标被严重高估（曾误判为优于基线）。"
            "已改为按键聚合求平均，并补回归测试。",
            "2026-10-14 修复：训练管线列名归一化用'先到先得'，"
            "导致 USA_WIND 被 WMO_WIND 覆盖、USA_PRES 被 TOKYO_PRES 覆盖（与冻结口径不符）。"
            "已改为显式映射。",
            "2026-10-14 修复：风速单位判定在列名归一化之后进行，导致 knot 未换算（偏大 1.94 倍）。"
            "已改为用归一化前的表头判定。",
        ],
        "open_questions": [
            "模型 ADE(729km) 高于持续性基线(577km)，但 57.7% 的样本上模型更优，"
            "且中位数上模型更好（419km vs 560km）。差异主要由少量极端样本造成"
            "（最差 20 个样本贡献了 67% 的差值）。",
            "【重点】模型预测值的**离散度被压缩**：风速预测标准差仅为实测的 0.81~0.82 倍，"
            "气压为 0.78~0.81 倍，而均值几乎无偏（训练集风速偏置 +0.19 m/s、测试集 +0.73 m/s）。"
            "这是典型的回归到均值（regression to the mean）现象：模型学会了"
            "'典型台风'的行为，但在极端个案上不敢给出极端预测。"
            "这解释了为什么中位数优于基线、均值却输给基线；也让持续性基线在短时效占优"
            "（基线不压缩、直接延续最后观测）。是否应改用分位数损失/不确定性建模？",
            "误差最大的样本集中在 lon≈-5°（即 355°E，中/东太平洋）、"
            "lat≈-1°（赤道以南）。疑为跨盆地台风（生成于 WP、东传越过 180° 后被标 EP）。"
            "当前口径按 SID 保留全轨迹，因此这些段落进入了训练与测试。"
            "是否应排除跨盆地区段？若排除又会切断跨 180° 台风的轨迹连续性，如何权衡？",
            "模型对典型台风预测准确（输入速度 < 60km/6h 时预测位移偏差仅 +3km），"
            "但对快速移动样本偏慢（-15km/6h）。是样本量不足还是模型容量/结构问题？",
            "训练集 ADE 469km vs 测试集 729km，存在一定过拟合（样本仅 6,584）。"
            "训练集风速标准差 17.06、测试集 15.86，分布略有差异。"
            "建议的正则化/数据增强手段？",
            "验证损失（0.234）远低于持续性基线（0.953），但物理单位指标反而更差。"
            "是否说明损失函数（标准化空间的加权 SmoothL1）与目标任务不匹配？"
            "是否应改为直接优化物理单位误差，或对位置项使用 Haversine 损失？",
        ],
    }
    (package / "data" / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ---- 图 1：损失曲线 ----
    history = meta.get("history", [])
    if history:
        epochs = [row["epoch"] for row in history]
        svg = svg_line_chart(
            {
                "训练损失": [row["train_loss"] for row in history],
                "验证损失": [row["val_loss"] for row in history],
            },
            title="训练 / 验证损失曲线（标准化空间）",
            x_labels=[str(e) if e % 5 == 0 else "" for e in epochs],
            y_label="加权 SmoothL1 损失",
        )
        (package / "plots" / "loss_curve.svg").write_text(svg, encoding="utf-8")
        print("已绘制损失曲线")

    # ---- 图 2：误差-时效曲线 ----
    svg = svg_line_chart(
        {
            "模型": [float(v) for v in model_hour],
            "持续性基线": [float(v) for v in base_hour],
        },
        title="分时效距离误差（测试集，Haversine 大圆距离）",
        x_labels=[f"{h}h" for h in FORECAST_HOURS],
        y_label="平均误差 (km)",
    )
    (package / "plots" / "error_by_hour.svg").write_text(svg, encoding="utf-8")

    # ---- 图 3：几个代表性台风的轨迹对比 ----
    picks = [int(order[-1]), int(order[len(order) // 2]), int(order[0])]
    labels = ["表现最好的样本", "中位样本", "表现最差的样本"]
    for pick, label in zip(picks, labels):
        svg = svg_track_chart(
            [
                {
                    "color": "#1f6feb",
                    "points": [(float(actual[pick, t, 0]), float(actual[pick, t, 1])) for t in range(T)],
                },
                {
                    "color": "#d1242f",
                    "points": [(float(pred[pick, t, 0]), float(pred[pick, t, 1])) for t in range(T)],
                    "dashed": True,
                },
            ],
            title=f"{label}（样本 #{pick}）模型 ADE {model_ade[pick]:.0f} km / 基线 {base_ade[pick]:.0f} km",
        )
        (package / "plots" / f"track_sample_{pick}.svg").write_text(svg, encoding="utf-8")
    print("已绘制轨迹对比图")

    # ---- 复核说明 ----
    readme = f"""# 模型复核包说明

本包用于外部复查：**为什么模型在 24 小时时效上未优于持续性基线**。
复查者无需接触原始代码库即可完成判断。

生成时间：{summary['generated_at']}

## 1 这是什么问题

任务是：给定台风最近 **5 个 6 小时观测点**（纬度、经度、最大风速、中心气压），
预测未来 **12 个 6 小时时效点**（即最长 72 小时）的同样四个要素。

对照基线为**持续性预测**（把最后观测点原样延续 12 个时效）。
需求文档 NFR-005 要求：24 小时平均误差不劣于该基线。**当前未达标**。

模型：PyTorch 内置 TransformerEncoder，d_model=64、4 头、2 层，
输入 5×4 经线性投影 + 可学习位置编码 + 2 层编码器，取最后时刻表示与均值池化拼接后
经 MLP 输出 12×4。参数量 82,864。单次多步输出（非自回归）。

## 2 目录内容

| 路径 | 内容 |
| --- | --- |
| `model/best.pt` | 最佳权重（**state_dict 格式**，需先用下面代码构造模型再 load_state_dict） |
| `model/last.pt` | 最后一轮权重 |
| `model/metadata.json` | 超参数、标准化器、**逐轮训练历史**、最佳轮次 |
| `model/scaler.json` | 四特征均值/标准差（反标准化用） |
| `model/metrics_test.json` | 官方评估脚本输出的指标 |
| `data/dataset_meta.json` | 数据集口径留痕、按 SID 的划分清单 |
| `data/dataset.npz` | 数据集本体（`x/y_train|val|test`，已标准化），可完整复现推理 |
| `data/summary.json` | 数据集统计、指标汇总、**已知问题**、**待判断问题** |
| `samples/predictions.csv` | 逐样本逐时效的预测/实测/基线/误差（{n * T} 行，Excel 可直接看） |
| `samples/worst_cases.json` | 误差最大的 {return_args.worst_cases} 个样本明细 |
| `plots/loss_curve.svg` | 训练/验证损失曲线 |
| `plots/error_by_hour.svg` | 分时效误差曲线（模型 vs 基线） |
| `plots/track_sample_*.svg` | 3 个代表性样本的轨迹对比（最好/中位/最差） |

> 图为纯 SVG 矢量图，用浏览器或图片查看器直接打开，无需任何依赖。

## 3 复现步骤

```bash
pip install "torch>=2.2" numpy
```

```python
import json, numpy as np, torch

# 1) 数据结构：x=(N,5,4) 最近 5 个观测；y=(N,12,4) 未来 12 个时效
#    特征顺序固定为 lat, lon, max_wind_mps, central_pressure_hpa
#    npz 中的数值已标准化（均值 0、标准差 1），用 model/scaler.json 反标准化即得物理量
data = np.load("data/dataset.npz")
x_test, y_test = data["x_test"], data["y_test"]

# 2) 模型结构（必须与本包 model/best.pt 匹配）
class PositionalEncoding(torch.nn.Module):
    def __init__(self, d_model, max_len=64, dropout=0.1):
        super().__init__()
        self.dropout = torch.nn.Dropout(dropout)
        self.encoding = torch.nn.Parameter(torch.zeros(1, max_len, d_model))
        torch.nn.init.trunc_normal_(self.encoding, std=0.02)
    def forward(self, x):
        return self.dropout(x + self.encoding[:, :x.size(1), :])

class TyphoonTransformer(torch.nn.Module):
    def __init__(self, d_model=64, nhead=4, layers=2, ff=128, dropout=0.1,
                 in_len=5, out_len=12, feat=4):
        super().__init__()
        self.input_length, self.output_length, self.feature_dim = in_len, out_len, feat
        self.input_projection = torch.nn.Linear(feat, d_model)
        self.positional_encoding = PositionalEncoding(d_model, 64, dropout)
        enc = torch.nn.TransformerEncoderLayer(
            d_model=d_model, nhead=nhead, dim_feedforward=ff, dropout=dropout,
            activation="gelu", batch_first=True)
        self.encoder = torch.nn.TransformerEncoder(enc, num_layers=layers)
        self.norm = torch.nn.LayerNorm(d_model)
        self.head = torch.nn.Sequential(
            torch.nn.Linear(d_model * 2, d_model), torch.nn.GELU(),
            torch.nn.Dropout(dropout), torch.nn.Linear(d_model, out_len * feat))
    def forward(self, x):
        h = self.positional_encoding(self.input_projection(x))
        h = self.norm(self.encoder(h))
        rep = torch.cat([h[:, -1, :], h.mean(dim=1)], dim=-1)
        return self.head(rep).view(x.size(0), self.output_length, self.feature_dim)

model = TyphoonTransformer()
state = torch.load("model/best.pt", map_location="cpu", weights_only=False)
model.load_state_dict(state)
model.eval()
print("参数量", sum(p.numel() for p in model.parameters()))
```

## 4 数据集口径（关键）

| 项 | 值 |
| --- | --- |
| 数据源 | IBTrACS v04r01，西北太平洋（WP）子集 |
| 风速列 | `USA_WIND`（**原始单位 knot，已乘 0.514444 换算为 m/s**） |
| 气压列 | `USA_PRES` 为主，缺失时依次用 `WMO_PRES`/`CMA_PRES`/`HKO_PRES` 填补 |
| 采样 | 只保留 00/06/12/18 UTC（synoptic 时刻），**不做插值** |
| 年份下限 | 2000 年（此前无可用样本） |
| 性质过滤 | 只保留 `NATURE == 'TS'` |
| 盆地规则 | 按 SID 保留全轨迹（**允许跨盆地段落进入数据**） |
| 样本量 | 6,584（训练 4,584 / 验证 894 / 测试 1,106） |
| 标准化 | 四特征各自减均值除标准差，**仅由训练集拟合** |

## 5 已知问题

见 `data/summary.json` 的 `known_issues` 字段。摘要：

1. **指标函数曾有严重缺陷**（已修复）：批量评估时同一时效重复出现，
   `dict(zip(hours, errors))` 导致后值覆盖前值，指标被严重高估——
   曾据此误判"3 轮模型优于基线"。修复后结论反转为"未优于基线"。
2. **训练管线列名隐式映射**（已修复）：`USA_WIND` 被 `WMO_WIND` 覆盖、
   `USA_PRES` 被 `TOKYO_PRES` 覆盖。
3. **风速单位判定时机错误**（已修复）：knot 未换算，风速偏大 1.94 倍。

## 6 待判断问题（希望得到外部意见）

1. **模型 ADE 729 km 高于基线 577 km，但 57.7% 的样本上模型更优。**
   最差 20 个样本贡献了 67% 的差值。这是"模型确实更差"，还是"指标口径问题"（均值被极端值主导）？
   注意**中位数**上模型反而更好（模型 419 km vs 基线 560 km）。是否应同时报告中位数与分位数？

2. **误差最大的样本集中在经度 ≈ -5°（即 355°E，中/东太平洋）、纬度 ≈ -1°。**
   疑为**跨盆地台风**（生成于 WP、东传越过 180° 后被标记为 EP）。
   当前口径按 SID 保留全轨迹，这些段落进入了训练与测试。
   是否应排除？若排除，又会切断跨 180° 台风的轨迹连续性——如何权衡？

3. **【最可能的关键原因】模型把预测值"往中间压"。**
   风速预测标准差仅为实测的 **0.81~0.82 倍**，气压为 **0.78~0.81 倍**，
   而均值几乎无偏（偏置 < 1 m/s、< 0.5 hPa）。这是典型的**回归到均值**：
   模型学到"典型台风"，但在极端个案上不敢外推。
   这正好解释"中位数赢、均值输"，也让持续性基线在短时效占优
   （基线不压缩离散度，直接延续最后观测）。
   验证方式：直接看 `samples/predictions.csv` 的 `pred_wind_mps` 与 `actual_wind_mps` 两列分布。

4. **模型对典型台风很准**（输入速度 < 60 km/6h 时，预测位移偏差仅 +3 km），
   但对快速移动样本预测偏慢（-15 km/6h）。这是样本量不足，还是模型容量/结构问题？

5. **训练集 ADE 469 km vs 测试集 729 km**，存在一定过拟合（样本仅 6,584）。
   建议的正则化/数据增强手段？

6. **验证损失（0.234）远低于持续性基线（0.953），但物理单位指标反而更差。**
   这是否说明损失函数（标准化空间的加权 SmoothL1）与目标任务不匹配？
   是否应改为直接优化物理单位误差，或对位置项改用 Haversine 损失？

## 7 复现训练

```bash
cd backend
python scripts/fetch_ibtracs.py --basin WP            # 下载数据（约 109 MB）
python scripts/data_recon.py                          # 数据体检表
python training/prepare_data.py --input ../data/raw/ibtracs.WP.list.v04r01.csv
python training/train.py --epochs 60                  # 训练（CPU 约 3 分钟）
python training/evaluate.py --split test              # 评估
```
"""
    (package / "README_REVIEW.md").write_text(readme, encoding="utf-8")

    print()
    print("=" * 68)
    print(f"复核包已生成：{package}")
    print(f"  模型 ADE 均值 {model_ade.mean():.1f} km / 中位 {np.median(model_ade):.1f} km")
    print(f"  基线 ADE 均值 {base_ade.mean():.1f} km / 中位 {np.median(base_ade):.1f} km")
    print(f"  模型更优样本占比 {(model_ade < base_ade).mean() * 100:.1f}%")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
