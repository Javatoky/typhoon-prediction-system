"""生成符合附录A 格式的示例 CSV，并一键导入演示台风数据。

用途：
- 前端联调需要真实可用的历史台风数据（FR-003/FR-006）。
- 成果验收需要一份可复现的样例数据（AT-02/AT-04）。

用法（在 backend 目录）：
    python scripts/seed_demo_data.py --count 3        # 生成并导入 3 个演示台风
    python scripts/seed_demo_data.py --csv-only       # 只生成 CSV 到 data/samples/
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from config import SAMPLES_DIR  # noqa: E402


def synth_typhoon(index: int, *, point_count: int = 24, start_year: int = 2018):
    """构造一条平滑的西北太平洋台风轨迹（仅用于演示，不代表真实观测）。"""
    sid = f"WP{start_year}{index:02d}"
    names = ["Demo-One", "Demo-Two", "Demo-Three", "Demo-Four", "Demo-Five"]
    name = names[(index - 1) % len(names)]
    start = datetime(start_year, 8, 10 + (index - 1) * 3, 0, 0, 0)

    lat = 12.0 + index
    lon = 141.0 - index
    wind = 18.0 + index
    pressure = 1002.0 - index * 2

    rows = []
    for step in range(point_count):
        moment = start + timedelta(hours=6 * step)
        # 向西北方向移动并逐渐增强，接近 30°N/125°E 后转向偏北
        dlat = 0.7 + 0.05 * step
        dlon = -(0.85 + 0.05 * step)
        lat += dlat
        lon += dlon
        if lat > 30.0:
            dlat *= 0.6
            dlon *= 0.4
        wind = min(62.0, wind + 1.4)
        pressure = max(905.0, pressure - 2.1)
        rows.append(
            {
                "sid": sid,
                "iso_time": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "lat": round(lat, 2),
                "lon": round(lon, 2),
                "max_wind_mps": round(wind, 1),
                "pressure_hpa": round(pressure, 1),
                "name": name,
                "season": start_year,
                "basin": "WP",
            }
        )
    return sid, rows


def write_csv(rows: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["sid", "iso_time", "lat", "lon", "max_wind_mps", "pressure_hpa"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成并导入演示台风数据")
    parser.add_argument("--count", type=int, default=3, help="生成的台风数量")
    parser.add_argument("--points", type=int, default=24, help="每个台风的观测点数（6 小时间隔）")
    parser.add_argument("--csv-only", action="store_true", help="只生成 CSV，不写入数据库")
    parser.add_argument("--env", dest="env_name", default=None, help="运行环境名")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    generated: list[tuple[str, list[dict]]] = [
        synth_typhoon(index + 1, point_count=args.points) for index in range(args.count)
    ]

    for sid, rows in generated:
        path = write_csv(rows, SAMPLES_DIR / f"{sid}.csv")
        print(f"已生成示例 CSV：{path}（{len(rows)} 行）")

    if args.csv_only:
        return 0

    from app import create_app
    from app.services import dataset_service, user_service

    app = create_app(args.env_name)
    with app.app_context():
        actor = user_service.ensure_admin("admin", "Adm1n@2024")[0]
        for sid, rows in generated:
            header = list(rows[0].keys())
            record, stats = dataset_service.import_rows(
                actor,
                rows=rows,
                header=header,
                wind_unit="mps",
                source="demo-generator",
                source_version="demo-1.0",
                original_filename=f"{sid}.csv",
                stored_filename=None,
                file_size_bytes=None,
                strict=False,
            )
            print(
                f"导入 {sid}：状态 {record.status}，台风 {stats.typhoon_count} 个，"
                f"轨迹点 {stats.point_count} 个"
            )
    print("演示数据准备完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
