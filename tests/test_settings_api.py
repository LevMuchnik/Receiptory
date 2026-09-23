import pytest
import bcrypt
from unittest.mock import MagicMock, patch

import litellm
from fastapi.testclient import TestClient

from backend.processing.extract import build_extraction_schema
from backend.processing.pipeline import extraction_categories
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


# --- POST /settings/test-llm: connectivity plus the structured-output probe (issue #67) ---


def _reply(text="Hello from test"):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = text
    return response


def _test_llm(authed_client, completion, supported=True):
    with patch("backend.api.settings.resolve_llm_api_key", return_value="test-key"), \
         patch("litellm.completion", side_effect=completion) as mock_completion, \
         patch("litellm.supports_response_schema", return_value=supported):
        resp = authed_client.post("/api/settings/test-llm")
    return resp, mock_completion


def test_test_llm_probes_the_real_extraction_schema(authed_client):
    """The probe sends the schema extraction will send, category enum included:
    a large enum is the documented way a schema gets rejected, so a toy schema
    would say "supported" where the real one fails."""
    resp, mock_completion = _test_llm(authed_client, [_reply(), _reply("{}")])
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["schema"] == "supported"
    assert mock_completion.call_count == 2
    assert "response_format" not in mock_completion.call_args_list[0].kwargs
    sent = mock_completion.call_args_list[1].kwargs["response_format"]
    assert sent["type"] == "json_schema"
    assert sent["json_schema"]["schema"] == build_extraction_schema(*extraction_categories())
    assert len(sent["json_schema"]["schema"]["properties"]["category"]["enum"]) > 1  # seeded categories, not the no-enum case


def test_test_llm_reports_a_model_without_schema_support_without_a_second_call(authed_client):
    resp, mock_completion = _test_llm(authed_client, [_reply()], supported=False)
    assert resp.status_code == 200
    assert resp.json()["schema"] == "unsupported"
    assert mock_completion.call_count == 1


def test_test_llm_reports_a_rejected_schema_but_still_connects(authed_client):
    rejected = litellm.BadRequestError(message="schema too complex", model="gemini/x", llm_provider="gemini")
    resp, _ = _test_llm(authed_client, [_reply(), rejected])
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["schema"] == "rejected"
    assert "schema too complex" in resp.json()["schema_detail"]
    assert "falls back to JSON mode" in resp.json()["schema_detail"]


def test_test_llm_reports_an_unexpected_probe_failure_as_error(authed_client):
    resp, _ = _test_llm(authed_client, [_reply(), TimeoutError("read timed out")])
    assert resp.status_code == 200
    assert resp.json()["schema"] == "error"


def test_test_llm_connectivity_failure_is_still_a_500(authed_client):
    resp, mock_completion = _test_llm(authed_client, [Exception("bad key")])
    assert resp.status_code == 500
    assert "LLM test failed" in resp.json()["detail"]
    assert mock_completion.call_count == 1


def test_test_llm_without_a_key_is_still_a_400(authed_client):
    with patch("backend.api.settings.resolve_llm_api_key", return_value=""):
        resp = authed_client.post("/api/settings/test-llm")
    assert resp.status_code == 400



def test_test_llm_with_json_mode_off_reports_off_without_a_second_call(authed_client):
    """Extraction then sends no response_format at all, so a schema verdict
    would describe a request that never happens."""
    set_setting("llm_json_mode", False)
    resp, mock_completion = _test_llm(authed_client, [_reply()])
    assert resp.status_code == 200
    assert resp.json()["schema"] == "off"
    assert mock_completion.call_count == 1


def test_test_llm_does_not_report_a_non_schema_400_as_a_rejected_schema(authed_client):
    overflow = litellm.ContextWindowExceededError(message="too long", model="gemini/x", llm_provider="gemini")
    resp, _ = _test_llm(authed_client, [_reply(), overflow])
    assert resp.json()["schema"] == "error"


def test_test_llm_probe_setup_failure_is_reported_not_a_500(authed_client):
    """Connectivity already succeeded; the probe must never turn that into a
    bare Internal Server Error."""
    with patch("backend.processing.pipeline.extraction_categories", side_effect=RuntimeError("db gone")):
        resp, _ = _test_llm(authed_client, [_reply()])
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["schema"] == "error"


def test_test_llm_calls_carry_a_timeout(authed_client):
    _, mock_completion = _test_llm(authed_client, [_reply(), _reply("{}")])
    assert all(c.kwargs.get("timeout") for c in mock_completion.call_args_list)


def test_test_llm_probe_never_sends_reasoning_effort(authed_client):
    """A thinking budget above the probe's 50-token cap 400s on its own, and
    would then be reported as a rejected schema."""
    set_setting("llm_reasoning_effort", "high")
    with patch("litellm.supports_reasoning", return_value=True):
        resp, mock_completion = _test_llm(authed_client, [_reply(), _reply("{}")])
    assert resp.json()["schema"] == "supported"
    probe = mock_completion.call_args_list[1].kwargs
    assert probe["response_format"]["type"] == "json_schema"
    assert "reasoning_effort" not in probe
    assert probe["api_key"] == "test-key"



def test_test_llm_masks_the_api_key_in_error_text(authed_client):
    """Gemini carries the key in the request URL, and some errors quote it."""
    leak = Exception("GET https://generativelanguage.googleapis.com/v1/models?key=test-key failed")
    resp, _ = _test_llm(authed_client, [leak])
    assert resp.status_code == 500
    assert "test-key" not in resp.json()["detail"] and "***-key" in resp.json()["detail"]
    resp, _ = _test_llm(authed_client, [_reply(), litellm.BadRequestError(message="schema rejected, url ...?key=test-key", model="m", llm_provider="gemini")])
    assert resp.json()["schema"] == "rejected"
    assert "test-key" not in resp.json()["schema_detail"]
