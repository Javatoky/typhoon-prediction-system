"""数据质量校验器测试（DQ-01 ~ DQ-05、FR-005）。"""

from __future__ import annotations

import pytest

from app.ml.validator import STATUS_FAIL, STATUS_WARN, build_validation_report, validate_csv_rows


def _rows(count=5):
    start = [
        ("2026-07-18T00:00:00Z", 18.2, 126.5, 28.0, 985),
        ("2026-07-18T06:00:00Z", 18.8, 125.7, 30.5, 980),
        ("2026-07-18T12:00:00Z", 19.5, 124.8, 33.0, 975),
        ("2026-07-18T18:00:00Z", 20.3, 123.9, 35.0, 970),
        ("2026-07-19T00:00:00Z", 21.1, 123.0, 37.0, 965),
    ][:count]
    return [
        {
            "sid": "WP202601",
            "iso_time": iso,
            "lat": lat,
            "lon": lon,
            "max_wind_mps": wind,
            "pressure_hpa": pressure,
        }
        for iso, lat, lon, wind, pressure in start
    ]


def test_valid_rows_pass_and_build_window():
    report = build_validation_report(_rows())
    assert report.ok
    assert report.window and len(report.window) == 5
    assert report.window[-1][0] == 21.1
    assert report.sid == "WP202601"


def test_time_unparsable_is_error():
    rows = _rows()
    rows[1]["iso_time"] = "not-a-time"
    report = build_validation_report(rows)
    assert not report.ok
    assert any(issue.code == "DQ01_TIME_UNPARSABLE" for issue in report.issues)


def test_time_step_must_be_six_hours():
    rows = _rows()
    rows[2]["iso_time"] = "2026-07-18T13:00:00Z"
    report = build_validation_report(rows)
    assert not report.ok
    assert any(issue.code in {"DQ01_TIME_STEP", "DQ05_GAP_TOO_LARGE"} for issue in report.issues)


def test_lat_out_of_range_is_error():
    rows = _rows()
    rows[0]["lat"] = 120.0
    report = build_validation_report(rows)
    assert not report.ok
    assert any(issue.code == "DQ02_LAT_RANGE" for issue in report.issues)
    # 行号应指向 Excel 中的数据行（首行数据为第 2 行）
    assert any(issue.row == 2 for issue in report.issues if issue.code == "DQ02_LAT_RANGE")


def test_lon_0_360_is_converted():
    rows = _rows()
    # 126.5°E 以 0~360 口径表示为 126.5；再构造两个 >180 的值以触发口径识别
    rows[0]["lon"] = 246.5  # 等价于 -113.5
    rows[1]["lon"] = 240.0
    report = build_validation_report(rows)
    assert report.ok
    assert report.lon_converted
    assert report.lon_range_detected == "0-360"
    assert all(-180.0 <= point.lon <= 180.0 for point in report.points)
    assert report.points[0].lon == pytest.approx(-113.5, abs=1e-6)


def test_wind_not_numeric_is_error():
    rows = _rows()
    rows[3]["max_wind_mps"] = "abc"
    report = build_validation_report(rows)
    assert not report.ok
    assert any(issue.code == "DQ03_WIND_INVALID" for issue in report.issues)


def test_pressure_out_of_range_is_error():
    rows = _rows()
    rows[2]["pressure_hpa"] = 500
    report = build_validation_report(rows)
    assert not report.ok
    assert any(issue.code == "DQ03_PRESSURE_RANGE" for issue in report.issues)


def test_insufficient_points_reports_positions():
    rows = _rows(count=3)
    report = build_validation_report(rows)
    assert not report.ok
    assert any(issue.code == "DQ04_INSUFFICIENT_POINTS" for issue in report.issues)


def test_single_gap_is_interpolated_and_flagged():
    rows = _rows()
    del rows[2]  # 造成 12 小时缺口
    report = build_validation_report(rows)
    assert report.ok, [issue.to_dict() for issue in report.issues]
    assert report.interpolated_count == 1
    assert report.status == STATUS_WARN
    assert any(point.quality_flag == "interpolated" for point in report.points)
    assert report.window  # 插值后仍能构成窗口


def test_multi_point_sid_is_rejected():
    rows = _rows()
    rows[3]["sid"] = "WP202602"
    report = build_validation_report(rows)
    assert not report.ok
    assert any(issue.code == "MULTI_SID" for issue in report.issues)


def test_knot_unit_auto_converted():
    rows = _rows()
    for row in rows:
        # 28 m/s 以 knot 表示为约 54.4 kt；这里改成真实台风量级（100 kt 上下）以触发自动换算
        row["max_wind_mps"] = round((row["max_wind_mps"] + 25.0) / 0.514444, 1)
    report = build_validation_report(rows)
    assert report.converted_wind_unit == "kt->m/s"
    assert abs(report.points[0].max_wind_mps - 53.0) < 1.0


def test_explicit_wind_unit_kt():
    rows = _rows()
    for row in rows:
        row["max_wind_mps"] = 54.4  # knot
    report = build_validation_report(rows, wind_unit="kt")
    assert report.converted_wind_unit == "kt->m/s"
    assert report.points[0].max_wind_mps == pytest.approx(27.99, abs=0.05)


def test_physically_impossible_wind_rejected():
    rows = _rows()
    for row in rows:
        row["max_wind_mps"] = 300.0  # 既不是合理 m/s，也不是合理 knot
    report = build_validation_report(rows)
    assert not report.ok
    assert any(issue.code == "DQ03_WIND_RANGE" for issue in report.issues)


def test_validate_csv_rows_header_checks():
    rows = _rows()
    report = validate_csv_rows(
        [{"sid": "WP202601", "iso_time": "2026-07-18T00:00:00Z", "lat": 1, "lon": 1, "max_wind_mps": 20, "pressure_hpa": 1000}],
        header=["sid", "iso_time", "lat", "lon"],
    )
    assert not report.ok
    assert any(issue.code == "CSV_HEADER_MISSING" for issue in report.issues)


def test_validate_csv_rows_unknown_column_warning():
    rows = _rows()
    report = validate_csv_rows(
        rows,
        header=["sid", "iso_time", "lat", "lon", "max_wind_mps", "pressure_hpa", "remark"],
    )
    assert "remark" in report.unknown_columns
    assert report.ok
