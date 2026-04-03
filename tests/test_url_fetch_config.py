"""Test url_fetch_timeout configuration."""
from backend.config import get_setting, DEFAULTS
from backend.database import init_db


def test_url_fetch_timeout_default(db_path):
    assert DEFAULTS["url_fetch_timeout"] == 5
    assert get_setting("url_fetch_timeout") == 5


def test_url_fetch_timeout_env(db_path, monkeypatch):
    monkeypatch.setenv("RECEIPTORY_URL_FETCH_TIMEOUT", "10")
    assert get_setting("url_fetch_timeout") == 10
