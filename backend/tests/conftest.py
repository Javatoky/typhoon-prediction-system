"""pytest 共用夹具。

关键约定：
- 每个测试函数独立获取 app 与 client（函数级夹具），不跨测试共享会话 Cookie，
  否则会出现“上一个测试的登录态泄漏到下一个测试”的假失败。
- 测试环境使用内存 SQLite（TestingConfig），不触碰开发数据库与上传目录。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "Adm1n@2024"
STUDENT_USERNAME = "student01"
STUDENT_PASSWORD = "Student@123"


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """构造测试用 Flask 应用，并把运行期目录指向临时目录。

    注意：不能在 yield 期间保持一个长期存活的应用上下文。
    Flask 测试客户端在“已存在应用上下文”时保存会话的行为会出现偏差
    （例如 /auth/logout 之后会话仍未失效），因此这里只在建表/清表时压入上下文。
    """
    monkeypatch.setenv("DSH_APP_ENV", "testing")

    from app import create_app
    from app.extensions import db

    application = create_app("testing")
    application.config.update(
        UPLOAD_DIR=str(tmp_path / "uploads"),
        MODEL_STORE_DIR=str(tmp_path / "models"),
        EXPORT_DIR=str(tmp_path / "exports"),
        LOG_DIR=str(tmp_path / "logs"),
        WTF_CSRF_ENABLED=False,
    )
    Path(application.config["UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)
    Path(application.config["MODEL_STORE_DIR"]).mkdir(parents=True, exist_ok=True)

    with application.app_context():
        db.create_all()

    yield application

    with application.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    """未登录的测试客户端；与 app 夹具同为函数级，保证各测试互不影响。"""
    return app.test_client()


@pytest.fixture()
def admin_user(app):
    from app.services import user_service

    with app.app_context():
        user, _created = user_service.ensure_admin(ADMIN_USERNAME, ADMIN_PASSWORD)
        user_id = user.id
    return _reload_user(app, user_id)


@pytest.fixture()
def normal_user(app):
    from app.services import user_service

    with app.app_context():
        user, _password = user_service.create_user(
            None, username=STUDENT_USERNAME, password=STUDENT_PASSWORD, role="user"
        )
        user_id = user.id
    return _reload_user(app, user_id)


def _reload_user(app, user_id: int):
    """在独立应用上下文中读取用户对象，避免把上下文绑定的 ORM 实例传出。"""
    from app.extensions import db
    from app.models import User

    with app.app_context():
        user = db.session.get(User, user_id)
        db.session.expunge(user)  # 脱离会话，避免夹具结束后被回收成 DetachedInstanceError
        return user


def login_as(app, username: str, password: str):
    """在独立客户端上登录，返回已建立会话的客户端。"""
    test_client = app.test_client()
    response = test_client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.get_json()
    return test_client


@pytest.fixture()
def logged_in_client(app, normal_user):
    return login_as(app, STUDENT_USERNAME, STUDENT_PASSWORD)


@pytest.fixture()
def admin_client(app, admin_user):
    return login_as(app, ADMIN_USERNAME, ADMIN_PASSWORD)


@pytest.fixture()
def sample_rows():
    """一份合法的 5 点观测（对应附录A 结构）。"""
    return [
        {"sid": "WP202601", "iso_time": "2026-07-18T00:00:00Z", "lat": 18.2, "lon": 126.5, "max_wind_mps": 28.0, "pressure_hpa": 985},
        {"sid": "WP202601", "iso_time": "2026-07-18T06:00:00Z", "lat": 18.8, "lon": 125.7, "max_wind_mps": 30.5, "pressure_hpa": 980},
        {"sid": "WP202601", "iso_time": "2026-07-18T12:00:00Z", "lat": 19.5, "lon": 124.8, "max_wind_mps": 33.0, "pressure_hpa": 975},
        {"sid": "WP202601", "iso_time": "2026-07-18T18:00:00Z", "lat": 20.3, "lon": 123.9, "max_wind_mps": 35.0, "pressure_hpa": 970},
        {"sid": "WP202601", "iso_time": "2026-07-19T00:00:00Z", "lat": 21.1, "lon": 123.0, "max_wind_mps": 37.0, "pressure_hpa": 965},
    ]


@pytest.fixture()
def sample_csv_bytes(sample_rows):
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=["sid", "iso_time", "lat", "lon", "max_wind_mps", "pressure_hpa"],
    )
    writer.writeheader()
    writer.writerows(sample_rows)
    return buffer.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# 断言辅助（避免在断言里直接写中文常量，规避 Windows 控制台代码页干扰）
# ---------------------------------------------------------------------------
@pytest.fixture()
def error_message():
    """从统一错误响应中取出错误文案。"""

    def _extract(response) -> str:
        payload = response.get_json()
        assert payload["ok"] is False, payload
        return payload["error"]["message"]

    return _extract
