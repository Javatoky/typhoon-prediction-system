"""数据集导入服务测试（FR-013、UC-07：校验未通过整批回滚）。"""

from __future__ import annotations

import pytest

from app.services import dataset_service

IBTRACS_CSV = (
    "SID,SEASON,BASIN,NAME,ISO_TIME,LAT,LON,USA_WIND,USA_PRES\n"
    "WP2020001,2020,WP,Demo,2020-08-01 00:00:00,15.0,130.0,40,1000\n"
    "WP2020001,2020,WP,Demo,2020-08-01 06:00:00,15.9,129.2,45,996\n"
    "WP2020001,2020,WP,Demo,2020-08-01 12:00:00,16.8,128.4,50,992\n"
    "WP2020002,2020,WP,Demo2,2020-08-03 00:00:00,18.0,135.0,35,1002\n"
    "WP2020002,2020,WP,Demo2,2020-08-03 06:00:00,18.6,134.0,40,998\n"
).encode("utf-8")

STANDARD_CSV = (
    "sid,iso_time,lat,lon,max_wind_mps,pressure_hpa\n"
    "WP2021001,2021-07-01T00:00:00Z,16.0,132.0,20.0,1000\n"
    "WP2021001,2021-07-01T06:00:00Z,16.5,131.0,22.0,998\n"
).encode("utf-8")


def test_parse_ibtracs_columns_and_wind_unit():
    header, rows, wind_unit = dataset_service.parse_dataset_csv(IBTRACS_CSV)
    assert "SID" in header
    assert wind_unit == "kt"  # USA_WIND 单位为 knot
    assert {
        "sid",
        "iso_time",
        "lat",
        "lon",
        "max_wind_mps",
        "pressure_hpa",
    } <= set(rows[0].keys())


def test_parse_standard_columns_use_mps():
    _header, _rows, wind_unit = dataset_service.parse_dataset_csv(STANDARD_CSV)
    assert wind_unit == "mps"


def test_parse_missing_required_column_raises():
    bad = b"name,iso_time\nDemo,2020-08-01 00:00:00\n"
    with pytest.raises(Exception) as excinfo:
        dataset_service.parse_dataset_csv(bad)
    assert "缺少必需列" in str(excinfo.value)


def test_import_rows_converts_knots_and_builds_typhoons(app, admin_user):
    from app.models import TrackPoint, Typhoon

    with app.app_context():
        header, rows, wind_unit = dataset_service.parse_dataset_csv(IBTRACS_CSV)
        record, stats = dataset_service.import_rows(
            admin_user,
            rows=rows,
            header=header,
            wind_unit=wind_unit,
            source="IBTrACS",
            source_version="v04r01",
            original_filename="test.csv",
            stored_filename=None,
            file_size_bytes=len(IBTRACS_CSV),
        )
        assert record.status == "imported"
        assert stats.typhoon_count == 2
        assert stats.point_count == 5

        typhoon = Typhoon.query.filter_by(sid="WP2020001").first()
        assert typhoon is not None
        assert typhoon.season == 2020
        assert typhoon.name == "Demo"
        assert typhoon.track_point_count == 3
        # 40 knot ≈ 20.6 m/s
        assert typhoon.points[0].max_wind_mps == pytest.approx(20.59, abs=0.05)
        assert TrackPoint.query.count() == 5


def test_import_records_audit_entry(app, admin_user):
    from app.models import AuditLog

    with app.app_context():
        header, rows, wind_unit = dataset_service.parse_dataset_csv(STANDARD_CSV)
        dataset_service.import_rows(
            admin_user,
            rows=rows,
            header=header,
            wind_unit=wind_unit,
            source="standard",
            source_version=None,
            original_filename="s.csv",
            stored_filename=None,
            file_size_bytes=None,
        )
        assert AuditLog.query.filter_by(action="admin.dataset_import").count() == 1


def test_strict_mode_rolls_back_whole_batch(app, admin_user):
    from app.models import DatasetImport, Typhoon

    bad_csv = (
        "SID,ISO_TIME,LAT,LON,USA_WIND,USA_PRES\n"
        "WP2020003,2020-08-01 00:00:00,15.0,130.0,40,1000\n"
        "WP2020003,,15.9,129.2,45,996\n"  # 时间缺失 -> 严格模式下应整批回滚
    ).encode("utf-8")

    with app.app_context():
        header, rows, wind_unit = dataset_service.parse_dataset_csv(bad_csv)
        with pytest.raises(Exception) as excinfo:
            dataset_service.import_rows(
                admin_user,
                rows=rows,
                header=header,
                wind_unit=wind_unit,
                source="IBTrACS",
                source_version=None,
                original_filename="bad.csv",
                stored_filename=None,
                file_size_bytes=None,
                strict=True,
            )
        assert "整批回滚" in str(excinfo.value)
        assert Typhoon.query.filter_by(sid="WP2020003").first() is None
        assert DatasetImport.query.order_by(DatasetImport.id.desc()).first().status == "rolled_back"


def test_non_strict_mode_skips_bad_rows(app, admin_user):
    from app.models import Typhoon

    csv_bytes = (
        "SID,ISO_TIME,LAT,LON,USA_WIND,USA_PRES\n"
        "WP2020004,2020-08-01 00:00:00,15.0,130.0,40,1000\n"
        "WP2020004,not-a-time,15.9,129.2,45,996\n"
    ).encode("utf-8")

    with app.app_context():
        header, rows, wind_unit = dataset_service.parse_dataset_csv(csv_bytes)
        _record, stats = dataset_service.import_rows(
            admin_user,
            rows=rows,
            header=header,
            wind_unit=wind_unit,
            source="IBTrACS",
            source_version=None,
            original_filename="partial.csv",
            stored_filename=None,
            file_size_bytes=None,
            strict=False,
        )
        assert stats.skipped_row_count == 1
        assert Typhoon.query.filter_by(sid="WP2020004").first() is not None


def test_basin_filter_skips_other_basins(app, admin_user):
    from app.models import Typhoon

    csv_bytes = (
        "SID,ISO_TIME,LAT,LON,USA_WIND,USA_PRES,BASIN\n"
        "EP012020,2020-08-01 00:00:00,15.0,-130.0,40,1000,EP\n"
        "WP2020005,2020-08-01 00:00:00,15.0,130.0,40,1000,WP\n"
    ).encode("utf-8")

    with app.app_context():
        header, rows, wind_unit = dataset_service.parse_dataset_csv(csv_bytes)
        _record, stats = dataset_service.import_rows(
            admin_user,
            rows=rows,
            header=header,
            wind_unit=wind_unit,
            source="IBTrACS",
            source_version=None,
            original_filename="basin.csv",
            stored_filename=None,
            file_size_bytes=None,
        )
        assert stats.typhoon_count == 1
        assert Typhoon.query.filter_by(sid="WP2020005").first() is not None
        assert Typhoon.query.filter_by(sid="EP012020").first() is None
