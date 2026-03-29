import json
import os
import logging
from typing import Any

from backend.database import get_connection

logger = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    "business_names": [],
    "business_addresses": [],
    "business_tax_ids": [],
    "reference_currency": "ILS",
    "llm_model": "gemini/gemini-3-flash-preview",
    "llm_api_key": "",
    "llm_temperature": 0.0,
    "llm_max_tokens": 8192,
    "llm_sleep_interval": 0.0,
    "confidence_threshold": 0.7,
    "auth_username": "admin",
    "auth_password_hash": "",
    "log_level": "INFO",
    "page_render_dpi": 200,
    "backup_destination": "",
    "backup_schedule": "0 2 * * *",
    "backup_retention_daily": 7,
    "backup_retention_weekly": 4,
    "backup_retention_monthly": 3,
    "telegram_bot_token": "",
    "telegram_authorized_users": [],
    "gmail_address": "",
    "gmail_app_password": "",
    "gmail_imap_host": "imap.gmail.com",
    "gmail_imap_port": 993,
    "gmail_labels": [],
    "gmail_unread_only": True,
    "gmail_poll_interval": 300,
    "gmail_authorized_senders": [],
    "watched_folder_path": "",
    "watched_folder_poll_interval": 10,
    "base_url": "",
    "notify_from_name": "Receiptory",
    "notify_email_to": "",
    "notify_telegram_ingested": False,
    "notify_telegram_processed": False,
    "notify_telegram_failed": True,
    "notify_telegram_needs_review": True,
    "notify_telegram_backup_ok": False,
    "notify_telegram_backup_failed": True,
    "notify_email_ingested": False,
    "notify_email_processed": False,
    "notify_email_failed": True,
    "notify_email_needs_review": True,
    "notify_email_backup_ok": False,
    "notify_email_backup_failed": True,
}

SENSITIVE_KEYS = {"llm_api_key", "auth_password_hash", "telegram_bot_token", "gmail_app_password"}


def get_setting(key: str) -> Any:
    """Get a setting value. Precedence: env > db > default."""
    env_key = f"RECEIPTORY_{key.upper()}"
    env_val = os.environ.get(env_key)
    if env_val is not None and env_val != "":
        return _parse_value(key, env_val)
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is not None:
            return json.loads(row["value"])
    if key in DEFAULTS:
        return DEFAULTS[key]
    return None


def set_setting(key: str, value: Any) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO settings (key, value, updated_at)
               VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value, updated_at = excluded.updated_at""",
            (key, json.dumps(value)),
        )


def get_all_settings() -> dict[str, Any]:
    return {key: get_setting(key) for key in DEFAULTS}


def get_all_settings_masked() -> dict[str, Any]:
    settings = get_all_settings()
    for key in SENSITIVE_KEYS:
        val = settings.get(key, "")
        if val and isinstance(val, str) and len(val) > 4:
            settings[key] = val[:2] + "***" + val[-2:]
        elif val:
            settings[key] = "***"
    return settings


def init_settings() -> None:
    import bcrypt
    for key, default in DEFAULTS.items():
        with get_connection() as conn:
            row = conn.execute("SELECT key FROM settings WHERE key = ?", (key,)).fetchone()
            if row is None:
                value = default
                if key == "auth_password_hash" and not value:
                    value = bcrypt.hashpw(b"admin", bcrypt.gensalt()).decode("utf-8")
                set_setting(key, value)


def _parse_value(key: str, raw: str) -> Any:
    default = DEFAULTS.get(key)
    if isinstance(default, list):
        return json.loads(raw)
    if isinstance(default, bool):
        return raw.lower() in ("true", "1", "yes")
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    return raw
