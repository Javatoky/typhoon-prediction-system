"""生成用于联调与测试的 CSV 样本（附录A 格式）。

用法（在 backend 目录）：
    python scripts/make_sample_csv.py                       # 生成合法样本
    python scripts/make_sample_csv.py --invalid             # 生成故意包含错误的样本
    python scripts/make_sample_csv.py --big-mb 21           # 生成超过 20MB 的文件（NFR-004 边界测试）
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from config import SAMPLES_DIR  # noqa: E402

HEADER = ["sid", "iso_time", "lat", "lon", "max_wind_mps", "pressure_hpa"]


def valid_rows(*, sid: str = "WP202601", points: int = 12) -> list[list]:
    start = datetime(2026, 7, 18, 0, 0, 0)
    lat, lon, wind, pressure = 18.2, 126.5, 28.0, 985.0
    rows = []
    for step in range(points):
        moment = start + timedelta(hours=6 * step)
        lat += 0.8
        lon -= 0.9
        wind += 2.0
        pressure -= 5.0
        rows.append(
            [
                sid,
                moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
                round(lat, 2),
                round(lon, 2),
                round(wind, 1),
                round(pressure, 1),
            ]
        )
    return rows


def invalid_rows() -> list[list]:
    """覆盖 DQ-01/02/03/04 的典型错误，便于验证行级提示。"""
    rows = valid_rows(points=6)
    rows[0][1] = "2026-07-18 00:00"  # 非 ISO 结尾，仍可解析（允许）
    rows[1][1] = "not-a-time"  # DQ-01 时间不可解析
    rows[2][2] = 120.0  # DQ-02 纬度越界
    rows[3][4] = "abc"  # DQ-03 风速非数值
    rows[4][5] = 500.0  # DQ-03 气压越界
    del rows[5]  # 造成时间缺口，同时减少可用点数（DQ-04）
    return rows


def write(path: Path, header: list[str], rows: list[list]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成 CSV 测试样本")
    parser.add_argument("--invalid", action="store_true", help="生成包含校验错误的样本")
    parser.add_argument("--big-mb", type=int, default=0, help="生成指定大小的文件用于超限测试")
    parser.add_argument("--points", type=int, default=12, help="合法样本的观测点数")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.big_mb:
        target = SAMPLES_DIR / f"oversize-{args.big_mb}mb.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        random.seed(42)
        with target.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(HEADER)
            approx_rows = int(args.big_mb * 1024 * 1024 / 60)
            start = datetime(2026, 7, 18)
            for index in range(approx_rows):
                moment = start + timedelta(hours=6 * index)
                writer.writerow(
                    [
                        "WPBIG001",
                        moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        round(15 + (index % 200) * 0.1, 2),
                        round(120 + (index % 300) * 0.1, 2),
                        round(20 + (index % 300) * 0.1, 1),
                        round(1005 - (index % 100) * 0.5, 1),
                    ]
                )
        print(f"已生成超限样本：{target}（约 {target.stat().st_size / 1024 / 1024:.1f} MB）")
        return 0

    if args.invalid:
        path = write(SAMPLES_DIR / "invalid-track.csv", HEADER, invalid_rows())
        print(f"已生成含错误的样本：{path}")
    else:
        path = write(SAMPLES_DIR / "valid-track.csv", HEADER, valid_rows(points=args.points))
        print(f"已生成合法样本：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
