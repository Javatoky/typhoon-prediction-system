"""阶段 3 抽查工具测试：逐行去向判定必须与 prepare_data 实际输出一致。"""

from __future__ import annotations

import pytest

from scripts import spot_check
from tests import ibtracs_fixture as fx
from training import prepare_data
from training.config import TrainConfig


@pytest.fixture()
def pipeline(tmp_path):
    config = TrainConfig(
        input_path=fx.write_csv(tmp_path / "raw.csv", fx.build_default_rows()),
        processed_dir=tmp_path / "processed",
    )
    raw, unit, _ = prepare_data.load_raw(config.input_path)
    cleaned = prepare_data.clean_frame(raw, config, unit)
    return config, raw, cleaned


@pytest.mark.parametrize(
    "sid",
    ["2010200N15140", "2012200N15175", "2013200N15140", "1995200N15140", "2014200N15140"],
)
def test_diagnosis_matches_pipeline(pipeline, sid):
    config, raw, cleaned = pipeline
    info = spot_check.describe_storm(sid, raw, cleaned, config)
    assert info["mismatch"] == [] and info["value_mismatch"] == 0


def test_drop_reasons_are_specific(pipeline):
    config, raw, cleaned = pipeline
    table = spot_check.describe_storm("2013200N15140", raw, cleaned, config)["table"]
    reasons = set(table["reason"])
    assert "D-03 非 synoptic 时刻" in reasons
    assert "D-06 NATURE≠TS" in reasons
    old = spot_check.describe_storm("1995200N15140", raw, cleaned, config)
    assert set(old["table"]["reason"]) == {"D-04 年份 < 2000"}


def test_storm_summary_counts(pipeline):
    config, raw, cleaned = pipeline
    a = spot_check.describe_storm("2010200N15140", raw, cleaned, config)
    assert (a["raw_rows"], a["kept_rows"], a["sample_count"], a["fallback_rows"]) == (41, 21, 5, 1)
    assert not a["crosses_dateline"]
    assert spot_check.describe_storm("2012200N15175", raw, cleaned, config)["crosses_dateline"]


def test_self_check_detects_pipeline_divergence(pipeline):
    """反例：把管线输出删掉一行，自检必须报"不一致"。"""
    config, raw, cleaned = pipeline
    tampered = cleaned.drop(cleaned[cleaned["sid"] == "2010200N15140"].index[3])
    info = spot_check.describe_storm("2010200N15140", raw, tampered, config)
    assert len(info["mismatch"]) == 1


def test_pick_sids_is_stratified_and_reproducible():
    meta = {"splits": {"train_sids": list("abcdefgh"), "val_sids": ["v1", "v2"], "test_sids": ["t1", "t2"]}}
    first = spot_check.pick_sids(meta, 5, seed=1)
    assert first == spot_check.pick_sids(meta, 5, seed=1)
    assert len(first) == 5 and {split for _sid, split in first} == {"train", "val", "test"}
    excluded = spot_check.pick_sids(meta, 5, seed=1, exclude={"v1", "a", "b"})
    assert len(excluded) == 5 and not {"v1", "a", "b"} & {sid for sid, _ in excluded}


def test_main_writes_package(pipeline, tmp_path, monkeypatch):
    config, _raw, _cleaned = pipeline
    prepare_data.save(prepare_data.prepare(config), config)
    out = tmp_path / "spot"
    monkeypatch.setattr(
        "sys.argv",
        ["spot_check.py", "--input", str(config.input_path), "--processed-dir",
         str(config.processed_dir), "--out", str(out), "--n", "5", "--include-flagged"],
    )
    assert spot_check.main() == 0
    index = (out / "README.md").read_text(encoding="utf-8")
    assert "2012200N15175" in index  # 跨 180° 台风被 --include-flagged 加入
    # 重点台风不挤占随机名额：合成数据中有样本的台风只有 5 个（A/D/F/G/H），应全部被抽到
    assert len(list(out.glob("*.md"))) - 1 == 5
