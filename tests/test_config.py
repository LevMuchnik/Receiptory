import os
import pytest
from backend.config import get_setting, set_setting, DEFAULTS, init_settings

def test_defaults_returned_when_no_db_value(db_path):
    assert get_setting("llm_model") == "gemini/gemini-3-flash-preview"
    assert get_setting("confidence_threshold") == 0.7
    assert get_setting("business_names") == []

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
