"""认证与权限接口测试（FR-001/002/012、NFR-008）。

注意：断言使用错误码与密码业务规则本身（纯 ASCII），
这样在 Windows GBK 控制台下失败信息也可读。
"""

from __future__ import annotations

from tests.conftest import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    STUDENT_PASSWORD,
    STUDENT_USERNAME,
    login_as,
)


def test_health_endpoint_needs_no_login(client):
    response = client.get("/api/system/health")
    assert response.status_code == 200
    assert response.get_json()["data"]["status"] == "ok"


def test_login_required_for_business_api(client):
    response = client.get("/api/typhoons")
    assert response.status_code == 401
    body = response.get_json()
    assert body["ok"] is False
    assert body["error"]["code"] == "unauthorized"


def test_login_success_and_me(client, normal_user):
    response = client.post(
        "/api/auth/login", json={"username": STUDENT_USERNAME, "password": STUDENT_PASSWORD}
    )
    assert response.status_code == 200
    payload = response.get_json()["data"]
    assert payload["user"]["username"] == STUDENT_USERNAME
    assert payload["user"]["role"] == "user"
    assert payload["session_idle_minutes"] == 30

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.get_json()["data"]["user"]["username"] == STUDENT_USERNAME


def test_login_wrong_password_unified_message(client, normal_user, error_message):
    response = client.post(
        "/api/auth/login", json={"username": STUDENT_USERNAME, "password": "wrong-password"}
    )
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "invalid_credentials"
    wrong_password_message = error_message(response)

    missing = client.post("/api/auth/login", json={"username": "nobody", "password": "whatever"})
    assert missing.status_code == 401
    # 6.2：不得暴露账号是否存在，两种失败情况的提示必须完全一致
    assert missing.get_json()["error"]["message"] == wrong_password_message


def test_login_lockout_after_five_failures(client, normal_user):
    for _ in range(5):
        client.post(
            "/api/auth/login", json={"username": STUDENT_USERNAME, "password": "bad-password"}
        )
    response = client.post(
        "/api/auth/login", json={"username": STUDENT_USERNAME, "password": STUDENT_PASSWORD}
    )
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "account_locked"


def test_disabled_account_cannot_login(client, normal_user, app):
    from app.services import user_service

    with app.app_context():
        admin, _created = user_service.ensure_admin(ADMIN_USERNAME, ADMIN_PASSWORD)
        user_service.update_user(admin, normal_user.id, status="disabled")

    response = client.post(
        "/api/auth/login", json={"username": STUDENT_USERNAME, "password": STUDENT_PASSWORD}
    )
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "account_disabled"


def test_change_password_flow(client, normal_user):
    client.post(
        "/api/auth/login", json={"username": STUDENT_USERNAME, "password": STUDENT_PASSWORD}
    )
    weak = client.post(
        "/api/auth/change-password",
        json={"current_password": STUDENT_PASSWORD, "new_password": "123"},
    )
    assert weak.status_code == 422
    assert weak.get_json()["error"]["code"] == "validation_error"

    mismatch = client.post(
        "/api/auth/change-password",
        json={
            "current_password": STUDENT_PASSWORD,
            "new_password": "NewStudent@456",
            "confirm_password": "Other@456",
        },
    )
    assert mismatch.status_code == 422

    ok = client.post(
        "/api/auth/change-password",
        json={"current_password": STUDENT_PASSWORD, "new_password": "NewStudent@456"},
    )
    assert ok.status_code == 200

    client.post("/api/auth/logout")
    relogin = client.post(
        "/api/auth/login", json={"username": STUDENT_USERNAME, "password": "NewStudent@456"}
    )
    assert relogin.status_code == 200


def test_password_must_not_contain_username(app):
    """密码策略：不得包含用户名（与种子密码同源的真实缺陷回归测试）。"""
    from app.core.errors import ValidationError
    from app.core.security import validate_password_strength

    with app.app_context():
        try:
            validate_password_strength("Admin@12345", username="admin")
        except ValidationError:
            pass
        else:  # pragma: no cover
            raise AssertionError("包含用户名的密码必须被拒绝")


def test_logout_invalidates_session(client, normal_user):
    client.post(
        "/api/auth/login", json={"username": STUDENT_USERNAME, "password": STUDENT_PASSWORD}
    )
    assert client.get("/api/auth/me").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_normal_user_cannot_access_admin_api(logged_in_client):
    response = logged_in_client.get("/api/admin/users")
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "forbidden"


def test_admin_can_create_and_list_users(admin_client):
    created = admin_client.post(
        "/api/admin/users", json={"username": "student02", "display_name": "No.2"}
    )
    assert created.status_code == 201
    body = created.get_json()["data"]
    assert body["user"]["username"] == "student02"
    assert body["initial_password"]

    listing = admin_client.get("/api/admin/users")
    assert listing.status_code == 200
    usernames = [item["username"] for item in listing.get_json()["data"]]
    assert {"admin", "student02"} <= set(usernames)


def test_duplicate_username_rejected(admin_client):
    admin_client.post("/api/admin/users", json={"username": "student03"})
    again = admin_client.post("/api/admin/users", json={"username": "student03"})
    assert again.status_code == 409
    assert again.get_json()["error"]["code"] == "conflict"


def test_admin_cannot_disable_last_admin(admin_client, admin_user):
    response = admin_client.put(f"/api/admin/users/{admin_user.id}", json={"status": "disabled"})
    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "validation_error"


def test_admin_can_reset_password_and_login(admin_client, normal_user):
    reset = admin_client.post(f"/api/admin/users/{normal_user.id}/reset-password", json={})
    assert reset.status_code == 200
    new_password = reset.get_json()["data"]["new_password"]

    fresh = admin_client.application.test_client()
    login = fresh.post(
        "/api/auth/login", json={"username": STUDENT_USERNAME, "password": new_password}
    )
    assert login.status_code == 200


def test_password_never_returned_in_user_payload(admin_client):
    admin_client.post("/api/admin/users", json={"username": "student04"})
    listing = admin_client.get("/api/admin/users").get_json()["data"]
    assert listing
    assert all(
        "password" not in item and "password_hash" not in item and "initial_password" not in item
        for item in listing
    )


def test_system_config_requires_login(client, app, normal_user):
    assert client.get("/api/system/config").status_code == 401

    authed = login_as(app, STUDENT_USERNAME, STUDENT_PASSWORD)
    response = authed.get("/api/system/config")
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert [item["name"] for item in data["features"]] == [
        "lat",
        "lon",
        "max_wind_mps",
        "central_pressure_hpa",
    ]
    assert data["key_forecast_hours"] == [6, 12, 24, 48, 72]
    assert data["limits"]["max_upload_mb"] == 20
    assert data["disclaimer"].strip() != ""
    assert len(data["intensity_thresholds"]) == 6


def test_admin_logs_record_login_and_user_creation(admin_client, admin_user):
    admin_client.post("/api/admin/users", json={"username": "student05"})
    logs = admin_client.get("/api/admin/logs").get_json()["data"]
    actions = {item["action"] for item in logs}
    assert "auth.login" in actions
    assert "admin.user_create" in actions
    assert all("password" not in (item["detail"] or "").lower() for item in logs)
