"""合成 IBTrACS 格式 CSV，用于离线测试数据管线（不依赖真实 109 MB 文件）。

刻意复现真实文件里的几个"坑"（docs/data-workflow.md 第 4 节）：
- 第 2 行是单位行；
- 机构同义列的列序与真实文件一致：WMO_* 在 USA_* 之前、TOKYO_PRES 也有值，
  且各机构取值不同——一旦列映射退化为"先到先得"，测试会立刻读到错误的值；
- 3 小时间隔记录（synoptic 过滤后才成为 6 小时）；
- 经度为 0~360 口径；跨盆地（WP → EP）与非 TS 性质记录。
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

COLUMNS = [
    "SID", "SEASON", "BASIN", "NAME", "ISO_TIME", "NATURE", "LAT", "LON",
    "WMO_WIND", "WMO_PRES", "USA_WIND", "USA_PRES",
    "TOKYO_WIND", "TOKYO_PRES", "CMA_WIND", "CMA_PRES", "HKO_WIND", "HKO_PRES",
]
UNITS_ROW = [
    "", "Year", "", "", "", "", "degrees_north", "degrees_east",
    "kts", "mb", "kts", "mb", "kts", "mb", "kts", "mb", "kts", "mb",
]

# 各机构取值刻意不同，便于断言"读的是哪一列"；风速、气压逐点变化（避免标准差为 0）
TOKYO_PRES = 900  # 若被误映射为主气压列，会出现 900 这个"不可能"的值


def usa_wind_kt(i: int) -> int:
    return 40 + 2 * i


def wmo_wind_kt(i: int) -> int:
    return usa_wind_kt(i) - 10


def usa_pres(i: int) -> int:
    return 1000 - i


def wmo_pres(i: int) -> int:
    return usa_pres(i) + 5


def storm_rows(
    sid: str,
    start: datetime,
    n_points: int,
    *,
    lat0: float = 15.0,
    lon0: float = 140.0,
    dlat: float = 0.3,
    dlon: float = -0.5,
    basin: str = "WP",
    nature: str = "TS",
    name: str = "DEMO",
) -> list[dict]:
    """生成一条 3 小时间隔的轨迹（n_points 个 3 小时记录）。"""
    rows = []
    for i in range(n_points):
        time = start + timedelta(hours=3 * i)
        rows.append(
            {
                "SID": sid,
                "SEASON": str(start.year),
                "BASIN": basin,
                "NAME": name,
                "ISO_TIME": time.strftime("%Y-%m-%d %H:%M:%S"),
                "NATURE": nature,
                "LAT": f"{lat0 + dlat * i:.1f}",
                "LON": f"{lon0 + dlon * i:.1f}",
                "WMO_WIND": str(wmo_wind_kt(i)),
                "WMO_PRES": str(wmo_pres(i)),
                "USA_WIND": str(usa_wind_kt(i)),
                "USA_PRES": str(usa_pres(i)),
                "TOKYO_WIND": "55",
                "TOKYO_PRES": str(TOKYO_PRES),
                "CMA_WIND": "52",
                "CMA_PRES": "990",
                "HKO_WIND": "53",
                "HKO_PRES": "995",
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerow(UNITS_ROW)
        for row in rows:
            writer.writerow([row.get(column, " ") for column in COLUMNS])
    return path


def build_default_rows(*, include_dateline: bool = True) -> list[dict]:
    """默认场景：每种口径规则至少触发一次。include_dateline=False 时去掉跨 180° 的 D 台风。"""
    rows: list[dict] = []

    # A：标准 WP 台风，2010 年，41 个 3 小时记录 → 21 个 synoptic 点 → 5 个样本
    a = storm_rows("2010200N15140", datetime(2010, 7, 19, 0), 41)
    # 第 3 个 synoptic 点（06 UTC 后的 12 UTC，即索引 4）USA_PRES 缺失 → 应回退到 WMO_PRES
    a[4]["USA_PRES"] = " "
    rows += a

    # B：1995 年 → D-04 年份下限剔除
    rows += storm_rows("1995200N15140", datetime(1995, 7, 19, 0), 41)

    # C：只在 EP → D-05 按 SID 剔除
    rows += storm_rows("2011200N15250", datetime(2011, 7, 19, 0), 41, lon0=250.0, basin="EP")

    # D：生成于 WP、东传越过 180° 进入 EP → D-05 应整体保留（含 EP 段）
    if include_dateline:
        d = storm_rows("2012200N15175", datetime(2012, 7, 19, 0), 41, lon0=170.0, dlon=0.6)
        for row in d:
            if float(row["LON"]) >= 180.0:
                row["BASIN"] = "EP"
        rows += d

    # E：中段变性为 ET → D-06 剔除该段，轨迹断开
    e = storm_rows("2013200N15140", datetime(2013, 7, 19, 0), 41)
    for row in e[16:20]:
        row["NATURE"] = "ET"
    rows += e

    # F/G/H：额外的普通台风，保证按 SID 划分时三个集合都非空
    rows += storm_rows("2014200N15140", datetime(2014, 7, 19, 0), 41)
    rows += storm_rows("2015200N15140", datetime(2015, 7, 19, 0), 41)
    rows += storm_rows("2016200N15140", datetime(2016, 7, 19, 0), 41)
    return rows
