import pytest
import bcrypt
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.config import set_setting, init_settings


@pytest.fixture
def app(db_path, tmp_data_dir):
    """Create test app with initialized DB."""
    init_settings()
    pw_hash = bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode()
    set_setting("auth_password_hash", pw_hash)
    set_setting("auth_username", "admin")
    return create_app(str(tmp_data_dir), run_background=False)


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def authed_client(client):
    """Client with valid session cookie."""
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "testpass"})
    assert resp.status_code == 200
    return client


def test_login_success(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "testpass"})
    assert resp.status_code == 200
    assert "receiptory_session" in resp.cookies


def test_login_wrong_password(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert resp.status_code == 401


def test_login_wrong_username(client):
    resp = client.post("/api/auth/login", json={"username": "nobody", "password": "testpass"})
    assert resp.status_code == 401


def test_me_authenticated(authed_client):
    resp = authed_client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["username"] == "admin"


def test_me_unauthenticated(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
