"""上传校验、预测任务与导出接口测试（FR-004 ~ FR-011）。"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest


@pytest.fixture()
def typhoon(app):
    """在测试库中插入一个 24 点的历史台风（对应 AT-02 / AT-04）。"""
    from app.extensions import db
    from app.models import TrackPoint, Typhoon
    from app.utils.intensity import classify_intensity

    with app.app_context():
        record = Typhoon(
            sid="WP2020001",
            name="DEMO",
            season=2020,
            basin="WP",
            source="test",
        )
        db.session.add(record)
        db.session.flush()

        start = datetime(2020, 8, 1, 0, 0, 0)
        lat, lon, wind, pressure = 15.0, 130.0, 20.0, 1000.0
        for step in range(24):
            lat += 0.9
            lon -= 0.8
            wind += 1.5
            pressure -= 1.5
            code, _name = classify_intensity(wind)
            db.session.add(
                TrackPoint(
                    typhoon_id=record.id,
                    iso_time=start + timedelta(hours=6 * step),
                    lat=round(lat, 2),
                    lon=round(lon, 2),
                    max_wind_mps=round(wind, 1),
                    central_pressure_hpa=round(pressure, 1),
                    quality_flag="original",
                    intensity_category=code,
                    in_focus_area=True,
                )
            )
        record.start_time = start
        record.end_time = start + timedelta(hours=6 * 23)
        record.track_point_count = 24
        record.max_wind_mps = round(wind, 1)
        record.min_pressure_hpa = round(pressure, 1)
        record.in_focus_area = True
        db.session.commit()
        typhoon_id = record.id

    # 返回脱离会话的对象快照，避免把上下文绑定的实例传出夹具
    with app.app_context():
        loaded = db.session.get(Typhoon, typhoon_id)
        db.session.expunge(loaded)
        return loaded


def test_typhoon_list_and_track(logged_in_client, typhoon):
    listing = logged_in_client.get("/api/typhoons?keyword=DEMO")
    assert listing.status_code == 200
    data = listing.get_json()["data"]
    assert len(data) == 1
    assert data[0]["sid"] == "WP2020001"

    track = logged_in_client.get(f"/api/typhoons/{typhoon.id}/track")
    assert track.status_code == 200
    payload = track.get_json()["data"]
    assert payload["point_count"] == 24
    assert payload["points"][0]["intensity_code"] in {"TD", "TS", "STS", "TY", "STY", "SuperTY", "NA"}


def test_empty_search_returns_empty_not_error(logged_in_client):
    response = logged_in_client.get("/api/typhoons?keyword=NOT-EXIST-9999")
    assert response.status_code == 200
    assert response.get_json()["data"] == []


def test_upload_valid_csv_saves_points(logged_in_client, sample_csv_bytes):
    response = logged_in_client.post(
        "/api/uploads/track",
        data={"file": (open_bytes(sample_csv_bytes), "track.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 201, response.get_json()
    data = response.get_json()["data"]
    assert data["upload"]["row_count"] == 5
    assert data["upload"]["valid_point_count"] == 5
    assert data["validation"]["status"] in {"pass", "warning"}
    assert data["validation"]["ok"] is True


def test_upload_invalid_csv_returns_row_level_errors(logged_in_client):
    bad = (
        "sid,iso_time,lat,lon,max_wind_mps,pressure_hpa\n"
        "WP202601,2026-07-18T00:00:00Z,18.2,126.5,28.0,985\n"
        "WP202601,not-a-time,18.8,125.7,30.5,980\n"
        "WP202601,2026-07-18T12:00:00Z,120.0,124.8,33.0,975\n"
        "WP202601,2026-07-18T18:00:00Z,20.3,123.9,abc,970\n"
        "WP202601,2026-07-19T00:00:00Z,21.1,123.0,37.0,500\n"
    ).encode("utf-8")
    response = logged_in_client.post(
        "/api/uploads/track",
        data={"file": (open_bytes(bad), "bad.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 422
    error = response.get_json()["error"]
    assert error["code"] == "validation_error"
    codes = {issue["code"] for issue in error["details"]["errors"]}
    assert {"DQ01_TIME_UNPARSABLE", "DQ02_LAT_RANGE", "DQ03_WIND_INVALID", "DQ03_PRESSURE_RANGE"} <= codes
    assert any(issue["row"] for issue in error["details"]["errors"])


def test_upload_rejects_wrong_extension(logged_in_client, sample_csv_bytes):
    response = logged_in_client.post(
        "/api/uploads/track",
        data={"file": (open_bytes(sample_csv_bytes), "track.txt")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 422


def test_oversize_upload_rejected_before_parsing(logged_in_client, app):
    app.config["MAX_UPLOAD_MB"] = 1
    payload = b"sid,iso_time,lat,lon,max_wind_mps,pressure_hpa\n" + b"x" * (1024 * 1024 + 10)
    response = logged_in_client.post(
        "/api/uploads/track",
        data={"file": (open_bytes(payload), "big.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 413


def test_validate_only_endpoint_does_not_save(logged_in_client, sample_csv_bytes, app):
    from app.models import Upload

    response = logged_in_client.post(
        "/api/uploads/track/validate",
        data={"file": (open_bytes(sample_csv_bytes), "track.csv")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert response.get_json()["data"]["saved"] is False
    with app.app_context():
        assert Upload.query.count() == 0


def test_create_prediction_from_typhoon(logged_in_client, typhoon):
    response = logged_in_client.post("/api/predictions", json={"typhoon_id": typhoon.id})
    assert response.status_code == 201, response.get_json()
    data = response.get_json()["data"]
    assert data["status"] == "succeeded", data.get("error_summary")
    assert len(data["points"]) == 12
    # 12 个时效点，间隔 6 小时，最长 72 小时（3.2 模型输出）
    assert [point["forecast_hour"] for point in data["points"]] == [
        6, 12, 18, 24, 30, 36, 42, 48, 54, 60, 66, 72
    ]
    assert data["points"][0]["intensity_category"]
    assert data["data_version"] == "test"
    assert data["input_window"]["feature_order"] == [
        "lat",
        "lon",
        "max_wind_mps",
        "central_pressure_hpa",
    ]


def test_prediction_key_hours_flagged(logged_in_client, typhoon):
    data = logged_in_client.post("/api/predictions", json={"typhoon_id": typhoon.id}).get_json()[
        "data"
    ]
    key = [point["forecast_hour"] for point in data["points"] if point["is_key_hour"]]
    assert key == [6, 12, 24, 48, 72]


def test_prediction_records_baseline_model_version(logged_in_client, typhoon, app):
    """NFR-012：任务必须记录模型版本；未训练模型时记录内置基线版本。"""
    from app.services import model_service

    with app.app_context():
        baseline = model_service.ensure_baseline_version(None, activate=True)
        baseline_id = baseline.id

    data = logged_in_client.post("/api/predictions", json={"typhoon_id": typhoon.id}).get_json()[
        "data"
    ]
    assert data["model_version_id"] == baseline_id
    assert data["status"] == "succeeded"


def test_create_prediction_requires_exactly_one_source(logged_in_client):
    assert logged_in_client.post("/api/predictions", json={}).status_code == 422
    assert (
        logged_in_client.post("/api/predictions", json={"typhoon_id": 1, "upload_id": 2}).status_code
        == 422
    )


def test_prediction_is_private_to_owner(app, typhoon, admin_user, normal_user):
    from app.services import user_service
    from tests.conftest import (
        ADMIN_PASSWORD,
        ADMIN_USERNAME,
        STUDENT_PASSWORD,
        STUDENT_USERNAME,
        login_as,
    )

    owner = login_as(app, STUDENT_USERNAME, STUDENT_PASSWORD)
    created = owner.post("/api/predictions", json={"typhoon_id": typhoon.id}).get_json()["data"]
    task_id = created["id"]

    # 管理员可以查看任何任务（7 权限要求：任务所有者/管理员）
    admin = login_as(app, ADMIN_USERNAME, ADMIN_PASSWORD)
    assert admin.get(f"/api/predictions/{task_id}").status_code == 200

    # 其他普通用户按“不存在”处理，避免泄露他人任务存在性
    with app.app_context():
        user_service.create_user(admin_user, username="student09", password="Student@999")
    other = login_as(app, "student09", "Student@999")
    assert other.get(f"/api/predictions/{task_id}").status_code == 404
    assert other.get(f"/api/predictions/{task_id}/export").status_code == 404


def test_export_csv_contains_metadata_and_points(logged_in_client, typhoon):
    created = logged_in_client.post(
        "/api/predictions", json={"typhoon_id": typhoon.id}
    ).get_json()["data"]
    response = logged_in_client.get(f"/api/predictions/{created['id']}/export")
    assert response.status_code == 200
    assert "text/csv" in response.headers["Content-Type"]
    assert "attachment" in response.headers["Content-Disposition"]
    text = response.data.decode("utf-8-sig")
    assert "task_code" in text
    assert "forecast_hour" in text
    assert created["task_code"] in text
    assert len([line for line in text.splitlines() if line.startswith(created["task_code"])]) == 12


def test_task_statistics(logged_in_client, typhoon):
    logged_in_client.post("/api/predictions", json={"typhoon_id": typhoon.id})
    response = logged_in_client.get("/api/predictions/statistics")
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["task_total"] >= 1
    assert data["task_succeeded"] >= 1


def test_task_status_choices(logged_in_client):
    response = logged_in_client.get("/api/predictions/status-choices")
    values = [item["value"] for item in response.get_json()["data"]["statuses"]]
    assert values == ["created", "validating", "running", "succeeded", "failed"]


def open_bytes(payload: bytes):
    import io

    return io.BytesIO(payload)
