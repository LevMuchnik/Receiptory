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
    set_setting("llm_api_key", "sk-secret-key-12345")
    from backend.config import get_all_settings_masked
    settings = get_all_settings_masked()
    assert settings["llm_api_key"] != "sk-secret-key-12345"
    assert "***" in settings["llm_api_key"]

def test_init_settings_seeds_defaults(db_path):
    init_settings()
    from backend.database import get_connection
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'llm_model'").fetchone()
        assert row is not None
