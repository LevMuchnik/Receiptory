import json
import os
import logging
from typing import Any

from backend.database import get_connection

logger = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    "business_names": [],
    "export_name": "",
    "business_addresses": [],
    "business_tax_ids": [],
    "reference_currency": "ILS",
    "llm_model": "gemini/gemini-3-flash-preview",
    "llm_api_key_ref": "",
    "llm_temperature": 1.0,
    "llm_max_tokens": 8192,
    "llm_json_mode": True,
    "llm_parse_retries": 1,
    "llm_reasoning_effort": "none",
    "llm_sleep_interval": 0.0,
    "confidence_threshold": 0.7,
    "auth_username": "admin",
    "auth_password_hash": "",
    "theme": "light",
    "log_level": "INFO",
    # Rasterization DPI for feeding page images to the LLM. Read in two places
    # that must agree, or the scan round trip stops being 1:1:
    #   - backend/storage.py render_all_pages_to_memory (via pipeline.py) --
    #     rasters the PDF back to pixels at this DPI.
    #   - backend/processing/normalize.py _image_to_pdf -- sizes the PDF page
    #     from the image's pixels at this DPI. Reads the setting, so it tracks.
    # A THIRD copy lives in frontend/src/lib/pdf-builder.ts (TARGET_DPI) and does
    # NOT track: it is a build-time constant. It now only matters for MULTI-PAGE
    # scans, which are the one path that still builds a PDF client-side; a
    # single-page scan uploads a JPEG and is sized by _image_to_pdf above.
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
    "url_fetch_timeout": 5,
    "base_url": "",
    "gdrive_client_id": "",
    "gdrive_client_secret": "",
    "onedrive_client_id": "",
    "onedrive_client_secret": "",
    "cloud_auth_gdrive_token": "",
    "cloud_auth_gdrive_email": "",
    "cloud_auth_gdrive_folder": "",
    "cloud_auth_onedrive_token": "",
    "cloud_auth_onedrive_email": "",
    "cloud_auth_onedrive_folder": "",
    "cloud_auth_onedrive_drive_id": "",
    "cloud_auth_gdrive_drive_id": "",
    "cloud_auth_state": "",
    "notify_from_name": "Receiptory",
    "notify_email_to": "",
    "notify_telegram_ingested": False,
    "notify_telegram_processed": False,
    "notify_telegram_failed": True,
    "notify_telegram_needs_review": True,
    "notify_telegram_backup_ok": False,
    "notify_telegram_backup_failed": True,
    "notify_telegram_nothing_found": True,
    "notify_email_ingested": False,
    "notify_email_processed": False,
    "notify_email_failed": True,
    "notify_email_needs_review": True,
    "notify_email_backup_ok": False,
    "notify_email_backup_failed": True,
    "notify_email_nothing_found": False,
    "scanner_active_config": {"detector": "classical", "params": {}},
}

SENSITIVE_KEYS = {
    "auth_password_hash", "telegram_bot_token", "gmail_app_password",
    "gdrive_client_secret", "onedrive_client_secret",
    "cloud_auth_gdrive_token", "cloud_auth_onedrive_token", "cloud_auth_state",
}

# The single legacy env fallback key, provider-agnostic. Read at startup to seed
# the DB list (once), and read at resolve time only when no DB key is selected.
# The RECEIPTORY_LLM_API_KEY_<LABEL> named scheme is retired (issue #25).
_LEGACY_ENV_KEY = "RECEIPTORY_LLM_API_KEY"


def _get_raw_setting(key: str, default: Any) -> Any:
    """Read a DB-only setting that is NOT in DEFAULTS (e.g. `llm_api_keys`,
    `llm_api_keys_migrated`). Unlike `get_setting`, this never consults env and
    never applies the env>db>default precedence — those keys hold secret or
    control state that must live in the DB alone. Returns `default` if absent."""
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is not None:
            try:
                return json.loads(row["value"])
            except (ValueError, TypeError):
                # A corrupt/hand-edited value must not 500 the settings API or
                # crash key resolution mid-processing — degrade to the default.
                logger.warning("Corrupt JSON in settings[%r]; using default", key)
                return default
    return default


def provider_of(model: str | None) -> str | None:
    """litellm provider slug for a model id (e.g. 'gemini/gemini-3-flash' ->
    'gemini'), or None if it can't be resolved. Used to name an imported legacy
    key and to match a key to the selected model."""
    if not model:
        return None
    try:
        import litellm
        return litellm.get_llm_provider(model=model)[1]
    except Exception:
        return None


def list_llm_api_keys() -> list[dict]:
    """The DB-stored named API keys: [{"name", "key"}]. Key material included —
    callers that expose this to clients MUST mask it (see the API layer)."""
    keys = _get_raw_setting("llm_api_keys", [])
    return keys if isinstance(keys, list) else []


def resolve_llm_api_key() -> str:
    """The API key to send to litellm.

    Precedence is INVERTED from the app-wide env>db rule, on purpose: a key
    selected in the UI (llm_api_key_ref -> a DB llm_api_keys entry) wins over the
    legacy env key, so an `.env` value can never silently mask a UI edit. Only if
    no valid DB selection exists do we fall back to the single legacy env key.
    """
    # Narrow guard: tolerate the DB being unavailable on an early env-only path,
    # but do NOT swallow arbitrary errors — a transient failure must not silently
    # send the wrong (legacy) provider's key.
    try:
        ref = get_setting("llm_api_key_ref")
        entries = list_llm_api_keys()
    except RuntimeError:
        ref, entries = "", []
    if isinstance(ref, str) and ref:
        # Names are case-insensitive identifiers everywhere they're mutated
        # (add/delete/select), so match the ref the same way here — otherwise a
        # casing drift between ref and the stored entry silently drops the
        # selected key and falls back to the wrong (legacy) provider's key.
        for e in entries:
            if isinstance(e, dict) and e.get("name", "").lower() == ref.lower() and e.get("key"):
                return e["key"]
    return os.environ.get(_LEGACY_ENV_KEY) or ""


def set_llm_api_keys(entries: list[dict]) -> None:
    """Overwrite the DB named-key list. Prefer `mutate_llm_api_keys` for
    read-then-write edits so the two halves can't interleave."""
    set_setting("llm_api_keys", entries)


def mutate_llm_api_keys(fn) -> list[dict]:
    """Atomically read-modify-write the named-key list. `fn(entries)` receives the
    current list and returns the new one.

    The read and write run in one `BEGIN IMMEDIATE` transaction: IMMEDIATE takes
    the write lock up front (a plain DEFERRED txn wouldn't lock at SELECT time),
    so two concurrent callers — FastAPI runs sync endpoints in a threadpool, and
    `get_connection` opens a fresh connection with no process-level lock — can't
    read the same snapshot and lose an update. Returns the written list."""
    with get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT value FROM settings WHERE key = ?", ("llm_api_keys",)).fetchone()
        try:
            current = json.loads(row["value"]) if row is not None else []
        except (ValueError, TypeError):
            current = []
        if not isinstance(current, list):
            current = []
        updated = fn(current)
        conn.execute(
            """INSERT INTO settings (key, value, updated_at)
               VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value, updated_at = excluded.updated_at""",
            ("llm_api_keys", json.dumps(updated)),
        )
        return updated


def migrate_llm_api_keys() -> None:
    """One-time import of env-based keys into the DB list, gated by a dedicated
    `llm_api_keys_migrated` flag (NOT an empty-list check: after the user deletes
    all keys in the UI the list is empty again, and a still-present `.env` value
    would wrongly re-seed on restart). Imports the legacy single key (named after
    the current model's provider) and any surviving RECEIPTORY_LLM_API_KEY_<LABEL>
    vars, then sets the flag regardless of whether anything was imported."""
    if _get_raw_setting("llm_api_keys_migrated", False):
        return
    imported: list[dict] = []
    seen: set[str] = set()
    legacy = os.environ.get(_LEGACY_ENV_KEY)
    if legacy:
        name = (provider_of(get_setting("llm_model")) or "default").title()
        imported.append({"name": name, "key": legacy})
        seen.add(name.lower())
    for k, v in os.environ.items():
        if k.startswith("RECEIPTORY_LLM_API_KEY_") and v:
            label = k[len("RECEIPTORY_LLM_API_KEY_"):]
            # RECEIPTORY_LLM_API_KEY_REF is the env override for the
            # llm_api_key_ref SETTING (a selection name), not a key — skip it.
            if label == "REF":
                continue
            if label.lower() not in seen:
                imported.append({"name": label, "key": v})
                seen.add(label.lower())
    if imported:
        set_llm_api_keys(imported)
        if not get_setting("llm_api_key_ref"):
            model_provider = (provider_of(get_setting("llm_model")) or "").lower()
            best = next((e["name"] for e in imported if e["name"].lower() == model_provider), None)
            set_setting("llm_api_key_ref", best or imported[0]["name"])
    set_setting("llm_api_keys_migrated", True)


def get_setting(key: str) -> Any:
    """Get a setting value. Precedence: env > db > default."""
    env_key = f"RECEIPTORY_{key.upper()}"
    env_val = os.environ.get(env_key)
    if env_val is not None and env_val != "":
        # A malformed env override (bad number, invalid JSON list) must not crash
        # callers, nor jump straight to the code default — precedence is env > db >
        # default, so an unusable override is ignored and resolution continues.
        try:
            return _parse_value(key, env_val)
        except ValueError:
            logger.warning("Invalid env %s=%r; ignoring override, falling back to db/default", env_key, env_val)
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


def env_overridden_keys() -> list[str]:
    """Settings whose value is currently pinned by an environment variable.

    Config precedence is env > db > default, so for these keys a UI edit writes
    the DB but is never read back — get_setting always returns the env value.
    The UI uses this to show such fields as read-only ("managed by .env") instead
    of silently discarding edits. Mirrors get_setting's env check exactly
    (present and non-empty)."""
    out = []
    for key in DEFAULTS:
        val = os.environ.get(f"RECEIPTORY_{key.upper()}")
        if val is not None and val != "":
            out.append(key)
    # Special case: the password field maps to auth_password_hash, but
    # verify_password checks the plain-text RECEIPTORY_AUTH_PASSWORD first — so a
    # UI password change is ignored while that is set, under a non-standard name.
    if os.environ.get("RECEIPTORY_AUTH_PASSWORD") and "auth_password_hash" not in out:
        out.append("auth_password_hash")
    return out


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
