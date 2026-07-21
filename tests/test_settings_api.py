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


def test_model_info_registry_hit(authed_client):
    # A known model resolves: in registry, priced, reasoning flag populated (#13).
    resp = authed_client.get("/api/settings/model-info?model=gpt-4o")
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == "gpt-4o"
    assert data["in_registry"] is True
    assert data["input_price_per_1m"] is not None
    assert data["output_price_per_1m"] is not None
    assert data["supports_reasoning"] is False  # gpt-4o is not a reasoning model


def test_model_info_reasoning_model(authed_client):
    resp = authed_client.get("/api/settings/model-info?model=gemini/gemini-3-flash-preview")
    assert resp.status_code == 200
    assert resp.json()["supports_reasoning"] is True


def test_model_info_unknown_model(authed_client):
    # Self-hosted / free-text id litellm can't map: no crash, flags false, no price.
    resp = authed_client.get("/api/settings/model-info?model=totally/unknown-xyz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["in_registry"] is False
    assert data["supports_reasoning"] is False
    assert data["input_price_per_1m"] is None


def test_model_info_defaults_to_configured_model(authed_client):
    # No ?model= -> uses the configured llm_model setting.
    resp = authed_client.get("/api/settings/model-info")
    assert resp.status_code == 200
    assert resp.json()["model"] == "gemini/gemini-3-flash-preview"


def test_model_info_requires_auth(app):
    resp = TestClient(app).get("/api/settings/model-info?model=gpt-4o")
    assert resp.status_code == 401
