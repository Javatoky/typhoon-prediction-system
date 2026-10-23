"""离线训练数据管线回归测试（docs/data-card.md D-01 ~ D-08，第 5 节三个缺陷）。

用合成的 IBTrACS 格式 CSV（tests/ibtracs_fixture.py），每条用例对应一条冻结口径
或一个历史缺陷：口径若被悄悄改动，这里会先失败。
"""

from __future__ import annotations

import numpy as np
import pytest

from app.ml import schema
from app.utils.units import KNOT_TO_MPS
from tests import ibtracs_fixture as fx
from training import prepare_data
from training.config import TrainConfig


@pytest.fixture()
def raw_csv(tmp_path):
    return fx.write_csv(tmp_path / "ibtracs.WP.synthetic.csv", fx.build_default_rows())


@pytest.fixture()
def config(raw_csv, tmp_path):
    return TrainConfig(input_path=raw_csv, processed_dir=tmp_path / "processed")


@pytest.fixture()
def cleaned(raw_csv, config):
    frame, wind_unit, _raw_columns = prepare_data.load_raw(raw_csv)
    return prepare_data.clean_frame(frame, config, wind_unit)


def by_sid(frame, sid):
    return frame[frame["sid"] == sid].sort_values("iso_time")


# ---------------------------------------------------------------------------
# 读取：单位行与列映射（缺陷 1、缺陷 2）
# ---------------------------------------------------------------------------
def test_units_row_is_skipped(raw_csv):
    frame, _unit, _columns = prepare_data.load_raw(raw_csv)
    assert len(frame) == len(fx.build_default_rows())
    assert not (frame["sid"].astype(str).str.strip() == "").any()


def test_wind_unit_detected_from_raw_header(raw_csv):
    _frame, wind_unit, raw_columns = prepare_data.load_raw(raw_csv)
    assert "USA_WIND" in raw_columns
    assert wind_unit == "kt"


def test_wind_column_is_usa_wind_not_first_agency_column(cleaned):
    """缺陷 1：WMO_WIND 列序在前（且比 USA 小 10 kt），若退化为"先到先得"会读错列。"""
    storm = by_sid(cleaned, "2010200N15140")
    # synoptic 点是 3 小时序列的偶数下标 0, 2, 4, ...
    expected = [fx.usa_wind_kt(i) * KNOT_TO_MPS for i in range(0, 41, 2)]
    assert storm["max_wind_mps"].to_numpy() == pytest.approx(expected)


def test_primary_pressure_is_usa_pres_not_tokyo(cleaned):
    """缺陷 1：TOKYO_PRES=900 若被误映射为主气压列，这里会出现 900。"""
    storm = by_sid(cleaned, "2010200N15140")
    primary = storm[storm["pressure_source"] == "primary"]
    assert len(primary) == 20
    expected = [fx.usa_pres(i) for i in range(0, 41, 2) if i != 4]
    assert primary["central_pressure_hpa"].tolist() == expected
    assert fx.TOKYO_PRES not in set(cleaned["central_pressure_hpa"])


# ---------------------------------------------------------------------------
# 冻结口径 D-02 ~ D-06
# ---------------------------------------------------------------------------
def test_pressure_fallback_fills_from_wmo_and_records_source(cleaned):
    storm = by_sid(cleaned, "2010200N15140")
    point = storm[storm["iso_time"] == np.datetime64("2010-07-19T12:00:00")]
    assert len(point) == 1
    assert point["central_pressure_hpa"].iloc[0] == fx.wmo_pres(4)
    assert point["pressure_source"].iloc[0] == "fallback:WMO_PRES"
    # 其余点仍用主列
    assert (storm["pressure_source"] == "primary").sum() == len(storm) - 1


def test_min_year_drops_pre_2000_storms(cleaned):
    assert "1995200N15140" not in set(cleaned["sid"])
    assert (cleaned["iso_time"].dt.year >= schema.MIN_YEAR).all()


def test_only_synoptic_hours_kept(cleaned):
    assert set(cleaned["iso_time"].dt.hour) <= set(schema.SYNOPTIC_HOURS)
    # 41 个 3 小时记录 → 21 个 synoptic 点，且不做任何插值
    assert len(by_sid(cleaned, "2010200N15140")) == 21


def test_basin_rule_is_per_sid_not_per_row(cleaned):
    """D-05：纯 EP 台风整条剔除；WP 生成、东传进 EP 的台风保留 EP 段。"""
    assert "2011200N15250" not in set(cleaned["sid"])
    crossing = by_sid(cleaned, "2012200N15175")
    assert len(crossing) == 21
    assert (crossing["_basin"] == "EP").any()


def test_non_ts_records_removed(cleaned):
    storm = by_sid(cleaned, "2013200N15140")
    assert len(storm) == 21 - 2  # 16/18 两个 synoptic 点为 ET
    assert set(storm["nature"]) == {"TS"}


def test_longitude_normalized_to_minus180_180(cleaned):
    assert cleaned["lon"].between(*schema.LON_RANGE).all()
    crossing = by_sid(cleaned, "2012200N15175")
    assert (crossing["lon"] < 0).any() and (crossing["lon"] > 170).any()


# ---------------------------------------------------------------------------
# 切段、滑窗、按 SID 划分
# ---------------------------------------------------------------------------
def test_segments_break_on_non_ts_gap(cleaned):
    segments = prepare_data.split_continuous_segments(cleaned, schema.TIME_STEP_HOURS)
    lengths = sorted(len(s) for s in segments if s["sid"].iloc[0] == "2013200N15140")
    assert lengths == [8, 11]  # 被 ET 段切开，两段都不足 17 点


def test_prepare_end_to_end_shapes_and_split(config):
    bundle = prepare_data.prepare(config)
    window = schema.INPUT_LENGTH + schema.OUTPUT_LENGTH
    # A、D、F、G、H 各 21 点 → 每个 21 - 17 + 1 = 5 个样本；E 无样本
    assert bundle.stats["sample_count"] == 5 * (21 - window + 1)
    for x, y in (
        (bundle.x_train, bundle.y_train),
        (bundle.x_val, bundle.y_val),
        (bundle.x_test, bundle.y_test),
    ):
        assert x.shape[1:] == (schema.INPUT_LENGTH, schema.FEATURE_DIM)
        assert y.shape[1:] == (schema.OUTPUT_LENGTH, schema.FEATURE_DIM)
        assert len(x) == len(y)
    train, val, test = map(set, (bundle.train_sids, bundle.val_sids, bundle.test_sids))
    assert not (train & val) and not (train & test) and not (val & test)
    assert bundle.stats["caliber"]["wind_column"] == "USA_WIND"
    assert bundle.stats["wind_unit_detected"] == "kt"


# ---------------------------------------------------------------------------
# 数据卡片需要披露的统计（气压来源、跨 180° 样本）
# ---------------------------------------------------------------------------
def test_stats_record_final_pressure_sources(config):
    bundle = prepare_data.prepare(config)
    counts = bundle.stats["pressure_source_counts"]
    assert counts.get("fallback:WMO_PRES") == 1
    assert sum(counts.values()) == bundle.stats["cleaned_rows"]


def test_stats_flag_dateline_crossing_samples(config):
    """D 台风东传越过 180°：它的 5 个样本窗口都含 +179 → -179 跳变，其余台风没有。"""
    bundle = prepare_data.prepare(config)
    dateline = bundle.stats["dateline_crossing"]
    assert dateline["sids"] == ["2012200N15175"]
    assert dateline["sample_count"] == 5


def run_verify(config) -> tuple[int, list[str]]:
    import subprocess
    import sys
    from pathlib import Path

    prepare_data.save(prepare_data.prepare(config), config)
    script = Path(prepare_data.BACKEND_DIR) / "scripts" / "verify_dataset.py"
    result = subprocess.run(
        [sys.executable, str(script), "--processed-dir", str(config.processed_dir)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=prepare_data.BACKEND_DIR,
    )
    failed = [line for line in result.stdout.splitlines() if line.startswith("[FAIL]")]
    return result.returncode, failed


def test_verify_dataset_passes_without_dateline_storm(tmp_path):
    """对照组：去掉跨 180° 台风后 verify_dataset 应全部通过（新检查不误报）。"""
    rows = fx.build_default_rows(include_dateline=False)
    config = TrainConfig(
        input_path=fx.write_csv(tmp_path / "no_dateline.csv", rows),
        processed_dir=tmp_path / "processed",
    )
    returncode, failed = run_verify(config)
    assert returncode == 0 and not failed, failed


def test_verify_dataset_flags_dateline_storm(config):
    """实验组：含跨 180° 台风时，"窗口内经度跳变"检查必须失败。"""
    returncode, failed = run_verify(config)
    assert returncode == 1
    assert any("跨 180°" in line for line in failed), failed
