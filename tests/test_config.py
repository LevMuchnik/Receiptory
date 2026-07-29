import os
import pytest
from backend.config import get_setting, set_setting, DEFAULTS, init_settings

def test_defaults_returned_when_no_db_value(db_path):
    assert get_setting("llm_model") == "gemini/gemini-3-flash-preview"
    assert get_setting("confidence_threshold") == 0.7
    assert get_setting("business_names") == []

def test_llm_temperature_default_is_one(db_path):
    # Issue #11: Gemini 3 is tuned for temperature 1.0; the default must reflect that.
    assert get_setting("llm_temperature") == 1.0

def test_init_settings_seeds_temperature_one(db_path):
    init_settings()
    assert get_setting("llm_temperature") == 1.0

def test_set_and_get(db_path):
    set_setting("llm_model", "gpt-4o")
    assert get_setting("llm_model") == "gpt-4o"

def test_env_overrides_db(db_path, monkeypatch):
    set_setting("llm_model", "gpt-4o")
    monkeypatch.setenv("RECEIPTORY_LLM_MODEL", "claude-sonnet-4-20250514")
    assert get_setting("llm_model") == "claude-sonnet-4-20250514"

def test_env_json_for_lists(db_path, monkeypatch):
    monkeypatch.setenv("RECEIPTORY_BUSINESS_NAMES", '["Acme", "אקמה"]')
    assert get_setting("business_names") == ["Acme", "אקמה"]

def test_invalid_float_env_falls_back_to_default(db_path, monkeypatch):
    # Issue #11: a malformed numeric env value must not crash callers (e.g. url_triage,
    # extract) with ValueError — the override is ignored and resolution falls through.
    monkeypatch.setenv("RECEIPTORY_LLM_TEMPERATURE", "not-a-number")
    assert get_setting("llm_temperature") == 1.0

def test_invalid_int_env_falls_back_to_default(db_path, monkeypatch):
    monkeypatch.setenv("RECEIPTORY_LLM_MAX_TOKENS", "1,024")
    assert get_setting("llm_max_tokens") == 8192

def test_invalid_env_falls_through_to_db_value(db_path, monkeypatch):
    # Precedence is env > db > default: a malformed env override is ignored, so the
    # persisted DB value wins — NOT the code default.
    set_setting("llm_temperature", 0.5)
    monkeypatch.setenv("RECEIPTORY_LLM_TEMPERATURE", "not-a-number")
    assert get_setting("llm_temperature") == 0.5

def test_get_all_settings(db_path):
    from backend.config import get_all_settings
    settings = get_all_settings()
    assert "llm_model" in settings
    assert "auth_username" in settings

def test_masked_settings(db_path):
    set_setting("telegram_bot_token", "sk-secret-key-12345")
    from backend.config import get_all_settings_masked
    settings = get_all_settings_masked()
    assert settings["telegram_bot_token"] != "sk-secret-key-12345"
    assert "***" in settings["telegram_bot_token"]

def test_init_settings_seeds_defaults(db_path):
    init_settings()
    from backend.database import get_connection
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'llm_model'").fetchone()
        assert row is not None


def test_remote_intake_defaults(db_path):
    assert get_setting("remote_intake_enabled") is False
    assert get_setting("remote_intake_base_url") == ""
    assert get_setting("remote_intake_token") == ""
    assert get_setting("remote_intake_poll_interval_seconds") == 10
    assert get_setting("remote_intake_batch_size") == 10
    assert get_setting("remote_intake_max_file_bytes") == 20 * 1024 * 1024


def test_remote_intake_env_types(db_path, monkeypatch):
    monkeypatch.setenv("RECEIPTORY_REMOTE_INTAKE_ENABLED", "true")
    monkeypatch.setenv("RECEIPTORY_REMOTE_INTAKE_POLL_INTERVAL_SECONDS", "15")
    monkeypatch.setenv("RECEIPTORY_REMOTE_INTAKE_BATCH_SIZE", "7")
    monkeypatch.setenv("RECEIPTORY_REMOTE_INTAKE_MAX_FILE_BYTES", "4096")
    assert get_setting("remote_intake_enabled") is True
    assert get_setting("remote_intake_poll_interval_seconds") == 15
    assert get_setting("remote_intake_batch_size") == 7
    assert get_setting("remote_intake_max_file_bytes") == 4096


def test_remote_intake_token_is_masked(db_path):
    set_setting("remote_intake_token", "worker-queue-secret")
    from backend.config import get_all_settings_masked

    settings = get_all_settings_masked()
    assert settings["remote_intake_token"] != "worker-queue-secret"
    assert "***" in settings["remote_intake_token"]


# --- DB-managed LLM API keys (#25) ---

def test_resolve_selected_db_key_wins_over_env(db_path, monkeypatch):
    """The INVERTED precedence: a UI-selected DB key beats the legacy env key."""
    from backend.config import resolve_llm_api_key, set_llm_api_keys
    monkeypatch.setenv("RECEIPTORY_LLM_API_KEY", "env-legacy")
    set_llm_api_keys([{"name": "OpenAI", "key": "sk-openai"}])
    set_setting("llm_api_key_ref", "OpenAI")
    assert resolve_llm_api_key() == "sk-openai"


def test_resolve_falls_back_to_legacy_env_when_ref_missing(db_path, monkeypatch):
    """ref names a deleted/absent entry -> fall through to the legacy env key."""
    from backend.config import resolve_llm_api_key, set_llm_api_keys
    monkeypatch.setenv("RECEIPTORY_LLM_API_KEY", "env-legacy")
    set_llm_api_keys([{"name": "Gemini", "key": "gk"}])
    set_setting("llm_api_key_ref", "Ghost")
    assert resolve_llm_api_key() == "env-legacy"


def test_resolve_no_ref_no_env_is_empty(db_path):
    from backend.config import resolve_llm_api_key
    assert resolve_llm_api_key() == ""


def test_resolve_ref_match_is_case_insensitive(db_path):
    """Names are case-insensitive identifiers everywhere they're mutated; resolve
    must match the same way so a casing drift doesn't silently drop the key."""
    from backend.config import resolve_llm_api_key, set_llm_api_keys
    set_llm_api_keys([{"name": "OpenAI", "key": "sk-openai"}])
    set_setting("llm_api_key_ref", "openai")   # different casing than stored
    assert resolve_llm_api_key() == "sk-openai"


def test_resolve_no_ref_uses_legacy_env(db_path, monkeypatch):
    from backend.config import resolve_llm_api_key
    monkeypatch.setenv("RECEIPTORY_LLM_API_KEY", "env-legacy")
    assert resolve_llm_api_key() == "env-legacy"


def test_migrate_imports_legacy_and_named_env_keys_once(db_path, monkeypatch):
    from backend.config import migrate_llm_api_keys, list_llm_api_keys
    monkeypatch.setenv("RECEIPTORY_LLM_API_KEY", "gk")
    monkeypatch.setenv("RECEIPTORY_LLM_API_KEY_OPENAI", "sk-openai")
    set_setting("llm_model", "gemini/gemini-3-flash-preview")
    migrate_llm_api_keys()
    names = {e["name"] for e in list_llm_api_keys()}
    assert "OpenAI" in names or "OPENAI" in names
    assert any(e["key"] == "gk" for e in list_llm_api_keys())
    assert get_setting("llm_api_key_ref")  # a key was auto-selected


def test_migrate_skips_ref_env_var(db_path, monkeypatch):
    """RECEIPTORY_LLM_API_KEY_REF is the ref-selection override, not a key —
    it must not be imported as a bogus 'REF' key."""
    from backend.config import migrate_llm_api_keys, list_llm_api_keys
    monkeypatch.setenv("RECEIPTORY_LLM_API_KEY_REF", "OpenAI")
    monkeypatch.setenv("RECEIPTORY_LLM_API_KEY_OPENAI", "sk-openai")
    migrate_llm_api_keys()
    names = {e["name"] for e in list_llm_api_keys()}
    assert "REF" not in names
    assert "OPENAI" in names


def test_get_raw_setting_tolerates_corrupt_json(db_path):
    """A corrupt stored value degrades to the default instead of crashing."""
    from backend.config import _get_raw_setting
    from backend.database import get_connection
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES ('llm_api_keys', 'not-json', 'now')"
        )
    assert _get_raw_setting("llm_api_keys", []) == []


def test_migrate_is_idempotent_after_keys_deleted(db_path, monkeypatch):
    """The flag (not empty-list) guards re-seeding: deleting all keys then
    restarting must NOT re-import from a still-present .env."""
    from backend.config import migrate_llm_api_keys, list_llm_api_keys, set_llm_api_keys
    monkeypatch.setenv("RECEIPTORY_LLM_API_KEY", "gk")
    migrate_llm_api_keys()
    assert len(list_llm_api_keys()) == 1
    set_llm_api_keys([])            # user deletes the last key in the UI
    migrate_llm_api_keys()          # simulate restart
    assert list_llm_api_keys() == []
