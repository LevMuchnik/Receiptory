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


def test_llm_models_lists_vision_chat_models(authed_client):
    # PR3: the picker registry — vision-capable chat models with prices (#13).
    resp = authed_client.get("/api/settings/llm-models")
    assert resp.status_code == 200
    models = resp.json()["models"]
    assert len(models) > 100  # ~749 in litellm 1.93
    ids = {m["id"] for m in models}
    # The default extraction model must be offerable in the picker.
    assert "gemini/gemini-3-flash-preview" in ids
    sample = next(m for m in models if m["id"] == "gemini/gemini-3-flash-preview")
    assert sample["supports_reasoning"] is True
    assert sample["input_price_per_1m"] is not None
    # Sorted by id, and every entry carries the picker's required shape.
    assert ids == set(sorted(ids))
    for m in models[:20]:
        assert set(m) >= {"id", "provider", "input_price_per_1m", "output_price_per_1m", "supports_reasoning"}


def test_llm_models_requires_auth(app):
    resp = TestClient(app).get("/api/settings/llm-models")
    assert resp.status_code == 401


def test_env_overrides_reports_pinned_keys(authed_client, monkeypatch):
    # A setting pinned by an env var must be reported so the UI can lock it (#13).
    monkeypatch.setenv("RECEIPTORY_LLM_MODEL", "gpt-4o")
    resp = authed_client.get("/api/settings/env-overrides")
    assert resp.status_code == 200
    assert "llm_model" in resp.json()["keys"]


def test_env_overrides_flags_auth_password_special_case(authed_client, monkeypatch):
    # verify_password checks plain-text RECEIPTORY_AUTH_PASSWORD before the
    # auth_password_hash setting, so the password field must be flagged even
    # though the env var name doesn't match the setting key (#13).
    monkeypatch.setenv("RECEIPTORY_AUTH_PASSWORD", "secret")
    resp = authed_client.get("/api/settings/env-overrides")
    assert "auth_password_hash" in resp.json()["keys"]


def test_env_overrides_empty_without_env(authed_client):
    # No RECEIPTORY_* env set (conftest clears them) -> nothing locked.
    resp = authed_client.get("/api/settings/env-overrides")
    assert resp.status_code == 200
    assert resp.json()["keys"] == []


def test_env_overrides_requires_auth(app):
    resp = TestClient(app).get("/api/settings/env-overrides")
    assert resp.status_code == 401


def test_llm_api_keys_add_list_and_mask(authed_client):
    # DB-managed keys (#25): add returns name + last4 only, never the secret.
    resp = authed_client.post("/api/settings/llm-api-keys", json={"name": "OpenAI", "key": "sk-secret-123456"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["keys"] == [{"name": "OpenAI", "last4": "3456"}]
    # default model is gemini/* -> provider resolves to gemini
    assert data["model_provider"] == "gemini"
    # secret value is never returned
    assert "sk-secret-123456" not in resp.text
    assert "secret" not in resp.text


def test_settings_get_never_leaks_key_material(authed_client):
    authed_client.post("/api/settings/llm-api-keys", json={"name": "OpenAI", "key": "sk-verysecret-abcdef"})
    resp = authed_client.get("/api/settings")
    assert "sk-verysecret-abcdef" not in resp.text
    assert "verysecret" not in resp.text


def test_llm_api_keys_replace_by_name_case_insensitive(authed_client):
    authed_client.post("/api/settings/llm-api-keys", json={"name": "openai", "key": "sk-old-1111"})
    resp = authed_client.post("/api/settings/llm-api-keys", json={"name": "OpenAI", "key": "sk-new-2222"})
    keys = resp.json()["keys"]
    assert len(keys) == 1
    assert keys[0]["name"] == "OpenAI" and keys[0]["last4"] == "2222"


def test_llm_api_keys_rejects_empty_name_or_key(authed_client):
    assert authed_client.post("/api/settings/llm-api-keys", json={"name": "  ", "key": "sk-x"}).status_code == 400
    assert authed_client.post("/api/settings/llm-api-keys", json={"name": "OpenAI", "key": ""}).status_code == 400


def test_llm_api_keys_rejects_slash_in_name(authed_client):
    # A slash would make the entry unreachable by the DELETE path route.
    assert authed_client.post("/api/settings/llm-api-keys", json={"name": "a/b", "key": "sk-x"}).status_code == 400


def test_llm_api_keys_select_and_delete_clears_ref(authed_client):
    authed_client.post("/api/settings/llm-api-keys", json={"name": "Gemini", "key": "gk-1234"})
    sel = authed_client.put("/api/settings/llm-api-keys/selected", json={"name": "Gemini"})
    assert sel.status_code == 200 and sel.json()["selected"] == "Gemini"
    # unknown selection is rejected
    assert authed_client.put("/api/settings/llm-api-keys/selected", json={"name": "Ghost"}).status_code == 400
    # deleting the selected key clears the ref
    dele = authed_client.delete("/api/settings/llm-api-keys/Gemini")
    assert dele.status_code == 200
    assert dele.json()["keys"] == [] and dele.json()["selected"] == ""


def test_llm_api_keys_requires_auth(app):
    resp = TestClient(app).get("/api/settings/llm-api-keys")
    assert resp.status_code == 401
