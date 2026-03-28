import pytest
import bcrypt
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.config import init_settings, set_setting


@pytest.fixture
def app(db_path, tmp_data_dir):
    init_settings()
    pw_hash = bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode()
    set_setting("auth_password_hash", pw_hash)
    return create_app(str(tmp_data_dir), run_background=False)


@pytest.fixture
def authed_client(app):
    client = TestClient(app)
    client.post("/api/auth/login", json={"username": "admin", "password": "testpass"})
    return client


def test_get_settings(authed_client):
    resp = authed_client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert "llm_model" in data
    assert "***" in str(data.get("auth_password_hash", ""))  # masked


def test_patch_settings(authed_client):
    resp = authed_client.patch("/api/settings", json={"settings": {"llm_model": "gpt-4o"}})
    assert resp.status_code == 200
    resp = authed_client.get("/api/settings")
    assert resp.json()["llm_model"] == "gpt-4o"


def test_queue_status(authed_client):
    resp = authed_client.get("/api/queue/status")
    assert resp.status_code == 200
    assert "pending" in resp.json()
