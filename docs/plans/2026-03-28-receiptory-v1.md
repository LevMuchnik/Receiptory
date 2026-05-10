# Receiptory v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-hosted receipt/invoice management system with web upload, LLM-powered extraction, document browsing, export, and cloud backup.

**Architecture:** Single-process FastAPI monolith with background asyncio tasks for document processing and backup scheduling. React frontend served as static files in production. SQLite with WAL mode and FTS5. LLM extraction via litellm.

**Tech Stack:** Python 3.12+ (uv), FastAPI, React 18+ (Vite, shadcn/ui, TypeScript), SQLite (WAL + FTS5), litellm, PyMuPDF, Pillow, weasyprint, rclone, Docker Compose.

---

## File Map

### Backend

| File | Responsibility |
|---|---|
| `backend/__init__.py` | Package marker |
| `backend/main.py` | FastAPI app creation, lifespan (queue + backup), static file mount, CORS |
| `backend/config.py` | Settings defaults, env override, DB read, `get_setting()` / `set_setting()` |
| `backend/auth.py` | Session cookie management, `require_auth` dependency, password hashing |
| `backend/database.py` | SQLite connection pool, WAL setup, migration runner |
| `backend/storage.py` | File save/read/delete, page rendering + caching via PyMuPDF |
| `backend/models.py` | Pydantic models for all API request/response shapes |
| `backend/api/__init__.py` | Package marker |
| `backend/api/auth.py` | `POST /api/auth/login`, `GET /api/auth/me` |
| `backend/api/upload.py` | `POST /api/upload` — hash, dedup, create pending doc |
| `backend/api/documents.py` | CRUD, search, filter, edit, reprocess, file/page serving |
| `backend/api/categories.py` | Category CRUD with soft delete |
| `backend/api/settings.py` | Settings get/patch, LLM test |
| `backend/api/export.py` | Zip generation with CSV |
| `backend/api/backup.py` | Trigger, history, download, delete |
| `backend/api/queue.py` | Queue status |
| `backend/api/stats.py` | Dashboard stats, processing costs |
| `backend/api/logs.py` | Log viewer |
| `backend/processing/__init__.py` | Package marker |
| `backend/processing/queue.py` | Background loop: poll, process, sleep |
| `backend/processing/pipeline.py` | Orchestrator: normalize → extract → file → update DB |
| `backend/processing/normalize.py` | Image→PDF (Pillow), HTML→PDF (weasyprint), passthrough PDF |
| `backend/processing/extract.py` | LLM prompt, litellm call, response parsing |
| `backend/processing/filing.py` | Stored filename generation, copy to filed/ |
| `backend/backup/__init__.py` | Package marker |
| `backend/backup/scheduler.py` | Cron eval loop, backup type determination |
| `backend/backup/runner.py` | Assemble backup: files, DB copy, JSONL, settings JSON, logs |
| `backend/backup/rclone.py` | rclone subprocess wrapper, retention cleanup |

### Migrations

| File | Responsibility |
|---|---|
| `migrations/001_initial_schema.sql` | All tables, FTS5, seed data |

### Frontend

| File | Responsibility |
|---|---|
| `frontend/src/main.tsx` | React entry point |
| `frontend/src/App.tsx` | Router, auth context, layout |
| `frontend/src/lib/api.ts` | API client (fetch wrapper with auth redirect) |
| `frontend/src/contexts/AuthContext.tsx` | Auth state, login/logout, session check |
| `frontend/src/pages/LoginPage.tsx` | Login form |
| `frontend/src/pages/DashboardPage.tsx` | Stats cards, recent activity |
| `frontend/src/pages/DocumentsPage.tsx` | Table, filters, search, bulk actions |
| `frontend/src/pages/DocumentDetailPage.tsx` | Page images + editable metadata form |
| `frontend/src/pages/ExportPage.tsx` | Filter controls, preset buttons, download |
| `frontend/src/pages/SettingsPage.tsx` | Tabbed: General, LLM, Categories, Backup, Logs |
| `frontend/src/components/DocumentTable.tsx` | Sortable, paginated table component |
| `frontend/src/components/FilterBar.tsx` | Status, category, type, date range filters |
| `frontend/src/components/PageViewer.tsx` | Navigable page image viewer |
| `frontend/src/components/MetadataForm.tsx` | Editable document metadata form |
| `frontend/src/components/CategoryManager.tsx` | Category list with add/edit/delete/reorder |
| `frontend/src/components/BackupPanel.tsx` | Backup history, trigger, download |
| `frontend/src/components/LogViewer.tsx` | Log entries with level filter |

### Config / Docker

| File | Responsibility |
|---|---|
| `pyproject.toml` | Python project definition, dependencies |
| `.env.example` | Example environment variables |
| `Dockerfile` | Multi-stage: Node build + Python runtime |
| `docker-compose.yml` | Single service with volume mount |

### Tests

| File | Responsibility |
|---|---|
| `tests/conftest.py` | Shared fixtures: temp DB, test client, sample files |
| `tests/test_database.py` | Migration runner, connection, WAL |
| `tests/test_config.py` | Settings precedence, defaults |
| `tests/test_auth.py` | Login, session, auth dependency |
| `tests/test_upload.py` | Upload, dedup, hash |
| `tests/test_storage.py` | File save/read, page rendering |
| `tests/test_normalize.py` | Image→PDF, HTML→PDF, passthrough |
| `tests/test_filing.py` | Filename generation |
| `tests/test_extract.py` | Prompt building, response parsing (mocked LLM) |
| `tests/test_pipeline.py` | Full pipeline with mocked LLM |
| `tests/test_queue.py` | Queue loop, crash recovery |
| `tests/test_documents_api.py` | Document CRUD, search, filter, edit |
| `tests/test_categories_api.py` | Category CRUD, soft delete |
| `tests/test_settings_api.py` | Settings get/patch |
| `tests/test_export.py` | Zip structure, CSV content |
| `tests/test_backup.py` | Runner, JSONL export, scheduler |
| `tests/test_stats.py` | Dashboard stats, cost summary |

---

## Task 1: Project Scaffold & Dependencies

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `backend/__init__.py`
- Create: `backend/api/__init__.py`
- Create: `backend/processing/__init__.py`
- Create: `backend/backup/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Initialize uv project**

Run:
```bash
cd /e/Projects/Lev/SmallProjects/Receiptory
uv init --no-readme
```

- [ ] **Step 2: Create pyproject.toml with all dependencies**

```toml
[project]
name = "receiptory"
version = "0.1.0"
description = "Self-hosted receipt and invoice management system"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.34.0",
    "python-multipart>=0.0.18",
    "litellm>=1.60.0",
    "PyMuPDF>=1.25.0",
    "Pillow>=11.0.0",
    "weasyprint>=63.0",
    "bcrypt>=4.2.0",
    "itsdangerous>=2.2.0",
    "croniter>=6.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.25.0",
    "httpx>=0.28.0",
    "pytest-cov>=6.0.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 3: Create .env.example**

```env
# Override any setting with RECEIPTORY_ prefix
# RECEIPTORY_LLM_API_KEY=your-api-key-here
# RECEIPTORY_AUTH_USERNAME=admin
# RECEIPTORY_AUTH_PASSWORD=admin
# RECEIPTORY_LLM_MODEL=gemini/gemini-3-flash-preview
```

- [ ] **Step 4: Create package __init__.py files**

Create empty files:
- `backend/__init__.py`
- `backend/api/__init__.py`
- `backend/processing/__init__.py`
- `backend/backup/__init__.py`
- `tests/__init__.py`

- [ ] **Step 5: Install dependencies**

Run:
```bash
uv sync --all-extras
```
Expected: all dependencies installed, `.venv` created.

- [ ] **Step 6: Verify pytest runs**

Run:
```bash
uv run pytest --co
```
Expected: "no tests ran" (no test files yet), exit 0 or 5 (no tests collected).

- [ ] **Step 7: Create .gitignore**

```gitignore
__pycache__/
*.pyc
.venv/
data/
.env
node_modules/
frontend/dist/
*.egg-info/
.pytest_cache/
```

- [ ] **Step 8: Initialize git and commit**

```bash
git init
git add pyproject.toml .env.example .gitignore backend/__init__.py backend/api/__init__.py backend/processing/__init__.py backend/backup/__init__.py tests/__init__.py uv.lock
git commit -m "chore: project scaffold with dependencies"
```

---

## Task 2: Database Layer & Migrations

**Files:**
- Create: `backend/database.py`
- Create: `migrations/001_initial_schema.sql`
- Create: `tests/conftest.py`
- Create: `tests/test_database.py`

- [ ] **Step 1: Write the migration SQL**

Create `migrations/001_initial_schema.sql`:

```sql
-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Settings key-value store
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Categories
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    is_system INTEGER NOT NULL DEFAULT 0,
    is_deleted INTEGER NOT NULL DEFAULT 0,
    display_order INTEGER,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Seed system categories
INSERT INTO categories (name, description, is_system, display_order) VALUES
    ('pending', 'Awaiting classification', 1, 0),
    ('not_a_receipt', 'Not a financial document', 1, 1),
    ('failed', 'Processing failed', 1, 2);

-- Seed starter user categories
INSERT INTO categories (name, description, display_order) VALUES
    ('office_supplies', 'Office equipment and supplies', 10),
    ('travel', 'Travel and transportation expenses', 11),
    ('meals', 'Meals and dining expenses', 12),
    ('utilities', 'Utility bills (electricity, water, internet, phone)', 13),
    ('other', 'Uncategorized expenses', 99);

-- Documents
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_type TEXT,
    original_filename TEXT NOT NULL,
    stored_filename TEXT,
    file_hash TEXT UNIQUE NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    page_count INTEGER,

    submission_date TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    submission_channel TEXT NOT NULL DEFAULT 'web_upload',
    sender_identifier TEXT,

    receipt_date TEXT,
    document_title TEXT,
    vendor_name TEXT,
    vendor_tax_id TEXT,
    vendor_receipt_id TEXT,
    client_name TEXT,
    client_tax_id TEXT,
    description TEXT,
    line_items TEXT,
    subtotal REAL,
    tax_amount REAL,
    total_amount REAL,
    currency TEXT,
    converted_amount REAL,
    conversion_rate REAL,
    payment_method TEXT,
    payment_identifier TEXT,
    language TEXT,
    additional_fields TEXT,
    raw_extracted_text TEXT,

    category_id INTEGER REFERENCES categories(id),
    status TEXT NOT NULL DEFAULT 'pending',

    extraction_confidence REAL,
    processing_model TEXT,
    processing_tokens_in INTEGER,
    processing_tokens_out INTEGER,
    processing_cost_usd REAL,
    processing_date TEXT,
    processing_attempts INTEGER NOT NULL DEFAULT 0,
    processing_error TEXT,

    manually_edited INTEGER NOT NULL DEFAULT 0,
    is_deleted INTEGER NOT NULL DEFAULT 0,
    edit_history TEXT DEFAULT '[]',
    user_notes TEXT,

    last_exported_date TEXT,

    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX idx_documents_status ON documents(status);
CREATE INDEX idx_documents_category ON documents(category_id);
CREATE INDEX idx_documents_receipt_date ON documents(receipt_date);
CREATE INDEX idx_documents_hash ON documents(file_hash);

-- FTS5 for full-text search
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    raw_extracted_text,
    vendor_name,
    description,
    document_title,
    content='documents',
    content_rowid='id'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER documents_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, raw_extracted_text, vendor_name, description, document_title)
    VALUES (new.id, new.raw_extracted_text, new.vendor_name, new.description, new.document_title);
END;

CREATE TRIGGER documents_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, raw_extracted_text, vendor_name, description, document_title)
    VALUES ('delete', old.id, old.raw_extracted_text, old.vendor_name, old.description, old.document_title);
END;

CREATE TRIGGER documents_au AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, raw_extracted_text, vendor_name, description, document_title)
    VALUES ('delete', old.id, old.raw_extracted_text, old.vendor_name, old.description, old.document_title);
    INSERT INTO documents_fts(rowid, raw_extracted_text, vendor_name, description, document_title)
    VALUES (new.id, new.raw_extracted_text, new.vendor_name, new.description, new.document_title);
END;

-- Backups
CREATE TABLE IF NOT EXISTS backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    size_bytes INTEGER,
    destination TEXT,
    error TEXT,
    backup_type TEXT NOT NULL
);
```

- [ ] **Step 2: Write database.py**

Create `backend/database.py`:

```python
import sqlite3
import os
import logging
from pathlib import Path
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_db_path: str | None = None

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


def init_db(db_path: str) -> None:
    """Initialize the database: set WAL mode and run pending migrations."""
    global _db_path
    _db_path = db_path

    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    with get_connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _run_migrations(conn)


@contextmanager
def get_connection():
    """Get a database connection with row factory set."""
    if _db_path is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _get_current_version(conn: sqlite3.Connection) -> int:
    """Get the current schema version, or -1 if no migrations applied."""
    try:
        row = conn.execute(
            "SELECT MAX(version) as v FROM schema_version"
        ).fetchone()
        return row["v"] if row["v"] is not None else -1
    except sqlite3.OperationalError:
        return -1


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Apply any pending migrations in order."""
    current = _get_current_version(conn)
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))

    for path in migration_files:
        version = int(path.name.split("_")[0])
        if version <= current:
            continue

        logger.info(f"Applying migration {path.name}")
        sql = path.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)", (version,)
        )
        conn.commit()
        logger.info(f"Migration {path.name} applied successfully")
```

- [ ] **Step 3: Write test fixtures in conftest.py**

Create `tests/conftest.py`:

```python
import os
import tempfile
import pytest
from pathlib import Path

from backend.database import init_db, get_connection


@pytest.fixture
def tmp_data_dir(tmp_path):
    """Create a temporary data directory structure."""
    for subdir in ["storage/originals", "storage/converted", "storage/filed", "storage/page_cache", "logs"]:
        (tmp_path / subdir).mkdir(parents=True)
    return tmp_path


@pytest.fixture
def db_path(tmp_data_dir):
    """Initialize a fresh test database and return its path."""
    path = str(tmp_data_dir / "receiptory.db")
    init_db(path)
    return path


@pytest.fixture
def db_conn(db_path):
    """Yield a database connection for test use."""
    with get_connection() as conn:
        yield conn


@pytest.fixture
def sample_pdf_path():
    """Return path to a sample PDF from test_documents/."""
    return str(Path(__file__).parent.parent / "test_documents" / "Receipt - esim.pdf")
```

- [ ] **Step 4: Write database tests**

Create `tests/test_database.py`:

```python
from backend.database import init_db, get_connection, _get_current_version


def test_init_creates_tables(db_conn):
    """All tables should exist after init."""
    tables = db_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    table_names = [r["name"] for r in tables]
    assert "schema_version" in table_names
    assert "settings" in table_names
    assert "categories" in table_names
    assert "documents" in table_names
    assert "backups" in table_names


def test_wal_mode(db_conn):
    """Database should be in WAL mode."""
    mode = db_conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_migration_version(db_conn):
    """Schema version should be 1 after initial migration."""
    version = _get_current_version(db_conn)
    assert version == 1


def test_system_categories_seeded(db_conn):
    """System categories should be seeded."""
    rows = db_conn.execute(
        "SELECT name FROM categories WHERE is_system = 1 ORDER BY name"
    ).fetchall()
    names = [r["name"] for r in rows]
    assert "failed" in names
    assert "not_a_receipt" in names
    assert "pending" in names


def test_user_categories_seeded(db_conn):
    """Starter user categories should be seeded."""
    rows = db_conn.execute(
        "SELECT name FROM categories WHERE is_system = 0 ORDER BY name"
    ).fetchall()
    names = [r["name"] for r in rows]
    assert "office_supplies" in names
    assert "travel" in names
    assert "meals" in names
    assert "utilities" in names
    assert "other" in names


def test_idempotent_migration(tmp_data_dir):
    """Running init_db twice should not fail or duplicate data."""
    path = str(tmp_data_dir / "receiptory.db")
    init_db(path)
    init_db(path)
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) as c FROM categories").fetchone()["c"]
        assert count == 8  # 3 system + 5 user
```

- [ ] **Step 5: Run tests**

Run:
```bash
uv run pytest tests/test_database.py -v
```
Expected: all 6 tests pass.

- [ ] **Step 6: Commit**

```bash
git add migrations/ backend/database.py tests/conftest.py tests/test_database.py
git commit -m "feat: database layer with migrations and seed data"
```

---

## Task 3: Configuration System

**Files:**
- Create: `backend/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write config tests**

Create `tests/test_config.py`:

```python
import os
import pytest
from backend.config import get_setting, set_setting, DEFAULTS, init_settings


def test_defaults_returned_when_no_db_value(db_path):
    """get_setting returns default when key has no DB entry."""
    assert get_setting("llm_model") == "gemini/gemini-3-flash-preview"
    assert get_setting("confidence_threshold") == 0.7
    assert get_setting("business_names") == []


def test_set_and_get(db_path):
    """set_setting persists and get_setting retrieves."""
    set_setting("llm_model", "gpt-4o")
    assert get_setting("llm_model") == "gpt-4o"


def test_env_overrides_db(db_path, monkeypatch):
    """Environment variable takes precedence over DB value."""
    set_setting("llm_model", "gpt-4o")
    monkeypatch.setenv("RECEIPTORY_LLM_MODEL", "claude-sonnet-4-20250514")
    assert get_setting("llm_model") == "claude-sonnet-4-20250514"


def test_env_json_for_lists(db_path, monkeypatch):
    """Environment variables with JSON arrays are parsed correctly."""
    monkeypatch.setenv("RECEIPTORY_BUSINESS_NAMES", '["Acme", "אקמה"]')
    assert get_setting("business_names") == ["Acme", "אקמה"]


def test_get_all_settings(db_path):
    """get_all_settings returns all settings with current values."""
    from backend.config import get_all_settings
    settings = get_all_settings()
    assert "llm_model" in settings
    assert "auth_username" in settings


def test_masked_settings(db_path):
    """get_all_settings_masked hides API keys."""
    set_setting("llm_api_key", "sk-secret-key-12345")
    from backend.config import get_all_settings_masked
    settings = get_all_settings_masked()
    assert settings["llm_api_key"] != "sk-secret-key-12345"
    assert "***" in settings["llm_api_key"]


def test_init_settings_seeds_defaults(db_path):
    """init_settings writes defaults to DB for keys not yet set."""
    init_settings()
    from backend.database import get_connection
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'llm_model'"
        ).fetchone()
        assert row is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/test_config.py -v
```
Expected: FAIL — `backend.config` does not exist.

- [ ] **Step 3: Implement config.py**

Create `backend/config.py`:

```python
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
    "llm_sleep_interval": 0.0,
    "confidence_threshold": 0.7,
    "auth_username": "admin",
    "auth_password_hash": "",  # Set during init_settings if empty
    "log_level": "INFO",
    "page_render_dpi": 200,
    "backup_destination": "",
    "backup_schedule": "0 2 * * *",
    "backup_retention_daily": 7,
    "backup_retention_weekly": 4,
    "backup_retention_monthly": 3,
}

SENSITIVE_KEYS = {"llm_api_key", "auth_password_hash"}


def get_setting(key: str) -> Any:
    """Get a setting value. Precedence: env > db > default."""
    env_key = f"RECEIPTORY_{key.upper()}"
    env_val = os.environ.get(env_key)
    if env_val is not None:
        return _parse_value(key, env_val)

    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        if row is not None:
            return json.loads(row["value"])

    if key in DEFAULTS:
        return DEFAULTS[key]

    return None


def set_setting(key: str, value: Any) -> None:
    """Write a setting to the database."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO settings (key, value, updated_at)
               VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   updated_at = excluded.updated_at""",
            (key, json.dumps(value)),
        )


def get_all_settings() -> dict[str, Any]:
    """Get all settings with current values (env > db > default)."""
    return {key: get_setting(key) for key in DEFAULTS}


def get_all_settings_masked() -> dict[str, Any]:
    """Get all settings with sensitive values masked."""
    settings = get_all_settings()
    for key in SENSITIVE_KEYS:
        val = settings.get(key, "")
        if val and isinstance(val, str) and len(val) > 4:
            settings[key] = val[:2] + "***" + val[-2:]
        elif val:
            settings[key] = "***"
    return settings


def init_settings() -> None:
    """Seed default settings into the DB for any keys not yet stored."""
    import bcrypt

    for key, default in DEFAULTS.items():
        with get_connection() as conn:
            row = conn.execute(
                "SELECT key FROM settings WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                value = default
                if key == "auth_password_hash" and not value:
                    value = bcrypt.hashpw(
                        b"admin", bcrypt.gensalt()
                    ).decode("utf-8")
                set_setting(key, value)


def _parse_value(key: str, raw: str) -> Any:
    """Parse an environment variable string into the correct type."""
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
```

- [ ] **Step 4: Run tests**

Run:
```bash
uv run pytest tests/test_config.py -v
```
Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/config.py tests/test_config.py
git commit -m "feat: configuration system with env/db/default precedence"
```

---

## Task 4: Authentication

**Files:**
- Create: `backend/auth.py`
- Create: `tests/test_auth.py`

- [ ] **Step 1: Write auth tests**

Create `tests/test_auth.py`:

```python
import pytest
import bcrypt
from backend.auth import verify_password, create_session, validate_session, SESSION_COOKIE_NAME
from backend.config import set_setting


def test_verify_password_correct(db_path):
    """Correct password returns True."""
    pw_hash = bcrypt.hashpw(b"secret123", bcrypt.gensalt()).decode()
    set_setting("auth_password_hash", pw_hash)
    assert verify_password("secret123") is True


def test_verify_password_wrong(db_path):
    """Wrong password returns False."""
    pw_hash = bcrypt.hashpw(b"secret123", bcrypt.gensalt()).decode()
    set_setting("auth_password_hash", pw_hash)
    assert verify_password("wrong") is False


def test_session_roundtrip():
    """Creating and validating a session token works."""
    token = create_session("admin")
    username = validate_session(token)
    assert username == "admin"


def test_invalid_session():
    """Invalid token returns None."""
    assert validate_session("garbage-token") is None


def test_expired_session():
    """Expired session returns None."""
    from itsdangerous import URLSafeTimedSerializer
    from backend.auth import _get_serializer
    s = _get_serializer()
    # We can't easily test expiry without mocking time, so just test invalid signature
    assert validate_session("invalid.token.here") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/test_auth.py -v
```
Expected: FAIL — `backend.auth` does not exist.

- [ ] **Step 3: Implement auth.py**

Create `backend/auth.py`:

```python
import os
import bcrypt
import logging
from fastapi import Request, Response, HTTPException
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from backend.config import get_setting

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "receiptory_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days
_SECRET_KEY = os.environ.get("RECEIPTORY_SECRET_KEY", "receiptory-default-secret-change-me")


def _get_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_SECRET_KEY)


def verify_password(password: str) -> bool:
    """Check password against stored bcrypt hash."""
    stored_hash = get_setting("auth_password_hash")
    if not stored_hash:
        return False
    return bcrypt.checkpw(
        password.encode("utf-8"), stored_hash.encode("utf-8")
    )


def create_session(username: str) -> str:
    """Create a signed session token."""
    s = _get_serializer()
    return s.dumps({"username": username})


def validate_session(token: str) -> str | None:
    """Validate a session token, return username or None."""
    s = _get_serializer()
    try:
        data = s.loads(token, max_age=SESSION_MAX_AGE)
        return data.get("username")
    except (BadSignature, SignatureExpired):
        return None


async def require_auth(request: Request) -> str:
    """FastAPI dependency: require valid session cookie."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    username = validate_session(token)
    if username is None:
        raise HTTPException(status_code=401, detail="Session expired")
    return username
```

- [ ] **Step 4: Run tests**

Run:
```bash
uv run pytest tests/test_auth.py -v
```
Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/auth.py tests/test_auth.py
git commit -m "feat: session-based authentication with bcrypt"
```

---

## Task 5: Storage Layer

**Files:**
- Create: `backend/storage.py`
- Create: `tests/test_storage.py`

- [ ] **Step 1: Write storage tests**

Create `tests/test_storage.py`:

```python
import os
import pytest
from pathlib import Path
from backend.storage import (
    save_original,
    save_converted,
    save_filed,
    get_file_path,
    render_page,
    clear_page_cache,
    compute_file_hash,
)


def test_compute_file_hash(sample_pdf_path):
    """SHA-256 hash is computed correctly."""
    h = compute_file_hash(sample_pdf_path)
    assert len(h) == 64  # hex SHA-256
    # Same file gives same hash
    assert compute_file_hash(sample_pdf_path) == h


def test_save_and_get_original(tmp_data_dir, sample_pdf_path):
    """Save original file and retrieve its path."""
    file_hash = compute_file_hash(sample_pdf_path)
    saved = save_original(sample_pdf_path, file_hash, ".pdf", str(tmp_data_dir))
    assert os.path.exists(saved)
    assert file_hash in saved

    retrieved = get_file_path("original", file_hash, ".pdf", str(tmp_data_dir))
    assert retrieved == saved


def test_save_filed(tmp_data_dir, sample_pdf_path):
    """Save filed copy with human-readable name."""
    stored_name = "2026-01-15-INV001-abc123.pdf"
    filed_path = save_filed(sample_pdf_path, stored_name, str(tmp_data_dir))
    assert os.path.exists(filed_path)
    assert stored_name in filed_path


def test_render_page(tmp_data_dir, sample_pdf_path):
    """Render a page from a PDF as PNG."""
    png_bytes = render_page(sample_pdf_path, page_num=0, dpi=150)
    assert len(png_bytes) > 0
    # PNG magic bytes
    assert png_bytes[:4] == b'\x89PNG'


def test_render_page_cached(tmp_data_dir, sample_pdf_path):
    """Second render reads from cache."""
    cache_dir = str(tmp_data_dir / "storage" / "page_cache")
    png1 = render_page(sample_pdf_path, page_num=0, dpi=150, cache_dir=cache_dir, doc_id=1)
    png2 = render_page(sample_pdf_path, page_num=0, dpi=150, cache_dir=cache_dir, doc_id=1)
    assert png1 == png2
    # Cache file should exist
    assert os.path.exists(os.path.join(cache_dir, "1", "page_0.png"))


def test_clear_page_cache(tmp_data_dir, sample_pdf_path):
    """Clearing cache removes page images for a document."""
    cache_dir = str(tmp_data_dir / "storage" / "page_cache")
    render_page(sample_pdf_path, page_num=0, dpi=150, cache_dir=cache_dir, doc_id=1)
    clear_page_cache(cache_dir, doc_id=1)
    assert not os.path.exists(os.path.join(cache_dir, "1"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/test_storage.py -v
```
Expected: FAIL — `backend.storage` does not exist.

- [ ] **Step 3: Implement storage.py**

Create `backend/storage.py`:

```python
import hashlib
import os
import shutil
import logging
from pathlib import Path

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


def compute_file_hash(file_path: str) -> str:
    """Compute SHA-256 hash of a file."""
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha.update(chunk)
    return sha.hexdigest()


def save_original(src_path: str, file_hash: str, ext: str, data_dir: str) -> str:
    """Copy original file to originals/ directory, named by hash."""
    dest_dir = os.path.join(data_dir, "storage", "originals")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"{file_hash}{ext}")
    if not os.path.exists(dest):
        shutil.copy2(src_path, dest)
    return dest


def save_converted(src_path: str, file_hash: str, data_dir: str) -> str:
    """Copy converted PDF to converted/ directory."""
    dest_dir = os.path.join(data_dir, "storage", "converted")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"{file_hash}.pdf")
    if not os.path.exists(dest):
        shutil.copy2(src_path, dest)
    return dest


def save_filed(src_path: str, stored_filename: str, data_dir: str) -> str:
    """Copy PDF to filed/ directory with human-readable name."""
    dest_dir = os.path.join(data_dir, "storage", "filed")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, stored_filename)
    shutil.copy2(src_path, dest)
    return dest


def get_file_path(file_type: str, file_hash: str, ext: str, data_dir: str) -> str:
    """Get the path to a stored file by type and hash."""
    if file_type == "original":
        return os.path.join(data_dir, "storage", "originals", f"{file_hash}{ext}")
    elif file_type == "converted":
        return os.path.join(data_dir, "storage", "converted", f"{file_hash}.pdf")
    raise ValueError(f"Unknown file type: {file_type}")


def get_pdf_page_count(pdf_path: str) -> int:
    """Count pages in a PDF."""
    doc = fitz.open(pdf_path)
    count = len(doc)
    doc.close()
    return count


def render_page(
    pdf_path: str,
    page_num: int,
    dpi: int = 200,
    cache_dir: str | None = None,
    doc_id: int | None = None,
) -> bytes:
    """Render a PDF page as PNG bytes. Optionally cache to disk."""
    if cache_dir and doc_id is not None:
        cache_path = os.path.join(cache_dir, str(doc_id), f"page_{page_num}.png")
        if os.path.exists(cache_path):
            with open(cache_path, "rb") as f:
                return f.read()

    doc = fitz.open(pdf_path)
    if page_num >= len(doc):
        doc.close()
        raise ValueError(f"Page {page_num} does not exist (document has {len(doc)} pages)")
    page = doc[page_num]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    png_bytes = pix.tobytes("png")
    doc.close()

    if cache_dir and doc_id is not None:
        page_dir = os.path.join(cache_dir, str(doc_id))
        os.makedirs(page_dir, exist_ok=True)
        with open(os.path.join(page_dir, f"page_{page_num}.png"), "wb") as f:
            f.write(png_bytes)

    return png_bytes


def clear_page_cache(cache_dir: str, doc_id: int) -> None:
    """Remove cached page images for a document."""
    page_dir = os.path.join(cache_dir, str(doc_id))
    if os.path.exists(page_dir):
        shutil.rmtree(page_dir)


def render_all_pages_to_memory(pdf_path: str, dpi: int = 200) -> list[bytes]:
    """Render all pages of a PDF to PNG bytes in memory (for LLM calls)."""
    doc = fitz.open(pdf_path)
    pages = []
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    for page in doc:
        pix = page.get_pixmap(matrix=mat)
        pages.append(pix.tobytes("png"))
    doc.close()
    return pages
```

- [ ] **Step 4: Run tests**

Run:
```bash
uv run pytest tests/test_storage.py -v
```
Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/storage.py tests/test_storage.py
git commit -m "feat: file storage layer with page rendering and caching"
```

---

## Task 6: File Normalization (Image→PDF, HTML→PDF)

**Files:**
- Create: `backend/processing/normalize.py`
- Create: `tests/test_normalize.py`

- [ ] **Step 1: Write normalization tests**

Create `tests/test_normalize.py`:

```python
import os
import pytest
from pathlib import Path
from PIL import Image
from backend.processing.normalize import normalize_file, NormalizeResult


@pytest.fixture
def sample_image(tmp_path):
    """Create a simple test JPEG image."""
    img = Image.new("RGB", (200, 300), color="white")
    path = str(tmp_path / "test_receipt.jpg")
    img.save(path, "JPEG")
    return path


@pytest.fixture
def sample_html(tmp_path):
    """Create a simple test HTML file."""
    path = str(tmp_path / "receipt.html")
    with open(path, "w") as f:
        f.write("<html><body><h1>Receipt</h1><p>Total: $50.00</p></body></html>")
    return path


def test_pdf_passthrough(sample_pdf_path, tmp_data_dir):
    """PDF files pass through without conversion."""
    result = normalize_file(sample_pdf_path, str(tmp_data_dir))
    assert result.converted is False
    assert result.pdf_path == sample_pdf_path
    assert result.page_count > 0


def test_image_to_pdf(sample_image, tmp_data_dir):
    """JPEG image is converted to PDF."""
    result = normalize_file(sample_image, str(tmp_data_dir))
    assert result.converted is True
    assert result.pdf_path.endswith(".pdf")
    assert os.path.exists(result.pdf_path)
    assert result.page_count == 1


def test_html_to_pdf(sample_html, tmp_data_dir):
    """HTML file is converted to PDF."""
    result = normalize_file(sample_html, str(tmp_data_dir))
    assert result.converted is True
    assert result.pdf_path.endswith(".pdf")
    assert os.path.exists(result.pdf_path)
    assert result.page_count >= 1


def test_unsupported_format(tmp_path, tmp_data_dir):
    """Unsupported file format raises ValueError."""
    path = str(tmp_path / "file.xyz")
    with open(path, "w") as f:
        f.write("not a document")
    with pytest.raises(ValueError, match="Unsupported"):
        normalize_file(path, str(tmp_data_dir))
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/test_normalize.py -v
```
Expected: FAIL — `backend.processing.normalize` does not exist.

- [ ] **Step 3: Implement normalize.py**

Create `backend/processing/normalize.py`:

```python
import os
import tempfile
import logging
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".heic", ".heif", ".webp"}
HTML_EXTENSIONS = {".html", ".htm"}
PDF_EXTENSIONS = {".pdf"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | HTML_EXTENSIONS | PDF_EXTENSIONS


@dataclass
class NormalizeResult:
    pdf_path: str
    converted: bool
    page_count: int
    original_ext: str


def normalize_file(file_path: str, data_dir: str) -> NormalizeResult:
    """Normalize a file to PDF. Returns the path to the (possibly converted) PDF."""
    ext = Path(file_path).suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file format: {ext}")

    if ext in PDF_EXTENSIONS:
        page_count = _count_pages(file_path)
        return NormalizeResult(
            pdf_path=file_path,
            converted=False,
            page_count=page_count,
            original_ext=ext,
        )

    if ext in IMAGE_EXTENSIONS:
        pdf_path = _image_to_pdf(file_path, data_dir)
        page_count = _count_pages(pdf_path)
        return NormalizeResult(
            pdf_path=pdf_path,
            converted=True,
            page_count=page_count,
            original_ext=ext,
        )

    if ext in HTML_EXTENSIONS:
        pdf_path = _html_to_pdf(file_path, data_dir)
        page_count = _count_pages(pdf_path)
        return NormalizeResult(
            pdf_path=pdf_path,
            converted=True,
            page_count=page_count,
            original_ext=ext,
        )

    raise ValueError(f"Unsupported file format: {ext}")


def _image_to_pdf(image_path: str, data_dir: str) -> str:
    """Convert an image to PDF using Pillow."""
    img = Image.open(image_path)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")

    tmp_dir = os.path.join(data_dir, "storage", "converted")
    os.makedirs(tmp_dir, exist_ok=True)

    stem = Path(image_path).stem
    pdf_path = os.path.join(tmp_dir, f"{stem}_converted.pdf")
    img.save(pdf_path, "PDF", resolution=200.0)
    return pdf_path


def _html_to_pdf(html_path: str, data_dir: str) -> str:
    """Convert HTML to PDF using weasyprint."""
    from weasyprint import HTML

    tmp_dir = os.path.join(data_dir, "storage", "converted")
    os.makedirs(tmp_dir, exist_ok=True)

    stem = Path(html_path).stem
    pdf_path = os.path.join(tmp_dir, f"{stem}_converted.pdf")
    HTML(filename=html_path).write_pdf(pdf_path)
    return pdf_path


def _count_pages(pdf_path: str) -> int:
    doc = fitz.open(pdf_path)
    count = len(doc)
    doc.close()
    return count
```

- [ ] **Step 4: Run tests**

Run:
```bash
uv run pytest tests/test_normalize.py -v
```
Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/processing/normalize.py tests/test_normalize.py
git commit -m "feat: file normalization (image/HTML to PDF conversion)"
```

---

## Task 7: Filing (Stored Filename Generation)

**Files:**
- Create: `backend/processing/filing.py`
- Create: `tests/test_filing.py`

- [ ] **Step 1: Write filing tests**

Create `tests/test_filing.py`:

```python
from backend.processing.filing import generate_stored_filename


def test_full_fields():
    """All fields present produces expected filename."""
    name = generate_stored_filename(
        receipt_date="2026-01-15",
        vendor_receipt_id="INV-001",
        file_hash="abcdef1234567890",
    )
    assert name == "2026-01-15-INV-001-abcdef12.pdf"


def test_no_date():
    """Missing date produces 0000-00-00."""
    name = generate_stored_filename(
        receipt_date=None,
        vendor_receipt_id="R123",
        file_hash="abcdef1234567890",
    )
    assert name == "0000-00-00-R123-abcdef12.pdf"


def test_no_receipt_id():
    """Missing receipt ID produces 000000."""
    name = generate_stored_filename(
        receipt_date="2026-03-01",
        vendor_receipt_id=None,
        file_hash="abcdef1234567890",
    )
    assert name == "2026-03-01-000000-abcdef12.pdf"


def test_no_fields():
    """All missing produces default placeholders."""
    name = generate_stored_filename(
        receipt_date=None,
        vendor_receipt_id=None,
        file_hash="abcdef1234567890",
    )
    assert name == "0000-00-00-000000-abcdef12.pdf"


def test_special_chars_sanitized():
    """Special characters in receipt ID are sanitized."""
    name = generate_stored_filename(
        receipt_date="2026-01-15",
        vendor_receipt_id="INV/001:test",
        file_hash="abcdef1234567890",
    )
    assert "/" not in name
    assert ":" not in name
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/test_filing.py -v
```
Expected: FAIL — `backend.processing.filing` does not exist.

- [ ] **Step 3: Implement filing.py**

Create `backend/processing/filing.py`:

```python
import re


def generate_stored_filename(
    receipt_date: str | None,
    vendor_receipt_id: str | None,
    file_hash: str,
) -> str:
    """Generate the stored filename: yyyy-mm-dd-vendor_receipt_id-hash.pdf"""
    date_part = receipt_date if receipt_date else "0000-00-00"
    id_part = vendor_receipt_id if vendor_receipt_id else "000000"
    hash_part = file_hash[:8]

    # Sanitize: only allow alphanumeric, hyphens, dots, underscores
    id_part = re.sub(r"[^a-zA-Z0-9\-_.]", "_", id_part)

    return f"{date_part}-{id_part}-{hash_part}.pdf"
```

- [ ] **Step 4: Run tests**

Run:
```bash
uv run pytest tests/test_filing.py -v
```
Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/processing/filing.py tests/test_filing.py
git commit -m "feat: stored filename generation for filing"
```

---

## Task 8: LLM Extraction (Prompt + Response Parsing)

**Files:**
- Create: `backend/processing/extract.py`
- Create: `tests/test_extract.py`

- [ ] **Step 1: Write extraction tests**

Create `tests/test_extract.py`:

```python
import json
import pytest
from unittest.mock import patch, MagicMock
from backend.processing.extract import (
    build_extraction_prompt,
    parse_llm_response,
    extract_document,
    ExtractionResult,
)


SAMPLE_LLM_RESPONSE = json.dumps({
    "receipt_date": "2026-01-15",
    "document_title": "Tax Invoice",
    "vendor_name": "Office Depot",
    "vendor_tax_id": "515234567",
    "vendor_receipt_id": "INV-2026-001",
    "client_name": None,
    "client_tax_id": None,
    "description": "Office supplies purchase",
    "line_items": [
        {"description": "Paper A4", "quantity": 5, "unit_price": 25.0},
        {"description": "Ink cartridge", "quantity": 2, "unit_price": 89.0},
    ],
    "subtotal": 303.0,
    "tax_amount": 51.51,
    "total_amount": 354.51,
    "currency": "ILS",
    "payment_method": "credit_card",
    "payment_identifier": "4580",
    "language": "he",
    "additional_fields": [],
    "raw_extracted_text": "Office Depot\nTax Invoice\n...",
    "document_type": "expense_receipt",
    "category": "office_supplies",
    "extraction_confidence": 0.95,
})


def test_build_prompt_includes_business_info():
    """Prompt includes user's business names, addresses, and tax IDs."""
    prompt = build_extraction_prompt(
        business_names=["Acme Corp", "אקמה בע\"מ"],
        business_addresses=["123 Main St", "רחוב ראשי 123"],
        business_tax_ids=["515000000"],
        categories=[
            {"name": "office_supplies", "description": "Office equipment and supplies"},
            {"name": "travel", "description": "Travel expenses"},
        ],
    )
    assert "Acme Corp" in prompt
    assert "515000000" in prompt
    assert "office_supplies" in prompt
    assert "Office equipment and supplies" in prompt


def test_parse_valid_response():
    """Valid JSON response parses into ExtractionResult."""
    result = parse_llm_response(SAMPLE_LLM_RESPONSE)
    assert result.vendor_name == "Office Depot"
    assert result.total_amount == 354.51
    assert result.extraction_confidence == 0.95
    assert result.document_type == "expense_receipt"
    assert result.category_name == "office_supplies"
    assert len(result.line_items) == 2


def test_parse_response_with_markdown_fence():
    """Response wrapped in ```json``` fences is handled."""
    wrapped = f"```json\n{SAMPLE_LLM_RESPONSE}\n```"
    result = parse_llm_response(wrapped)
    assert result.vendor_name == "Office Depot"


def test_parse_invalid_json():
    """Invalid JSON raises ValueError."""
    with pytest.raises(ValueError, match="Failed to parse"):
        parse_llm_response("this is not json")


def test_document_type_override():
    """If vendor_tax_id matches business ID, type is overridden to issued_invoice."""
    response = json.loads(SAMPLE_LLM_RESPONSE)
    response["vendor_tax_id"] = "515000000"
    result = parse_llm_response(json.dumps(response))
    # The parse itself doesn't override — that's done in pipeline
    # But we test the result contains the tax_id for verification
    assert result.vendor_tax_id == "515000000"


@patch("backend.processing.extract.litellm_completion")
def test_extract_document_calls_llm(mock_completion):
    """extract_document sends images and prompt to litellm."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = SAMPLE_LLM_RESPONSE
    mock_response.usage.prompt_tokens = 1000
    mock_response.usage.completion_tokens = 500
    mock_completion.return_value = mock_response

    page_images = [b"fake-png-bytes"]
    result = extract_document(
        page_images=page_images,
        model="gemini/gemini-3-flash-preview",
        api_key="test-key",
        business_names=["Acme"],
        business_addresses=["123 Main"],
        business_tax_ids=["515000000"],
        categories=[{"name": "office_supplies", "description": "Office stuff"}],
    )

    assert result.extraction.vendor_name == "Office Depot"
    assert result.tokens_in == 1000
    assert result.tokens_out == 500
    mock_completion.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/test_extract.py -v
```
Expected: FAIL — `backend.processing.extract` does not exist.

- [ ] **Step 3: Implement extract.py**

Create `backend/processing/extract.py`:

```python
import json
import re
import base64
import logging
from dataclasses import dataclass, field
from typing import Any

import litellm

logger = logging.getLogger(__name__)


@dataclass
class ExtractionResult:
    receipt_date: str | None = None
    document_title: str | None = None
    vendor_name: str | None = None
    vendor_tax_id: str | None = None
    vendor_receipt_id: str | None = None
    client_name: str | None = None
    client_tax_id: str | None = None
    description: str | None = None
    line_items: list[dict] = field(default_factory=list)
    subtotal: float | None = None
    tax_amount: float | None = None
    total_amount: float | None = None
    currency: str | None = None
    payment_method: str | None = None
    payment_identifier: str | None = None
    language: str | None = None
    additional_fields: list[dict] = field(default_factory=list)
    raw_extracted_text: str | None = None
    document_type: str | None = None
    category_name: str | None = None
    extraction_confidence: float | None = None


@dataclass
class LLMExtractionResult:
    extraction: ExtractionResult
    tokens_in: int
    tokens_out: int
    model: str


def build_extraction_prompt(
    business_names: list[str],
    business_addresses: list[str],
    business_tax_ids: list[str],
    categories: list[dict[str, str]],
) -> str:
    """Build the system prompt for document extraction."""
    category_list = "\n".join(
        f"  - {c['name']}: {c.get('description', '')}" for c in categories
    )

    return f"""You are a document data extraction system. Analyze the provided document image(s) and extract all structured data.

## User's Business Information (for identifying issued invoices vs expense receipts)
- Business names: {json.dumps(business_names)}
- Business addresses: {json.dumps(business_addresses)}
- Business tax IDs: {json.dumps(business_tax_ids)}

If the document's issuer (vendor) matches any of the above business names, addresses, or tax IDs, classify it as "issued_invoice". Otherwise, classify as "expense_receipt" for financial documents or "other_document" for non-financial documents.

## Available Categories
{category_list}

## Required Output
Return a single JSON object with these fields:
- receipt_date: date on the document (YYYY-MM-DD format, or null)
- document_title: title as it appears on the document (e.g., "Tax Invoice", "Receipt", "חשבונית מס")
- vendor_name: vendor/issuer name
- vendor_tax_id: business number / tax ID of the issuer
- vendor_receipt_id: receipt/invoice number
- client_name: client/buyer name (if present)
- client_tax_id: client/buyer tax ID (if present)
- description: brief summary of the purchase/service/document
- line_items: array of {{"description": "...", "quantity": N, "unit_price": N}}
- subtotal: pre-tax amount (null for non-financial)
- tax_amount: tax amount (null for non-financial)
- total_amount: total amount (null for non-financial)
- currency: ISO 4217 code (ILS, USD, EUR, etc.)
- payment_method: cash, credit_card, bank_transfer, etc. (if detectable)
- payment_identifier: card last digits, account number, etc.
- language: detected language code (he, en, ru, etc.)
- additional_fields: array of {{"key": "...", "value": "..."}} for any other extracted data
- raw_extracted_text: full OCR text of the entire document
- document_type: "expense_receipt", "issued_invoice", or "other_document"
- category: one of the category names listed above
- extraction_confidence: 0.0 to 1.0 confidence score for the overall extraction quality

Return ONLY the JSON object, no markdown fences or explanation."""


def parse_llm_response(response_text: str) -> ExtractionResult:
    """Parse the LLM response text into an ExtractionResult."""
    text = response_text.strip()

    # Strip markdown code fences if present
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse LLM response as JSON: {e}")

    return ExtractionResult(
        receipt_date=data.get("receipt_date"),
        document_title=data.get("document_title"),
        vendor_name=data.get("vendor_name"),
        vendor_tax_id=data.get("vendor_tax_id"),
        vendor_receipt_id=data.get("vendor_receipt_id"),
        client_name=data.get("client_name"),
        client_tax_id=data.get("client_tax_id"),
        description=data.get("description"),
        line_items=data.get("line_items", []),
        subtotal=data.get("subtotal"),
        tax_amount=data.get("tax_amount"),
        total_amount=data.get("total_amount"),
        currency=data.get("currency"),
        payment_method=data.get("payment_method"),
        payment_identifier=data.get("payment_identifier"),
        language=data.get("language"),
        additional_fields=data.get("additional_fields", []),
        raw_extracted_text=data.get("raw_extracted_text"),
        document_type=data.get("document_type"),
        category_name=data.get("category"),
        extraction_confidence=data.get("extraction_confidence"),
    )


def litellm_completion(**kwargs):
    """Wrapper around litellm.completion for easy mocking."""
    return litellm.completion(**kwargs)


def extract_document(
    page_images: list[bytes],
    model: str,
    api_key: str,
    business_names: list[str],
    business_addresses: list[str],
    business_tax_ids: list[str],
    categories: list[dict[str, str]],
) -> LLMExtractionResult:
    """Send document page images to the LLM and extract structured data."""
    prompt = build_extraction_prompt(
        business_names=business_names,
        business_addresses=business_addresses,
        business_tax_ids=business_tax_ids,
        categories=categories,
    )

    # Build content array with text prompt + images
    content: list[dict] = [{"type": "text", "text": prompt}]
    for img_bytes in page_images:
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })

    response = litellm_completion(
        model=model,
        api_key=api_key,
        messages=[{"role": "user", "content": content}],
        temperature=0.0,
        max_tokens=4096,
    )

    extraction = parse_llm_response(response.choices[0].message.content)

    return LLMExtractionResult(
        extraction=extraction,
        tokens_in=response.usage.prompt_tokens,
        tokens_out=response.usage.completion_tokens,
        model=model,
    )
```

- [ ] **Step 4: Run tests**

Run:
```bash
uv run pytest tests/test_extract.py -v
```
Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/processing/extract.py tests/test_extract.py
git commit -m "feat: LLM extraction with prompt building and response parsing"
```

---

## Task 9: Processing Pipeline (Orchestrator)

**Files:**
- Create: `backend/processing/pipeline.py`
- Create: `tests/test_pipeline.py`

- [ ] **Step 1: Write pipeline tests**

Create `tests/test_pipeline.py`:

```python
import json
import pytest
from unittest.mock import patch, MagicMock
from backend.processing.pipeline import process_document
from backend.database import get_connection
from backend.config import set_setting, init_settings
from backend.processing.extract import ExtractionResult, LLMExtractionResult


MOCK_EXTRACTION = ExtractionResult(
    receipt_date="2026-01-15",
    document_title="Tax Invoice",
    vendor_name="Office Depot",
    vendor_tax_id="515234567",
    vendor_receipt_id="INV-001",
    description="Office supplies",
    line_items=[{"description": "Paper", "quantity": 1, "unit_price": 25.0}],
    subtotal=25.0,
    tax_amount=4.25,
    total_amount=29.25,
    currency="ILS",
    payment_method="credit_card",
    payment_identifier="4580",
    language="he",
    additional_fields=[],
    raw_extracted_text="Office Depot Tax Invoice ...",
    document_type="expense_receipt",
    category_name="office_supplies",
    extraction_confidence=0.95,
)

MOCK_LLM_RESULT = LLMExtractionResult(
    extraction=MOCK_EXTRACTION,
    tokens_in=1000,
    tokens_out=500,
    model="gemini/gemini-3-flash-preview",
)


@pytest.fixture
def setup_db(db_path, tmp_data_dir):
    """Set up database with settings and return data_dir."""
    init_settings()
    set_setting("llm_api_key", "test-key")
    return str(tmp_data_dir)


@pytest.fixture
def pending_doc(setup_db, sample_pdf_path):
    """Create a pending document in the database."""
    import shutil
    from backend.storage import compute_file_hash, save_original

    file_hash = compute_file_hash(sample_pdf_path)
    save_original(sample_pdf_path, file_hash, ".pdf", setup_db)

    with get_connection() as conn:
        conn.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes, status, submission_channel)
               VALUES (?, ?, ?, 'pending', 'web_upload')""",
            ("test.pdf", file_hash, 1234),
        )
        doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return doc_id


@patch("backend.processing.pipeline.extract_document")
def test_process_document_success(mock_extract, pending_doc, setup_db):
    """Successful processing sets status to processed and fills fields."""
    mock_extract.return_value = MOCK_LLM_RESULT

    process_document(pending_doc, setup_db)

    with get_connection() as conn:
        doc = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (pending_doc,)
        ).fetchone()

    assert doc["status"] == "processed"
    assert doc["vendor_name"] == "Office Depot"
    assert doc["total_amount"] == 29.25
    assert doc["extraction_confidence"] == 0.95
    assert doc["processing_model"] == "gemini/gemini-3-flash-preview"
    assert doc["processing_tokens_in"] == 1000
    assert doc["stored_filename"] is not None


@patch("backend.processing.pipeline.extract_document")
def test_process_document_low_confidence(mock_extract, pending_doc, setup_db):
    """Low confidence sets status to needs_review."""
    low_conf = LLMExtractionResult(
        extraction=ExtractionResult(
            **{**MOCK_EXTRACTION.__dict__, "extraction_confidence": 0.3}
        ),
        tokens_in=1000,
        tokens_out=500,
        model="gemini/gemini-3-flash-preview",
    )
    mock_extract.return_value = low_conf

    process_document(pending_doc, setup_db)

    with get_connection() as conn:
        doc = conn.execute(
            "SELECT status FROM documents WHERE id = ?", (pending_doc,)
        ).fetchone()
    assert doc["status"] == "needs_review"


@patch("backend.processing.pipeline.extract_document")
def test_process_document_failure(mock_extract, pending_doc, setup_db):
    """Exception during processing sets status to failed."""
    mock_extract.side_effect = Exception("LLM timeout")

    process_document(pending_doc, setup_db)

    with get_connection() as conn:
        doc = conn.execute(
            "SELECT status, processing_error, processing_attempts FROM documents WHERE id = ?",
            (pending_doc,),
        ).fetchone()
    assert doc["status"] == "failed"
    assert "LLM timeout" in doc["processing_error"]
    assert doc["processing_attempts"] == 1


@patch("backend.processing.pipeline.extract_document")
def test_process_document_type_override(mock_extract, pending_doc, setup_db):
    """Vendor tax ID matching user's business ID overrides to issued_invoice."""
    set_setting("business_tax_ids", ["515234567"])
    mock_extract.return_value = MOCK_LLM_RESULT

    process_document(pending_doc, setup_db)

    with get_connection() as conn:
        doc = conn.execute(
            "SELECT document_type FROM documents WHERE id = ?", (pending_doc,)
        ).fetchone()
    assert doc["document_type"] == "issued_invoice"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/test_pipeline.py -v
```
Expected: FAIL — `backend.processing.pipeline` does not exist.

- [ ] **Step 3: Implement pipeline.py**

Create `backend/processing/pipeline.py`:

```python
import json
import os
import logging
from datetime import datetime, timezone

from backend.database import get_connection
from backend.config import get_setting
from backend.storage import (
    get_file_path,
    save_filed,
    render_all_pages_to_memory,
    get_pdf_page_count,
)
from backend.processing.normalize import normalize_file
from backend.processing.extract import extract_document, ExtractionResult
from backend.processing.filing import generate_stored_filename

logger = logging.getLogger(__name__)


def process_document(doc_id: int, data_dir: str) -> None:
    """Process a single document through the full pipeline."""
    with get_connection() as conn:
        doc = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()

    if doc is None:
        logger.error(f"Document {doc_id} not found")
        return

    try:
        _run_pipeline(doc_id, doc, data_dir)
    except Exception as e:
        logger.error(f"Processing failed for document {doc_id}: {e}")
        with get_connection() as conn:
            conn.execute(
                """UPDATE documents SET
                    status = 'failed',
                    processing_error = ?,
                    processing_attempts = processing_attempts + 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                WHERE id = ?""",
                (str(e), doc_id),
            )


def _run_pipeline(doc_id: int, doc: dict, data_dir: str) -> None:
    """Execute pipeline steps. Raises on failure."""
    file_hash = doc["file_hash"]
    original_ext = os.path.splitext(doc["original_filename"])[1].lower() or ".pdf"

    # Step 1: Find the original file
    original_path = get_file_path("original", file_hash, original_ext, data_dir)

    # Step 2: Normalize to PDF
    norm = normalize_file(original_path, data_dir)
    pdf_path = norm.pdf_path
    page_count = norm.page_count

    # If conversion happened, store the converted file
    if norm.converted:
        from backend.storage import save_converted
        save_converted(pdf_path, file_hash, data_dir)

    # Step 3: Render pages to memory for LLM
    dpi = get_setting("page_render_dpi")
    page_images = render_all_pages_to_memory(pdf_path, dpi=dpi)

    # Step 4: Extract via LLM
    model = get_setting("llm_model")
    api_key = get_setting("llm_api_key")
    business_names = get_setting("business_names")
    business_addresses = get_setting("business_addresses")
    business_tax_ids = get_setting("business_tax_ids")
    confidence_threshold = get_setting("confidence_threshold")

    # Get active categories
    with get_connection() as conn:
        cats = conn.execute(
            "SELECT name, description FROM categories WHERE is_deleted = 0 AND is_system = 0"
        ).fetchall()
    categories = [{"name": c["name"], "description": c["description"] or ""} for c in cats]

    llm_result = extract_document(
        page_images=page_images,
        model=model,
        api_key=api_key,
        business_names=business_names,
        business_addresses=business_addresses,
        business_tax_ids=business_tax_ids,
        categories=categories,
    )

    ext = llm_result.extraction

    # Step 5: Document type verification
    doc_type = ext.document_type
    if ext.vendor_tax_id and ext.vendor_tax_id in business_tax_ids:
        doc_type = "issued_invoice"

    # Step 6: Determine status based on confidence
    status = "processed"
    if ext.extraction_confidence is not None and ext.extraction_confidence < confidence_threshold:
        status = "needs_review"

    # Step 7: Filing
    stored_filename = generate_stored_filename(
        receipt_date=ext.receipt_date,
        vendor_receipt_id=ext.vendor_receipt_id,
        file_hash=file_hash,
    )
    save_filed(pdf_path, stored_filename, data_dir)

    # Step 8: Resolve category_id
    category_id = None
    if ext.category_name:
        with get_connection() as conn:
            cat_row = conn.execute(
                "SELECT id FROM categories WHERE name = ? AND is_deleted = 0",
                (ext.category_name,),
            ).fetchone()
            if cat_row:
                category_id = cat_row["id"]

    # Step 9: Update database
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with get_connection() as conn:
        conn.execute(
            """UPDATE documents SET
                document_type = ?,
                stored_filename = ?,
                page_count = ?,
                receipt_date = ?,
                document_title = ?,
                vendor_name = ?,
                vendor_tax_id = ?,
                vendor_receipt_id = ?,
                client_name = ?,
                client_tax_id = ?,
                description = ?,
                line_items = ?,
                subtotal = ?,
                tax_amount = ?,
                total_amount = ?,
                currency = ?,
                payment_method = ?,
                payment_identifier = ?,
                language = ?,
                additional_fields = ?,
                raw_extracted_text = ?,
                category_id = ?,
                status = ?,
                extraction_confidence = ?,
                processing_model = ?,
                processing_tokens_in = ?,
                processing_tokens_out = ?,
                processing_cost_usd = ?,
                processing_date = ?,
                processing_attempts = processing_attempts + 1,
                processing_error = NULL,
                updated_at = ?
            WHERE id = ?""",
            (
                doc_type,
                stored_filename,
                page_count,
                ext.receipt_date,
                ext.document_title,
                ext.vendor_name,
                ext.vendor_tax_id,
                ext.vendor_receipt_id,
                ext.client_name,
                ext.client_tax_id,
                ext.description,
                json.dumps(ext.line_items) if ext.line_items else None,
                ext.subtotal,
                ext.tax_amount,
                ext.total_amount,
                ext.currency,
                ext.payment_method,
                ext.payment_identifier,
                ext.language,
                json.dumps(ext.additional_fields) if ext.additional_fields else None,
                ext.raw_extracted_text,
                category_id,
                status,
                ext.extraction_confidence,
                llm_result.model,
                llm_result.tokens_in,
                llm_result.tokens_out,
                _estimate_cost(llm_result.model, llm_result.tokens_in, llm_result.tokens_out),
                now,
                now,
                doc_id,
            ),
        )

    logger.info(f"Document {doc_id} processed successfully: {status}")


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Rough cost estimate based on model. Returns USD."""
    # Approximate per-million-token pricing
    pricing = {
        "gemini/gemini-3-flash-preview": (0.10, 0.40),
        "gpt-4o": (2.50, 10.00),
        "claude-sonnet-4-20250514": (3.00, 15.00),
    }
    in_rate, out_rate = pricing.get(model, (1.0, 3.0))
    return (tokens_in * in_rate + tokens_out * out_rate) / 1_000_000
```

- [ ] **Step 4: Run tests**

Run:
```bash
uv run pytest tests/test_pipeline.py -v
```
Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/processing/pipeline.py tests/test_pipeline.py
git commit -m "feat: document processing pipeline orchestrator"
```

---

## Task 10: Processing Queue (Background Loop)

**Files:**
- Create: `backend/processing/queue.py`
- Create: `tests/test_queue.py`

- [ ] **Step 1: Write queue tests**

Create `tests/test_queue.py`:

```python
import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from backend.database import get_connection
from backend.config import init_settings, set_setting
from backend.processing.queue import (
    get_next_pending,
    reset_stuck_processing,
    get_queue_status,
)


@pytest.fixture
def setup_queue(db_path, tmp_data_dir):
    init_settings()
    return str(tmp_data_dir)


def _insert_doc(status="pending", **kwargs):
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes, status, submission_channel)
               VALUES (?, ?, ?, ?, 'web_upload')""",
            (kwargs.get("filename", "test.pdf"), kwargs.get("hash", "abc123"), 100, status),
        )
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_get_next_pending_returns_oldest(setup_queue):
    """get_next_pending returns the oldest pending document."""
    id1 = _insert_doc(hash="hash1")
    id2 = _insert_doc(hash="hash2")
    result = get_next_pending()
    assert result["id"] == id1


def test_get_next_pending_skips_non_pending(setup_queue):
    """get_next_pending skips processed/failed documents."""
    _insert_doc(status="processed", hash="hash1")
    _insert_doc(status="failed", hash="hash2")
    id3 = _insert_doc(status="pending", hash="hash3")
    result = get_next_pending()
    assert result["id"] == id3


def test_get_next_pending_none(setup_queue):
    """get_next_pending returns None when queue is empty."""
    assert get_next_pending() is None


def test_reset_stuck_processing(setup_queue):
    """Documents stuck in 'processing' are reset to 'pending'."""
    doc_id = _insert_doc(status="processing", hash="hash1")
    count = reset_stuck_processing()
    assert count == 1
    with get_connection() as conn:
        doc = conn.execute("SELECT status FROM documents WHERE id = ?", (doc_id,)).fetchone()
    assert doc["status"] == "pending"


def test_queue_status(setup_queue):
    """Queue status returns correct counts."""
    _insert_doc(status="pending", hash="h1")
    _insert_doc(status="pending", hash="h2")
    _insert_doc(status="processing", hash="h3")
    _insert_doc(status="processed", hash="h4")
    _insert_doc(status="failed", hash="h5")

    status = get_queue_status()
    assert status["pending"] == 2
    assert status["processing"] == 1
    assert status["recent_completed"] >= 1
    assert status["recent_failed"] >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/test_queue.py -v
```
Expected: FAIL — `backend.processing.queue` does not exist.

- [ ] **Step 3: Implement queue.py**

Create `backend/processing/queue.py`:

```python
import asyncio
import logging

from backend.database import get_connection
from backend.config import get_setting
from backend.processing.pipeline import process_document

logger = logging.getLogger(__name__)


def get_next_pending():
    """Get the oldest pending document, or None."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE status = 'pending' ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
    return row


def set_status(doc_id: int, status: str) -> None:
    """Set a document's status."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE documents SET status = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
            (status, doc_id),
        )


def reset_stuck_processing() -> int:
    """Reset documents stuck in 'processing' back to 'pending'. Returns count."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE documents SET status = 'pending' WHERE status = 'processing'"
        )
        count = conn.execute("SELECT changes()").fetchone()[0]
    if count > 0:
        logger.warning(f"Reset {count} stuck document(s) from 'processing' to 'pending'")
    return count


def get_queue_status() -> dict:
    """Get queue status summary."""
    with get_connection() as conn:
        pending = conn.execute(
            "SELECT COUNT(*) as c FROM documents WHERE status = 'pending'"
        ).fetchone()["c"]

        processing = conn.execute(
            "SELECT COUNT(*) as c FROM documents WHERE status = 'processing'"
        ).fetchone()["c"]

        recent_completed = conn.execute(
            "SELECT COUNT(*) as c FROM documents WHERE status = 'processed' AND processing_date > datetime('now', '-1 hour')"
        ).fetchone()["c"]

        recent_failed = conn.execute(
            "SELECT COUNT(*) as c FROM documents WHERE status = 'failed' AND updated_at > datetime('now', '-1 hour')"
        ).fetchone()["c"]

        current = conn.execute(
            "SELECT id, original_filename FROM documents WHERE status = 'processing' LIMIT 1"
        ).fetchone()

    return {
        "pending": pending,
        "processing": processing,
        "recent_completed": recent_completed,
        "recent_failed": recent_failed,
        "current_document": dict(current) if current else None,
    }


async def run_queue_loop(data_dir: str) -> None:
    """Background loop that processes documents from the queue."""
    logger.info("Processing queue started")
    reset_stuck_processing()

    while True:
        try:
            doc = get_next_pending()
            if doc is None:
                await asyncio.sleep(2.0)
                continue

            api_key = get_setting("llm_api_key")
            if not api_key:
                logger.warning("No LLM API key configured, skipping processing")
                await asyncio.sleep(10.0)
                continue

            doc_id = doc["id"]
            logger.info(f"Processing document {doc_id}: {doc['original_filename']}")
            set_status(doc_id, "processing")

            # Run in executor to not block the event loop
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, process_document, doc_id, data_dir)

            sleep_interval = get_setting("llm_sleep_interval")
            if sleep_interval > 0:
                await asyncio.sleep(sleep_interval)

        except asyncio.CancelledError:
            logger.info("Processing queue shutting down")
            break
        except Exception as e:
            logger.error(f"Queue loop error: {e}")
            await asyncio.sleep(5.0)
```

- [ ] **Step 4: Run tests**

Run:
```bash
uv run pytest tests/test_queue.py -v
```
Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/processing/queue.py tests/test_queue.py
git commit -m "feat: background processing queue with crash recovery"
```

---

## Task 11: Pydantic Models

**Files:**
- Create: `backend/models.py`

- [ ] **Step 1: Create Pydantic models**

Create `backend/models.py`:

```python
from pydantic import BaseModel, Field
from typing import Any


# === Auth ===

class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    message: str
    username: str


class AuthMeResponse(BaseModel):
    username: str


# === Categories ===

class CategoryCreate(BaseModel):
    name: str
    description: str | None = None


class CategoryUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    display_order: int | None = None


class CategoryResponse(BaseModel):
    id: int
    name: str
    description: str | None
    is_system: bool
    is_deleted: bool
    display_order: int | None
    created_at: str
    updated_at: str


# === Documents ===

class LineItem(BaseModel):
    description: str
    quantity: float | None = None
    unit_price: float | None = None


class AdditionalField(BaseModel):
    key: str
    value: str


class DocumentResponse(BaseModel):
    id: int
    document_type: str | None
    original_filename: str
    stored_filename: str | None
    file_hash: str
    file_size_bytes: int
    page_count: int | None

    submission_date: str
    submission_channel: str
    sender_identifier: str | None

    receipt_date: str | None
    document_title: str | None
    vendor_name: str | None
    vendor_tax_id: str | None
    vendor_receipt_id: str | None
    client_name: str | None
    client_tax_id: str | None
    description: str | None
    line_items: list[LineItem] | None = None
    subtotal: float | None
    tax_amount: float | None
    total_amount: float | None
    currency: str | None
    converted_amount: float | None
    conversion_rate: float | None
    payment_method: str | None
    payment_identifier: str | None
    language: str | None
    additional_fields: list[AdditionalField] | None = None
    raw_extracted_text: str | None

    category_id: int | None
    category_name: str | None = None
    status: str

    extraction_confidence: float | None
    processing_model: str | None
    processing_tokens_in: int | None
    processing_tokens_out: int | None
    processing_cost_usd: float | None
    processing_date: str | None
    processing_attempts: int
    processing_error: str | None

    manually_edited: bool
    is_deleted: bool
    edit_history: list[dict] | None = None
    user_notes: str | None

    last_exported_date: str | None
    created_at: str
    updated_at: str


class DocumentUpdate(BaseModel):
    receipt_date: str | None = None
    document_title: str | None = None
    vendor_name: str | None = None
    vendor_tax_id: str | None = None
    vendor_receipt_id: str | None = None
    client_name: str | None = None
    client_tax_id: str | None = None
    description: str | None = None
    total_amount: float | None = None
    currency: str | None = None
    category_id: int | None = None
    document_type: str | None = None
    user_notes: str | None = None


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
    total: int
    page: int
    page_size: int


class DuplicateGroup(BaseModel):
    receipt_date: str | None
    vendor_receipt_id: str | None
    documents: list[DocumentResponse]


# === Export ===

class ExportRequest(BaseModel):
    preset: str | None = None  # since_last_export, month, date_range, full_year
    date_from: str | None = None
    date_to: str | None = None
    month: str | None = None  # YYYY-MM
    year: int | None = None
    status: str | None = None
    category_id: int | None = None
    document_type: str | None = None


# === Settings ===

class SettingsUpdate(BaseModel):
    settings: dict[str, Any]


# === Stats ===

class DashboardStats(BaseModel):
    processed_this_month: int
    total_expenses_by_category: list[dict]
    pending_review_count: int
    recent_activity: list[dict]


class ProcessingCosts(BaseModel):
    total_tokens_in: int
    total_tokens_out: int
    total_cost_usd: float
    by_model: list[dict]


# === Queue ===

class QueueStatus(BaseModel):
    pending: int
    processing: int
    recent_completed: int
    recent_failed: int
    current_document: dict | None


# === Backup ===

class BackupResponse(BaseModel):
    id: int
    started_at: str
    completed_at: str | None
    status: str
    size_bytes: int | None
    destination: str | None
    error: str | None
    backup_type: str


# === Batch operations ===

class BatchReprocessRequest(BaseModel):
    document_ids: list[int] | None = None
    status: str | None = None
    category_id: int | None = None
```

- [ ] **Step 2: Verify models import correctly**

Run:
```bash
uv run python -c "from backend.models import DocumentResponse, LoginRequest; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/models.py
git commit -m "feat: Pydantic request/response models"
```

---

## Task 12: FastAPI App Shell + Auth API

**Files:**
- Create: `backend/main.py`
- Create: `backend/api/auth.py`
- Create: `tests/test_auth_api.py` (rename: actually tests for the auth API endpoints)

- [ ] **Step 1: Write auth API tests**

Create `tests/test_auth_api.py`:

```python
import pytest
import bcrypt
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.config import set_setting, init_settings


@pytest.fixture
def app(db_path, tmp_data_dir):
    """Create test app with initialized DB."""
    init_settings()
    pw_hash = bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode()
    set_setting("auth_password_hash", pw_hash)
    set_setting("auth_username", "admin")
    return create_app(str(tmp_data_dir), run_background=False)


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def authed_client(client):
    """Client with valid session cookie."""
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "testpass"})
    assert resp.status_code == 200
    return client


def test_login_success(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "testpass"})
    assert resp.status_code == 200
    assert "receiptory_session" in resp.cookies


def test_login_wrong_password(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert resp.status_code == 401


def test_login_wrong_username(client):
    resp = client.post("/api/auth/login", json={"username": "nobody", "password": "testpass"})
    assert resp.status_code == 401


def test_me_authenticated(authed_client):
    resp = authed_client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["username"] == "admin"


def test_me_unauthenticated(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/test_auth_api.py -v
```
Expected: FAIL — `backend.main` does not exist.

- [ ] **Step 3: Implement main.py**

Create `backend/main.py`:

```python
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.database import init_db
from backend.config import init_settings

logger = logging.getLogger(__name__)


def create_app(data_dir: str | None = None, run_background: bool = True) -> FastAPI:
    """Create and configure the FastAPI application."""
    if data_dir is None:
        data_dir = os.environ.get("RECEIPTORY_DATA_DIR", os.path.join(os.getcwd(), "data"))

    background_tasks: list[asyncio.Task] = []

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Initialize DB and settings
        db_path = os.path.join(data_dir, "receiptory.db")
        init_db(db_path)
        init_settings()

        # Configure logging
        from backend.config import get_setting
        log_level = get_setting("log_level")
        logging.basicConfig(
            level=getattr(logging, log_level, logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(os.path.join(data_dir, "logs", "receiptory.log")),
            ],
        )
        os.makedirs(os.path.join(data_dir, "logs"), exist_ok=True)

        if run_background:
            from backend.processing.queue import run_queue_loop
            queue_task = asyncio.create_task(run_queue_loop(data_dir))
            background_tasks.append(queue_task)

        app.state.data_dir = data_dir
        yield

        for task in background_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="Receiptory", version="0.1.0", lifespan=lifespan)

    # CORS for dev mode
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register API routes
    from backend.api.auth import router as auth_router
    app.include_router(auth_router, prefix="/api/auth", tags=["auth"])

    # Serve frontend static files in production
    frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
    if os.path.exists(frontend_dir):
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

    return app
```

- [ ] **Step 4: Implement api/auth.py**

Create `backend/api/auth.py`:

```python
from fastapi import APIRouter, Response, HTTPException, Depends

from backend.auth import (
    verify_password,
    create_session,
    require_auth,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
)
from backend.config import get_setting
from backend.models import LoginRequest, LoginResponse, AuthMeResponse

router = APIRouter()


@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest, response: Response):
    expected_username = get_setting("auth_username")
    if req.username != expected_username or not verify_password(req.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_session(req.username)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_MAX_AGE,
    )
    return LoginResponse(message="Login successful", username=req.username)


@router.get("/me", response_model=AuthMeResponse)
def me(username: str = Depends(require_auth)):
    return AuthMeResponse(username=username)
```

- [ ] **Step 5: Run tests**

Run:
```bash
uv run pytest tests/test_auth_api.py -v
```
Expected: all 5 tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/api/auth.py tests/test_auth_api.py
git commit -m "feat: FastAPI app shell with auth login/session endpoints"
```

---

## Task 13: Upload API

**Files:**
- Create: `backend/api/upload.py`
- Create: `tests/test_upload.py`

- [ ] **Step 1: Write upload tests**

Create `tests/test_upload.py`:

```python
import os
import pytest
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.config import init_settings, set_setting
from backend.database import get_connection
import bcrypt


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


def test_upload_pdf(authed_client, sample_pdf_path):
    """Uploading a PDF creates a pending document."""
    with open(sample_pdf_path, "rb") as f:
        resp = authed_client.post("/api/upload", files={"files": ("receipt.pdf", f, "application/pdf")})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["documents"]) == 1
    assert data["documents"][0]["status"] == "pending"


def test_upload_duplicate_rejected(authed_client, sample_pdf_path):
    """Uploading the same file twice rejects the duplicate."""
    with open(sample_pdf_path, "rb") as f:
        authed_client.post("/api/upload", files={"files": ("receipt.pdf", f, "application/pdf")})
    with open(sample_pdf_path, "rb") as f:
        resp = authed_client.post("/api/upload", files={"files": ("receipt.pdf", f, "application/pdf")})
    assert resp.status_code == 200
    assert len(resp.json()["duplicates"]) == 1


def test_upload_requires_auth(app, sample_pdf_path):
    """Upload without auth returns 401."""
    client = TestClient(app)
    with open(sample_pdf_path, "rb") as f:
        resp = client.post("/api/upload", files={"files": ("receipt.pdf", f, "application/pdf")})
    assert resp.status_code == 401


def test_upload_stores_original(authed_client, sample_pdf_path, tmp_data_dir):
    """Upload copies the original file to storage."""
    with open(sample_pdf_path, "rb") as f:
        resp = authed_client.post("/api/upload", files={"files": ("receipt.pdf", f, "application/pdf")})
    doc = resp.json()["documents"][0]
    original_path = os.path.join(str(tmp_data_dir), "storage", "originals", f"{doc['file_hash']}.pdf")
    assert os.path.exists(original_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/test_upload.py -v
```
Expected: FAIL — `backend.api.upload` does not exist, or route not registered.

- [ ] **Step 3: Implement api/upload.py**

Create `backend/api/upload.py`:

```python
import os
import tempfile
import logging
from fastapi import APIRouter, UploadFile, File, Depends, Request

from backend.auth import require_auth
from backend.database import get_connection
from backend.storage import compute_file_hash, save_original

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/upload")
async def upload_files(
    request: Request,
    files: list[UploadFile] = File(...),
    username: str = Depends(require_auth),
):
    """Upload one or more files for processing."""
    data_dir = request.app.state.data_dir
    created = []
    duplicates = []

    for upload in files:
        # Save to temp file to compute hash
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(upload.filename or "")[1]) as tmp:
            content = await upload.read()
            tmp.write(content)
            tmp_path = tmp.name

        try:
            file_hash = compute_file_hash(tmp_path)
            file_size = len(content)
            ext = os.path.splitext(upload.filename or ".pdf")[1].lower()

            # Check for duplicate
            with get_connection() as conn:
                existing = conn.execute(
                    "SELECT id FROM documents WHERE file_hash = ?", (file_hash,)
                ).fetchone()

            if existing:
                duplicates.append({
                    "filename": upload.filename,
                    "file_hash": file_hash,
                    "existing_id": existing["id"],
                })
                continue

            # Save original file
            save_original(tmp_path, file_hash, ext, data_dir)

            # Create document record
            with get_connection() as conn:
                conn.execute(
                    """INSERT INTO documents (original_filename, file_hash, file_size_bytes, status, submission_channel)
                       VALUES (?, ?, ?, 'pending', 'web_upload')""",
                    (upload.filename, file_hash, file_size),
                )
                doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()

            created.append({
                "id": doc["id"],
                "original_filename": doc["original_filename"],
                "file_hash": doc["file_hash"],
                "status": doc["status"],
            })

        finally:
            os.unlink(tmp_path)

    return {"documents": created, "duplicates": duplicates}
```

- [ ] **Step 4: Register the upload router in main.py**

Add to `backend/main.py`, after the auth router registration:

```python
    from backend.api.upload import router as upload_router
    app.include_router(upload_router, prefix="/api", tags=["upload"])
```

- [ ] **Step 5: Run tests**

Run:
```bash
uv run pytest tests/test_upload.py -v
```
Expected: all 4 tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/api/upload.py tests/test_upload.py backend/main.py
git commit -m "feat: file upload API with deduplication"
```

---

## Task 14: Documents API (CRUD, Search, Filter, Edit)

**Files:**
- Create: `backend/api/documents.py`
- Create: `tests/test_documents_api.py`

- [ ] **Step 1: Write document API tests**

Create `tests/test_documents_api.py`:

```python
import json
import os
import pytest
import bcrypt
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.config import init_settings, set_setting
from backend.database import get_connection


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


def _insert_doc(conn, **overrides):
    defaults = {
        "original_filename": "test.pdf",
        "file_hash": "hash_" + str(id(overrides)),
        "file_size_bytes": 100,
        "status": "processed",
        "submission_channel": "web_upload",
        "vendor_name": "Test Vendor",
        "total_amount": 100.0,
        "receipt_date": "2026-01-15",
        "raw_extracted_text": "some receipt text",
    }
    defaults.update(overrides)
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join("?" for _ in defaults)
    conn.execute(f"INSERT INTO documents ({cols}) VALUES ({placeholders})", tuple(defaults.values()))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_list_documents(authed_client, db_path):
    with get_connection() as conn:
        _insert_doc(conn, file_hash="h1")
        _insert_doc(conn, file_hash="h2")
    resp = authed_client.get("/api/documents")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2


def test_list_filter_by_status(authed_client, db_path):
    with get_connection() as conn:
        _insert_doc(conn, file_hash="h1", status="processed")
        _insert_doc(conn, file_hash="h2", status="failed")
    resp = authed_client.get("/api/documents?status=failed")
    assert resp.json()["total"] == 1
    assert resp.json()["documents"][0]["status"] == "failed"


def test_list_search_fts(authed_client, db_path):
    with get_connection() as conn:
        _insert_doc(conn, file_hash="h1", vendor_name="Office Depot", raw_extracted_text="office supplies")
        _insert_doc(conn, file_hash="h2", vendor_name="Gas Station", raw_extracted_text="fuel purchase")
    resp = authed_client.get("/api/documents?search=office")
    assert resp.json()["total"] == 1


def test_get_document(authed_client, db_path):
    with get_connection() as conn:
        doc_id = _insert_doc(conn, file_hash="h1")
    resp = authed_client.get(f"/api/documents/{doc_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == doc_id


def test_get_document_not_found(authed_client):
    resp = authed_client.get("/api/documents/999")
    assert resp.status_code == 404


def test_edit_document(authed_client, db_path):
    with get_connection() as conn:
        doc_id = _insert_doc(conn, file_hash="h1")
    resp = authed_client.patch(f"/api/documents/{doc_id}", json={"vendor_name": "Updated Vendor"})
    assert resp.status_code == 200
    assert resp.json()["vendor_name"] == "Updated Vendor"
    assert resp.json()["manually_edited"] is True

    # Check edit history
    with get_connection() as conn:
        doc = conn.execute("SELECT edit_history FROM documents WHERE id = ?", (doc_id,)).fetchone()
    history = json.loads(doc["edit_history"])
    assert len(history) == 1
    assert history[0]["field"] == "vendor_name"


def test_soft_delete(authed_client, db_path):
    with get_connection() as conn:
        doc_id = _insert_doc(conn, file_hash="h1")
    resp = authed_client.delete(f"/api/documents/{doc_id}")
    assert resp.status_code == 200
    with get_connection() as conn:
        doc = conn.execute("SELECT is_deleted FROM documents WHERE id = ?", (doc_id,)).fetchone()
    assert doc["is_deleted"] == 1


def test_reprocess_document(authed_client, db_path):
    with get_connection() as conn:
        doc_id = _insert_doc(conn, file_hash="h1", status="processed")
    resp = authed_client.post(f"/api/documents/{doc_id}/reprocess")
    assert resp.status_code == 200
    with get_connection() as conn:
        doc = conn.execute("SELECT status FROM documents WHERE id = ?", (doc_id,)).fetchone()
    assert doc["status"] == "pending"


def test_list_excludes_deleted(authed_client, db_path):
    with get_connection() as conn:
        _insert_doc(conn, file_hash="h1", is_deleted=0)
        _insert_doc(conn, file_hash="h2", is_deleted=1)
    resp = authed_client.get("/api/documents")
    assert resp.json()["total"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/test_documents_api.py -v
```
Expected: FAIL — `backend.api.documents` does not exist.

- [ ] **Step 3: Implement api/documents.py**

Create `backend/api/documents.py`:

```python
import json
import os
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from backend.auth import require_auth
from backend.database import get_connection
from backend.storage import render_page, clear_page_cache, get_file_path
from backend.config import get_setting
from backend.models import (
    DocumentResponse,
    DocumentUpdate,
    DocumentListResponse,
    BatchReprocessRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _row_to_response(row) -> dict:
    """Convert a sqlite3.Row to a DocumentResponse-compatible dict."""
    d = dict(row)
    # Parse JSON fields
    for field in ("line_items", "additional_fields", "edit_history"):
        if d.get(field) and isinstance(d[field], str):
            d[field] = json.loads(d[field])
    # Bool conversions
    d["manually_edited"] = bool(d.get("manually_edited", 0))
    d["is_deleted"] = bool(d.get("is_deleted", 0))
    return d


@router.get("/documents", response_model=DocumentListResponse)
def list_documents(
    request: Request,
    status: str | None = None,
    category_id: int | None = None,
    document_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    channel: str | None = None,
    search: str | None = None,
    sort_by: str = "submission_date",
    sort_order: str = "desc",
    page: int = 1,
    page_size: int = 20,
    username: str = Depends(require_auth),
):
    conditions = ["d.is_deleted = 0"]
    params: list = []

    if status:
        conditions.append("d.status = ?")
        params.append(status)
    if category_id:
        conditions.append("d.category_id = ?")
        params.append(category_id)
    if document_type:
        conditions.append("d.document_type = ?")
        params.append(document_type)
    if date_from:
        conditions.append("d.receipt_date >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("d.receipt_date <= ?")
        params.append(date_to)
    if channel:
        conditions.append("d.submission_channel = ?")
        params.append(channel)
    if search:
        conditions.append("d.id IN (SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?)")
        params.append(search)

    where = " AND ".join(conditions)
    allowed_sort = {"submission_date", "receipt_date", "vendor_name", "total_amount", "status", "created_at"}
    if sort_by not in allowed_sort:
        sort_by = "submission_date"
    if sort_order not in ("asc", "desc"):
        sort_order = "desc"

    with get_connection() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) as c FROM documents d WHERE {where}", params
        ).fetchone()["c"]

        offset = (page - 1) * page_size
        rows = conn.execute(
            f"""SELECT d.*, c.name as category_name
                FROM documents d
                LEFT JOIN categories c ON d.category_id = c.id
                WHERE {where}
                ORDER BY d.{sort_by} {sort_order}
                LIMIT ? OFFSET ?""",
            params + [page_size, offset],
        ).fetchall()

    return DocumentListResponse(
        documents=[_row_to_response(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/documents/duplicates")
def list_duplicates(username: str = Depends(require_auth)):
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT receipt_date, vendor_receipt_id, GROUP_CONCAT(id) as ids
               FROM documents
               WHERE is_deleted = 0
                 AND receipt_date IS NOT NULL
                 AND vendor_receipt_id IS NOT NULL
               GROUP BY receipt_date, vendor_receipt_id
               HAVING COUNT(*) > 1"""
        ).fetchall()

    groups = []
    for row in rows:
        ids = [int(x) for x in row["ids"].split(",")]
        with get_connection() as conn:
            docs = conn.execute(
                f"SELECT d.*, c.name as category_name FROM documents d LEFT JOIN categories c ON d.category_id = c.id WHERE d.id IN ({','.join('?' * len(ids))})",
                ids,
            ).fetchall()
        groups.append({
            "receipt_date": row["receipt_date"],
            "vendor_receipt_id": row["vendor_receipt_id"],
            "documents": [_row_to_response(d) for d in docs],
        })
    return groups


@router.get("/documents/{doc_id}")
def get_document(doc_id: int, username: str = Depends(require_auth)):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT d.*, c.name as category_name FROM documents d LEFT JOIN categories c ON d.category_id = c.id WHERE d.id = ?",
            (doc_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    return _row_to_response(row)


@router.patch("/documents/{doc_id}")
def edit_document(doc_id: int, update: DocumentUpdate, username: str = Depends(require_auth)):
    with get_connection() as conn:
        existing = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Document not found")

    changes = update.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Build edit history entries
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    history = json.loads(existing["edit_history"] or "[]")
    for field, new_val in changes.items():
        old_val = existing[field]
        if str(old_val) != str(new_val):
            history.append({
                "field": field,
                "old_value": str(old_val) if old_val is not None else None,
                "new_value": str(new_val) if new_val is not None else None,
                "timestamp": now,
            })

    set_clauses = [f"{k} = ?" for k in changes]
    set_clauses.extend(["manually_edited = 1", "edit_history = ?", "updated_at = ?"])
    values = list(changes.values()) + [json.dumps(history), now, doc_id]

    with get_connection() as conn:
        conn.execute(
            f"UPDATE documents SET {', '.join(set_clauses)} WHERE id = ?",
            values,
        )
        row = conn.execute(
            "SELECT d.*, c.name as category_name FROM documents d LEFT JOIN categories c ON d.category_id = c.id WHERE d.id = ?",
            (doc_id,),
        ).fetchone()

    return _row_to_response(row)


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: int, username: str = Depends(require_auth)):
    with get_connection() as conn:
        conn.execute(
            "UPDATE documents SET is_deleted = 1, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
            (doc_id,),
        )
    return {"message": "Document deleted"}


@router.post("/documents/{doc_id}/reprocess")
def reprocess_document(doc_id: int, request: Request, username: str = Depends(require_auth)):
    data_dir = request.app.state.data_dir
    with get_connection() as conn:
        conn.execute(
            """UPDATE documents SET
                status = 'pending', processing_error = NULL,
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            WHERE id = ?""",
            (doc_id,),
        )
    cache_dir = os.path.join(data_dir, "storage", "page_cache")
    clear_page_cache(cache_dir, doc_id)
    return {"message": "Document queued for reprocessing"}


@router.post("/documents/batch-reprocess")
def batch_reprocess(body: BatchReprocessRequest, request: Request, username: str = Depends(require_auth)):
    data_dir = request.app.state.data_dir
    cache_dir = os.path.join(data_dir, "storage", "page_cache")

    if body.document_ids:
        placeholders = ",".join("?" * len(body.document_ids))
        with get_connection() as conn:
            conn.execute(
                f"UPDATE documents SET status = 'pending', processing_error = NULL WHERE id IN ({placeholders})",
                body.document_ids,
            )
        for did in body.document_ids:
            clear_page_cache(cache_dir, did)
        return {"message": f"Queued {len(body.document_ids)} documents for reprocessing"}

    conditions = ["is_deleted = 0"]
    params: list = []
    if body.status:
        conditions.append("status = ?")
        params.append(body.status)
    if body.category_id:
        conditions.append("category_id = ?")
        params.append(body.category_id)

    where = " AND ".join(conditions)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE documents SET status = 'pending', processing_error = NULL WHERE {where}",
            params,
        )
        count = conn.execute("SELECT changes()").fetchone()[0]
    return {"message": f"Queued {count} documents for reprocessing"}


@router.get("/documents/{doc_id}/file/{file_type}")
def serve_file(doc_id: int, file_type: str, request: Request, username: str = Depends(require_auth)):
    data_dir = request.app.state.data_dir
    with get_connection() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    ext = os.path.splitext(doc["original_filename"])[1].lower() or ".pdf"
    try:
        path = get_file_path(file_type, doc["file_hash"], ext, data_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid file type: {file_type}")

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found on disk")
    return FileResponse(path)


@router.get("/documents/{doc_id}/pages/{page_num}")
def serve_page(doc_id: int, page_num: int, request: Request, username: str = Depends(require_auth)):
    data_dir = request.app.state.data_dir
    with get_connection() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    ext = os.path.splitext(doc["original_filename"])[1].lower() or ".pdf"
    # Try converted first, fall back to original
    converted_path = os.path.join(data_dir, "storage", "converted", f"{doc['file_hash']}.pdf")
    if os.path.exists(converted_path):
        pdf_path = converted_path
    else:
        pdf_path = get_file_path("original", doc["file_hash"], ext, data_dir)

    if not os.path.exists(pdf_path):
        raise HTTPException(status_code=404, detail="PDF not found")

    dpi = get_setting("page_render_dpi")
    cache_dir = os.path.join(data_dir, "storage", "page_cache")
    try:
        png_bytes = render_page(pdf_path, page_num, dpi=dpi, cache_dir=cache_dir, doc_id=doc_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return Response(content=png_bytes, media_type="image/png")
```

- [ ] **Step 4: Register the documents router in main.py**

Add to `backend/main.py`, after the upload router:

```python
    from backend.api.documents import router as documents_router
    app.include_router(documents_router, prefix="/api", tags=["documents"])
```

- [ ] **Step 5: Run tests**

Run:
```bash
uv run pytest tests/test_documents_api.py -v
```
Expected: all 9 tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/api/documents.py tests/test_documents_api.py backend/main.py
git commit -m "feat: documents API with CRUD, search, filter, edit, reprocess"
```

---

## Task 15: Categories API

**Files:**
- Create: `backend/api/categories.py`
- Create: `tests/test_categories_api.py`

- [ ] **Step 1: Write category API tests**

Create `tests/test_categories_api.py`:

```python
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


def test_list_categories(authed_client):
    resp = authed_client.get("/api/categories")
    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()]
    assert "office_supplies" in names
    assert "pending" in names


def test_list_excludes_deleted(authed_client):
    # Create and delete a category
    resp = authed_client.post("/api/categories", json={"name": "temp", "description": "temporary"})
    cat_id = resp.json()["id"]
    authed_client.delete(f"/api/categories/{cat_id}")

    resp = authed_client.get("/api/categories")
    names = [c["name"] for c in resp.json()]
    assert "temp" not in names

    # But include_deleted=true shows it
    resp = authed_client.get("/api/categories?include_deleted=true")
    names = [c["name"] for c in resp.json()]
    assert "temp" in names


def test_create_category(authed_client):
    resp = authed_client.post("/api/categories", json={"name": "insurance", "description": "Insurance premiums"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "insurance"
    assert resp.json()["description"] == "Insurance premiums"


def test_create_duplicate_name_fails(authed_client):
    resp = authed_client.post("/api/categories", json={"name": "travel"})
    assert resp.status_code == 400


def test_update_category(authed_client):
    resp = authed_client.post("/api/categories", json={"name": "test_cat", "description": "old"})
    cat_id = resp.json()["id"]
    resp = authed_client.patch(f"/api/categories/{cat_id}", json={"description": "new description"})
    assert resp.status_code == 200
    assert resp.json()["description"] == "new description"


def test_delete_system_category_fails(authed_client):
    # Get 'pending' system category
    resp = authed_client.get("/api/categories")
    pending = [c for c in resp.json() if c["name"] == "pending"][0]
    resp = authed_client.delete(f"/api/categories/{pending['id']}")
    assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/test_categories_api.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement api/categories.py**

Create `backend/api/categories.py`:

```python
from fastapi import APIRouter, Depends, HTTPException, Query

from backend.auth import require_auth
from backend.database import get_connection
from backend.models import CategoryCreate, CategoryUpdate, CategoryResponse

router = APIRouter()


@router.get("/categories")
def list_categories(
    include_deleted: bool = False,
    username: str = Depends(require_auth),
):
    with get_connection() as conn:
        if include_deleted:
            rows = conn.execute(
                "SELECT * FROM categories ORDER BY display_order, name"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM categories WHERE is_deleted = 0 ORDER BY display_order, name"
            ).fetchall()
    return [dict(r) for r in rows]


@router.post("/categories")
def create_category(body: CategoryCreate, username: str = Depends(require_auth)):
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM categories WHERE name = ?", (body.name,)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="Category name already exists")

        conn.execute(
            """INSERT INTO categories (name, description)
               VALUES (?, ?)""",
            (body.name, body.description),
        )
        cat_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = conn.execute("SELECT * FROM categories WHERE id = ?", (cat_id,)).fetchone()
    return dict(row)


@router.patch("/categories/{cat_id}")
def update_category(cat_id: int, body: CategoryUpdate, username: str = Depends(require_auth)):
    with get_connection() as conn:
        existing = conn.execute("SELECT * FROM categories WHERE id = ?", (cat_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Category not found")

        updates = body.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        set_clauses = [f"{k} = ?" for k in updates]
        set_clauses.append("updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')")
        values = list(updates.values()) + [cat_id]

        conn.execute(
            f"UPDATE categories SET {', '.join(set_clauses)} WHERE id = ?",
            values,
        )
        row = conn.execute("SELECT * FROM categories WHERE id = ?", (cat_id,)).fetchone()
    return dict(row)


@router.delete("/categories/{cat_id}")
def delete_category(cat_id: int, username: str = Depends(require_auth)):
    with get_connection() as conn:
        existing = conn.execute("SELECT * FROM categories WHERE id = ?", (cat_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Category not found")
        if existing["is_system"]:
            raise HTTPException(status_code=400, detail="Cannot delete system categories")

        conn.execute(
            "UPDATE categories SET is_deleted = 1, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
            (cat_id,),
        )
    return {"message": "Category deleted"}
```

- [ ] **Step 4: Register in main.py**

Add after documents router:

```python
    from backend.api.categories import router as categories_router
    app.include_router(categories_router, prefix="/api", tags=["categories"])
```

- [ ] **Step 5: Run tests**

Run:
```bash
uv run pytest tests/test_categories_api.py -v
```
Expected: all 6 tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/api/categories.py tests/test_categories_api.py backend/main.py
git commit -m "feat: categories API with CRUD and soft delete"
```

---

## Task 16: Settings API + LLM Test + Queue Status + Stats + Logs

**Files:**
- Create: `backend/api/settings.py`
- Create: `backend/api/queue.py`
- Create: `backend/api/stats.py`
- Create: `backend/api/logs.py`
- Create: `tests/test_settings_api.py`
- Create: `tests/test_stats.py`

- [ ] **Step 1: Write settings and stats tests**

Create `tests/test_settings_api.py`:

```python
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
```

Create `tests/test_stats.py`:

```python
import pytest
import bcrypt
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.config import init_settings, set_setting
from backend.database import get_connection


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


def test_dashboard_stats(authed_client, db_path):
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes, status, total_amount, category_id, submission_channel)
               VALUES ('test.pdf', 'h1', 100, 'processed', 50.0, 4, 'web_upload')"""
        )
    resp = authed_client.get("/api/stats/dashboard")
    assert resp.status_code == 200
    assert "processed_this_month" in resp.json()


def test_processing_costs(authed_client, db_path):
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes, status, processing_model, processing_tokens_in, processing_tokens_out, processing_cost_usd, submission_channel)
               VALUES ('test.pdf', 'h1', 100, 'processed', 'gemini/gemini-3-flash-preview', 1000, 500, 0.001, 'web_upload')"""
        )
    resp = authed_client.get("/api/stats/processing-costs")
    assert resp.status_code == 200
    assert resp.json()["total_cost_usd"] > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/test_settings_api.py tests/test_stats.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement api/settings.py**

Create `backend/api/settings.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from unittest.mock import MagicMock

from backend.auth import require_auth
from backend.config import get_all_settings_masked, set_setting, get_setting
from backend.models import SettingsUpdate

router = APIRouter()


@router.get("/settings")
def get_settings(username: str = Depends(require_auth)):
    return get_all_settings_masked()


@router.patch("/settings")
def patch_settings(body: SettingsUpdate, username: str = Depends(require_auth)):
    for key, value in body.settings.items():
        if key == "auth_password_hash":
            # Password changes should be handled specially
            import bcrypt
            value = bcrypt.hashpw(value.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        set_setting(key, value)
    return {"message": "Settings updated"}


@router.post("/settings/test-llm")
def test_llm(username: str = Depends(require_auth)):
    """Test LLM connectivity by sending a minimal request."""
    import litellm

    model = get_setting("llm_model")
    api_key = get_setting("llm_api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="No API key configured")

    try:
        response = litellm.completion(
            model=model,
            api_key=api_key,
            messages=[{"role": "user", "content": "Reply with OK"}],
            max_tokens=10,
        )
        return {
            "status": "ok",
            "model": model,
            "response": response.choices[0].message.content,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM test failed: {e}")
```

- [ ] **Step 4: Implement api/queue.py**

Create `backend/api/queue.py`:

```python
from fastapi import APIRouter, Depends

from backend.auth import require_auth
from backend.processing.queue import get_queue_status

router = APIRouter()


@router.get("/queue/status")
def queue_status(username: str = Depends(require_auth)):
    return get_queue_status()
```

- [ ] **Step 5: Implement api/stats.py**

Create `backend/api/stats.py`:

```python
from fastapi import APIRouter, Depends

from backend.auth import require_auth
from backend.database import get_connection

router = APIRouter()


@router.get("/stats/dashboard")
def dashboard_stats(username: str = Depends(require_auth)):
    with get_connection() as conn:
        processed = conn.execute(
            """SELECT COUNT(*) as c FROM documents
               WHERE status = 'processed'
               AND processing_date >= strftime('%Y-%m-01T00:00:00Z', 'now')"""
        ).fetchone()["c"]

        pending_review = conn.execute(
            "SELECT COUNT(*) as c FROM documents WHERE status = 'needs_review' AND is_deleted = 0"
        ).fetchone()["c"]

        expenses = conn.execute(
            """SELECT c.name, SUM(d.total_amount) as total
               FROM documents d
               LEFT JOIN categories c ON d.category_id = c.id
               WHERE d.status = 'processed' AND d.is_deleted = 0 AND d.total_amount IS NOT NULL
               GROUP BY c.name
               ORDER BY total DESC"""
        ).fetchall()

        recent = conn.execute(
            """SELECT id, original_filename, status, submission_channel, submission_date
               FROM documents
               ORDER BY created_at DESC LIMIT 10"""
        ).fetchall()

    return {
        "processed_this_month": processed,
        "pending_review_count": pending_review,
        "total_expenses_by_category": [{"category": r["name"], "total": r["total"]} for r in expenses],
        "recent_activity": [dict(r) for r in recent],
    }


@router.get("/stats/processing-costs")
def processing_costs(username: str = Depends(require_auth)):
    with get_connection() as conn:
        totals = conn.execute(
            """SELECT
                COALESCE(SUM(processing_tokens_in), 0) as total_in,
                COALESCE(SUM(processing_tokens_out), 0) as total_out,
                COALESCE(SUM(processing_cost_usd), 0) as total_cost
               FROM documents WHERE processing_model IS NOT NULL"""
        ).fetchone()

        by_model = conn.execute(
            """SELECT processing_model,
                COUNT(*) as doc_count,
                SUM(processing_tokens_in) as tokens_in,
                SUM(processing_tokens_out) as tokens_out,
                SUM(processing_cost_usd) as cost
               FROM documents
               WHERE processing_model IS NOT NULL
               GROUP BY processing_model"""
        ).fetchall()

    return {
        "total_tokens_in": totals["total_in"],
        "total_tokens_out": totals["total_out"],
        "total_cost_usd": totals["total_cost"],
        "by_model": [dict(r) for r in by_model],
    }
```

- [ ] **Step 6: Implement api/logs.py**

Create `backend/api/logs.py`:

```python
import os
from fastapi import APIRouter, Depends, Request, Query

from backend.auth import require_auth

router = APIRouter()


@router.get("/logs")
def get_logs(
    request: Request,
    limit: int = Query(100, le=1000),
    level: str | None = None,
    username: str = Depends(require_auth),
):
    data_dir = request.app.state.data_dir
    log_path = os.path.join(data_dir, "logs", "receiptory.log")

    if not os.path.exists(log_path):
        return {"lines": []}

    with open(log_path, "r") as f:
        lines = f.readlines()

    # Filter by level if specified
    if level:
        level_upper = level.upper()
        lines = [l for l in lines if level_upper in l]

    # Return last N lines
    return {"lines": lines[-limit:]}
```

- [ ] **Step 7: Register all new routers in main.py**

Add to `backend/main.py` after existing router registrations:

```python
    from backend.api.settings import router as settings_router
    app.include_router(settings_router, prefix="/api", tags=["settings"])

    from backend.api.queue import router as queue_router
    app.include_router(queue_router, prefix="/api", tags=["queue"])

    from backend.api.stats import router as stats_router
    app.include_router(stats_router, prefix="/api", tags=["stats"])

    from backend.api.logs import router as logs_router
    app.include_router(logs_router, prefix="/api", tags=["logs"])
```

- [ ] **Step 8: Run tests**

Run:
```bash
uv run pytest tests/test_settings_api.py tests/test_stats.py -v
```
Expected: all 5 tests pass.

- [ ] **Step 9: Commit**

```bash
git add backend/api/settings.py backend/api/queue.py backend/api/stats.py backend/api/logs.py tests/test_settings_api.py tests/test_stats.py backend/main.py
git commit -m "feat: settings, queue status, stats, and logs APIs"
```

---

## Task 17: Export API

**Files:**
- Create: `backend/api/export.py`
- Create: `tests/test_export.py`

- [ ] **Step 1: Write export tests**

Create `tests/test_export.py`:

```python
import io
import csv
import zipfile
import pytest
import bcrypt
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.config import init_settings, set_setting
from backend.database import get_connection
import shutil
import os


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


@pytest.fixture
def docs_with_files(authed_client, db_path, tmp_data_dir, sample_pdf_path):
    """Create documents with actual filed PDFs."""
    filed_dir = os.path.join(str(tmp_data_dir), "storage", "filed")
    os.makedirs(filed_dir, exist_ok=True)

    with get_connection() as conn:
        conn.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes, status,
               stored_filename, receipt_date, vendor_name, total_amount, category_id, submission_channel)
               VALUES ('r1.pdf', 'h1', 100, 'processed', '2026-01-15-INV001-h1.pdf', '2026-01-15', 'Vendor A', 100.0, 4, 'web_upload')"""
        )
        conn.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes, status,
               stored_filename, receipt_date, vendor_name, total_amount, category_id, submission_channel)
               VALUES ('r2.pdf', 'h2', 200, 'processed', '2026-01-20-INV002-h2.pdf', '2026-01-20', 'Vendor B', 200.0, 5, 'web_upload')"""
        )

    # Create filed PDFs
    shutil.copy2(sample_pdf_path, os.path.join(filed_dir, "2026-01-15-INV001-h1.pdf"))
    shutil.copy2(sample_pdf_path, os.path.join(filed_dir, "2026-01-20-INV002-h2.pdf"))


def test_export_zip_structure(authed_client, docs_with_files):
    resp = authed_client.post("/api/export", json={"date_from": "2026-01-01", "date_to": "2026-12-31"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert any("metadata.csv" in n for n in names)
    assert any(".pdf" in n for n in names)


def test_export_csv_content(authed_client, docs_with_files):
    resp = authed_client.post("/api/export", json={"date_from": "2026-01-01", "date_to": "2026-12-31"})
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    csv_content = zf.read("metadata.csv").decode("utf-8")
    reader = csv.DictReader(io.StringIO(csv_content))
    rows = list(reader)
    assert len(rows) == 2
    assert rows[0]["vendor_name"] in ("Vendor A", "Vendor B")


def test_export_updates_last_exported(authed_client, docs_with_files):
    authed_client.post("/api/export", json={"date_from": "2026-01-01", "date_to": "2026-12-31"})
    with get_connection() as conn:
        docs = conn.execute("SELECT last_exported_date FROM documents WHERE status = 'processed'").fetchall()
    for doc in docs:
        assert doc["last_exported_date"] is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/test_export.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement api/export.py**

Create `backend/api/export.py`:

```python
import csv
import io
import os
import zipfile
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from backend.auth import require_auth
from backend.database import get_connection
from backend.models import ExportRequest

logger = logging.getLogger(__name__)
router = APIRouter()

EXPORT_CSV_FIELDS = [
    "id", "document_type", "original_filename", "stored_filename", "receipt_date",
    "vendor_name", "vendor_tax_id", "vendor_receipt_id", "client_name", "client_tax_id",
    "description", "subtotal", "tax_amount", "total_amount", "currency",
    "payment_method", "payment_identifier", "language", "status",
    "submission_date", "submission_channel", "category_name",
]


@router.post("/export")
def export_documents(body: ExportRequest, request: Request, username: str = Depends(require_auth)):
    data_dir = request.app.state.data_dir
    filed_dir = os.path.join(data_dir, "storage", "filed")

    # Build query conditions
    conditions = ["d.is_deleted = 0", "d.status = 'processed'"]
    params: list = []

    if body.preset == "since_last_export":
        conditions.append("d.last_exported_date IS NULL")
    elif body.preset == "month" and body.month:
        conditions.append("d.receipt_date >= ?")
        conditions.append("d.receipt_date < ?")
        year, month = body.month.split("-")
        next_month = int(month) + 1
        next_year = int(year)
        if next_month > 12:
            next_month = 1
            next_year += 1
        params.extend([f"{year}-{month}-01", f"{next_year:04d}-{next_month:02d}-01"])
    elif body.preset == "full_year" and body.year:
        conditions.append("d.receipt_date >= ?")
        conditions.append("d.receipt_date < ?")
        params.extend([f"{body.year}-01-01", f"{body.year + 1}-01-01"])
    else:
        if body.date_from:
            conditions.append("d.receipt_date >= ?")
            params.append(body.date_from)
        if body.date_to:
            conditions.append("d.receipt_date <= ?")
            params.append(body.date_to)

    if body.status:
        conditions.append("d.status = ?")
        params.append(body.status)
    if body.category_id:
        conditions.append("d.category_id = ?")
        params.append(body.category_id)
    if body.document_type:
        conditions.append("d.document_type = ?")
        params.append(body.document_type)

    where = " AND ".join(conditions)

    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT d.*, c.name as category_name
                FROM documents d
                LEFT JOIN categories c ON d.category_id = c.id
                WHERE {where}
                ORDER BY d.receipt_date""",
            params,
        ).fetchall()

    # Build zip in memory
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add PDFs organized by category
        for row in rows:
            cat_name = row["category_name"] or "uncategorized"
            stored = row["stored_filename"]
            if stored:
                pdf_path = os.path.join(filed_dir, stored)
                if os.path.exists(pdf_path):
                    zf.write(pdf_path, f"{cat_name}/{stored}")

        # Add CSV metadata
        csv_buf = io.StringIO()
        writer = csv.DictWriter(csv_buf, fieldnames=EXPORT_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({f: row[f] for f in EXPORT_CSV_FIELDS if f in row.keys()})
        zf.writestr("metadata.csv", csv_buf.getvalue())

    # Update last_exported_date
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc_ids = [row["id"] for row in rows]
    if doc_ids:
        placeholders = ",".join("?" * len(doc_ids))
        with get_connection() as conn:
            conn.execute(
                f"UPDATE documents SET last_exported_date = ? WHERE id IN ({placeholders})",
                [now] + doc_ids,
            )

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=receiptory_export.zip"},
    )
```

- [ ] **Step 4: Register in main.py**

Add after logs router:

```python
    from backend.api.export import router as export_router
    app.include_router(export_router, prefix="/api", tags=["export"])
```

- [ ] **Step 5: Run tests**

Run:
```bash
uv run pytest tests/test_export.py -v
```
Expected: all 3 tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/api/export.py tests/test_export.py backend/main.py
git commit -m "feat: export API with zip generation and CSV metadata"
```

---

## Task 18: Backup System

**Files:**
- Create: `backend/backup/scheduler.py`
- Create: `backend/backup/runner.py`
- Create: `backend/backup/rclone.py`
- Create: `backend/api/backup.py`
- Create: `tests/test_backup.py`

- [ ] **Step 1: Write backup tests**

Create `tests/test_backup.py`:

```python
import json
import os
import pytest
from backend.backup.runner import build_backup
from backend.backup.scheduler import determine_backup_type
from backend.database import get_connection
from backend.config import init_settings
from datetime import date


def test_determine_backup_type_daily():
    assert determine_backup_type(date(2026, 3, 15)) == "daily"


def test_determine_backup_type_weekly():
    # 2026-03-22 is a Sunday
    assert determine_backup_type(date(2026, 3, 22)) == "weekly"


def test_determine_backup_type_monthly():
    assert determine_backup_type(date(2026, 3, 1)) == "monthly"


def test_determine_backup_type_quarterly():
    assert determine_backup_type(date(2026, 4, 1)) == "quarterly"


def test_build_backup_creates_archive(db_path, tmp_data_dir):
    """build_backup produces a directory with DB copy, JSONL, and settings."""
    init_settings()
    data_dir = str(tmp_data_dir)

    # Insert a document
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes, status, submission_channel, vendor_name)
               VALUES ('test.pdf', 'h1', 100, 'processed', 'web_upload', 'Test Vendor')"""
        )

    backup_dir = build_backup(data_dir)
    assert os.path.exists(os.path.join(backup_dir, "receiptory.db"))
    assert os.path.exists(os.path.join(backup_dir, "metadata.jsonl"))
    assert os.path.exists(os.path.join(backup_dir, "settings.json"))

    # Check JSONL content
    with open(os.path.join(backup_dir, "metadata.jsonl")) as f:
        lines = f.readlines()
    assert len(lines) == 1
    doc = json.loads(lines[0])
    assert doc["vendor_name"] == "Test Vendor"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run pytest tests/test_backup.py -v
```
Expected: FAIL.

- [ ] **Step 3: Implement backup/scheduler.py**

Create `backend/backup/scheduler.py`:

```python
import asyncio
import logging
from datetime import date, datetime, timezone

from croniter import croniter

from backend.config import get_setting
from backend.database import get_connection
from backend.backup.runner import build_backup
from backend.backup.rclone import upload_backup, apply_retention

logger = logging.getLogger(__name__)


def determine_backup_type(d: date) -> str:
    """Determine the backup type based on the date."""
    if d.month in (1, 4, 7, 10) and d.day == 1:
        return "quarterly"
    if d.day == 1:
        return "monthly"
    if d.weekday() == 6:  # Sunday
        return "weekly"
    return "daily"


async def run_backup_scheduler(data_dir: str) -> None:
    """Background loop that runs backups on schedule."""
    logger.info("Backup scheduler started")

    while True:
        try:
            schedule = get_setting("backup_schedule")
            destination = get_setting("backup_destination")

            if not destination:
                await asyncio.sleep(60)
                continue

            now = datetime.now(timezone.utc)
            cron = croniter(schedule, now)
            next_run = cron.get_next(datetime)
            wait_seconds = (next_run - now).total_seconds()

            logger.info(f"Next backup scheduled at {next_run} ({wait_seconds:.0f}s)")
            await asyncio.sleep(wait_seconds)

            await run_backup(data_dir, "scheduled")

        except asyncio.CancelledError:
            logger.info("Backup scheduler shutting down")
            break
        except Exception as e:
            logger.error(f"Backup scheduler error: {e}")
            await asyncio.sleep(300)


async def run_backup(data_dir: str, trigger: str = "manual") -> int:
    """Execute a backup. Returns the backup record ID."""
    today = date.today()
    backup_type = trigger if trigger == "manual" else determine_backup_type(today)
    destination = get_setting("backup_destination")

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO backups (backup_type, destination, status) VALUES (?, ?, 'running')",
            (backup_type, destination),
        )
        backup_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    try:
        loop = asyncio.get_event_loop()
        backup_dir = await loop.run_in_executor(None, build_backup, data_dir)

        if destination:
            await loop.run_in_executor(None, upload_backup, backup_dir, destination, backup_type, today)
            await loop.run_in_executor(None, apply_retention, destination, data_dir)

        size = _dir_size(backup_dir)
        with get_connection() as conn:
            conn.execute(
                """UPDATE backups SET
                    status = 'completed',
                    completed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                    size_bytes = ?
                WHERE id = ?""",
                (size, backup_id),
            )
        logger.info(f"Backup {backup_id} completed ({size} bytes)")

    except Exception as e:
        logger.error(f"Backup {backup_id} failed: {e}")
        with get_connection() as conn:
            conn.execute(
                """UPDATE backups SET
                    status = 'failed',
                    completed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                    error = ?
                WHERE id = ?""",
                (str(e), backup_id),
            )

    return backup_id


def _dir_size(path: str) -> int:
    import os
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            total += os.path.getsize(os.path.join(dirpath, f))
    return total
```

- [ ] **Step 4: Implement backup/runner.py**

Create `backend/backup/runner.py`:

```python
import json
import os
import shutil
import logging
import tempfile
from datetime import datetime, timezone

from backend.database import get_connection
from backend.config import get_all_settings

logger = logging.getLogger(__name__)


def build_backup(data_dir: str) -> str:
    """Assemble backup contents into a temporary directory. Returns path."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = os.path.join(tempfile.gettempdir(), f"receiptory_backup_{timestamp}")
    os.makedirs(backup_dir, exist_ok=True)

    # Copy SQLite database
    db_path = os.path.join(data_dir, "receiptory.db")
    if os.path.exists(db_path):
        shutil.copy2(db_path, os.path.join(backup_dir, "receiptory.db"))

    # Copy storage files
    storage_dir = os.path.join(data_dir, "storage")
    if os.path.exists(storage_dir):
        shutil.copytree(storage_dir, os.path.join(backup_dir, "storage"), dirs_exist_ok=True)

    # Copy logs
    logs_dir = os.path.join(data_dir, "logs")
    if os.path.exists(logs_dir):
        shutil.copytree(logs_dir, os.path.join(backup_dir, "logs"), dirs_exist_ok=True)

    # Export JSONL metadata
    _export_jsonl(os.path.join(backup_dir, "metadata.jsonl"))

    # Export settings
    settings = get_all_settings()
    with open(os.path.join(backup_dir, "settings.json"), "w") as f:
        json.dump(settings, f, indent=2, default=str)

    logger.info(f"Backup assembled at {backup_dir}")
    return backup_dir


def _export_jsonl(output_path: str) -> None:
    """Export all document metadata as JSONL."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT d.*, c.name as category_name
               FROM documents d
               LEFT JOIN categories c ON d.category_id = c.id"""
        ).fetchall()

    with open(output_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(dict(row), default=str, ensure_ascii=False) + "\n")
```

- [ ] **Step 5: Implement backup/rclone.py**

Create `backend/backup/rclone.py`:

```python
import subprocess
import os
import logging
from datetime import date

from backend.config import get_setting

logger = logging.getLogger(__name__)


def upload_backup(backup_dir: str, destination: str, backup_type: str, backup_date: date) -> None:
    """Upload backup directory to rclone destination."""
    remote_path = f"{destination}/{backup_date.isoformat()}-{backup_type}"
    cmd = ["rclone", "copy", backup_dir, remote_path, "--progress"]

    logger.info(f"Uploading backup to {remote_path}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"rclone upload failed: {result.stderr}")
    logger.info("Backup upload complete")


def apply_retention(destination: str, data_dir: str) -> None:
    """Delete backups that exceed retention policy."""
    retention_daily = get_setting("backup_retention_daily")
    retention_weekly = get_setting("backup_retention_weekly")
    retention_monthly = get_setting("backup_retention_monthly")

    # List remote directories
    cmd = ["rclone", "lsf", destination, "--dirs-only"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.warning(f"Failed to list backups for retention: {result.stderr}")
        return

    today = date.today()
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        dirname = line.strip("/")
        try:
            parts = dirname.split("-")
            backup_date = date(int(parts[0]), int(parts[1]), int(parts[2]))
            backup_type = parts[3] if len(parts) > 3 else "daily"
        except (ValueError, IndexError):
            continue

        days_old = (today - backup_date).days
        should_delete = False

        if backup_type == "daily" and days_old > retention_daily:
            should_delete = True
        elif backup_type == "weekly" and days_old > retention_weekly * 7:
            should_delete = True
        elif backup_type == "monthly" and days_old > retention_monthly * 30:
            should_delete = True
        # quarterly: never auto-delete

        if should_delete:
            logger.info(f"Deleting expired backup: {dirname}")
            subprocess.run(
                ["rclone", "purge", f"{destination}/{dirname}"],
                capture_output=True,
            )
```

- [ ] **Step 6: Implement api/backup.py**

Create `backend/api/backup.py`:

```python
import asyncio
from fastapi import APIRouter, Depends, Request, HTTPException

from backend.auth import require_auth
from backend.database import get_connection
from backend.backup.scheduler import run_backup

router = APIRouter()


@router.post("/backup/trigger")
async def trigger_backup(request: Request, username: str = Depends(require_auth)):
    data_dir = request.app.state.data_dir
    backup_id = await run_backup(data_dir, trigger="manual")
    return {"message": "Backup triggered", "backup_id": backup_id}


@router.get("/backup/history")
def backup_history(username: str = Depends(require_auth)):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM backups ORDER BY started_at DESC LIMIT 50"
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/backup/{backup_id}/download")
def download_backup(backup_id: int, username: str = Depends(require_auth)):
    # For local backups this could serve the file; for remote, redirect to rclone
    raise HTTPException(status_code=501, detail="Download from remote not yet implemented")


@router.delete("/backup/{backup_id}")
def delete_backup(backup_id: int, username: str = Depends(require_auth)):
    with get_connection() as conn:
        conn.execute("DELETE FROM backups WHERE id = ?", (backup_id,))
    return {"message": "Backup record deleted"}
```

- [ ] **Step 7: Register backup router and scheduler in main.py**

Add backup router after export router:

```python
    from backend.api.backup import router as backup_router
    app.include_router(backup_router, prefix="/api", tags=["backup"])
```

Add backup scheduler to lifespan (alongside queue):

```python
        if run_background:
            from backend.processing.queue import run_queue_loop
            from backend.backup.scheduler import run_backup_scheduler
            queue_task = asyncio.create_task(run_queue_loop(data_dir))
            backup_task = asyncio.create_task(run_backup_scheduler(data_dir))
            background_tasks.extend([queue_task, backup_task])
```

- [ ] **Step 8: Run tests**

Run:
```bash
uv run pytest tests/test_backup.py -v
```
Expected: all 5 tests pass.

- [ ] **Step 9: Commit**

```bash
git add backend/backup/ backend/api/backup.py tests/test_backup.py backend/main.py
git commit -m "feat: backup system with scheduler, runner, and rclone upload"
```

---

## Task 19: Frontend Scaffold (Vite + React + shadcn/ui)

**Files:**
- Create: `frontend/` directory with Vite + React + TypeScript project
- Create: `frontend/src/lib/api.ts`
- Create: `frontend/src/contexts/AuthContext.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/main.tsx`

- [ ] **Step 1: Scaffold Vite project**

```bash
cd /e/Projects/Lev/SmallProjects/Receiptory
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
```

- [ ] **Step 2: Install shadcn/ui dependencies**

```bash
cd /e/Projects/Lev/SmallProjects/Receiptory/frontend
npm install tailwindcss @tailwindcss/vite
npm install class-variance-authority clsx tailwind-merge lucide-react
npx shadcn@latest init -d
```

Follow the prompts choosing defaults (New York style, Zinc color, CSS variables).

- [ ] **Step 3: Add core shadcn components**

```bash
cd /e/Projects/Lev/SmallProjects/Receiptory/frontend
npx shadcn@latest add button input label card table badge dialog select tabs textarea toast dropdown-menu separator
```

- [ ] **Step 4: Install React Router**

```bash
cd /e/Projects/Lev/SmallProjects/Receiptory/frontend
npm install react-router-dom
```

- [ ] **Step 5: Create API client**

Create `frontend/src/lib/api.ts`:

```typescript
const API_BASE = import.meta.env.DEV ? "http://localhost:8080/api" : "/api";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
    ...options,
  });

  if (res.status === 401) {
    window.location.href = "/login";
    throw new Error("Unauthorized");
  }

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || "Request failed");
  }

  if (res.headers.get("content-type")?.includes("application/json")) {
    return res.json();
  }
  return res as unknown as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  upload: async (files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    const res = await fetch(`${API_BASE}/upload`, {
      method: "POST",
      credentials: "include",
      body: form,
    });
    if (!res.ok) throw new Error("Upload failed");
    return res.json();
  },
  exportDocs: async (body: unknown) => {
    const res = await fetch(`${API_BASE}/export`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error("Export failed");
    return res.blob();
  },
};
```

- [ ] **Step 6: Create Auth context**

Create `frontend/src/contexts/AuthContext.tsx`:

```tsx
import { createContext, useContext, useState, useEffect, ReactNode } from "react";
import { api } from "@/lib/api";

interface AuthState {
  username: string | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [username, setUsername] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get<{ username: string }>("/auth/me")
      .then((data) => setUsername(data.username))
      .catch(() => setUsername(null))
      .finally(() => setLoading(false));
  }, []);

  const login = async (user: string, password: string) => {
    await api.post("/auth/login", { username: user, password });
    setUsername(user);
  };

  const logout = () => {
    setUsername(null);
    window.location.href = "/login";
  };

  return (
    <AuthContext.Provider value={{ username, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
```

- [ ] **Step 7: Create App.tsx with routing**

Create `frontend/src/App.tsx`:

```tsx
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "@/contexts/AuthContext";
import LoginPage from "@/pages/LoginPage";

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { username, loading } = useAuth();
  if (loading) return <div className="flex items-center justify-center h-screen">Loading...</div>;
  if (!username) return <Navigate to="/login" />;
  return <>{children}</>;
}

function AppLayout({ children }: { children: React.ReactNode }) {
  const { username, logout } = useAuth();
  return (
    <div className="min-h-screen bg-background">
      <header className="border-b px-6 py-3 flex items-center justify-between">
        <nav className="flex gap-4">
          <a href="/" className="font-semibold">Receiptory</a>
          <a href="/documents" className="text-muted-foreground hover:text-foreground">Documents</a>
          <a href="/export" className="text-muted-foreground hover:text-foreground">Export</a>
          <a href="/settings" className="text-muted-foreground hover:text-foreground">Settings</a>
        </nav>
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">{username}</span>
          <button onClick={logout} className="text-sm underline">Logout</button>
        </div>
      </header>
      <main className="p-6">{children}</main>
    </div>
  );
}

// Placeholder pages — implemented in subsequent tasks
function Placeholder({ name }: { name: string }) {
  return <div className="text-xl">{name} — coming soon</div>;
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<ProtectedRoute><AppLayout><Placeholder name="Dashboard" /></AppLayout></ProtectedRoute>} />
          <Route path="/documents" element={<ProtectedRoute><AppLayout><Placeholder name="Documents" /></AppLayout></ProtectedRoute>} />
          <Route path="/documents/:id" element={<ProtectedRoute><AppLayout><Placeholder name="Document Detail" /></AppLayout></ProtectedRoute>} />
          <Route path="/export" element={<ProtectedRoute><AppLayout><Placeholder name="Export" /></AppLayout></ProtectedRoute>} />
          <Route path="/settings" element={<ProtectedRoute><AppLayout><Placeholder name="Settings" /></AppLayout></ProtectedRoute>} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
```

- [ ] **Step 8: Create LoginPage**

Create `frontend/src/pages/LoginPage.tsx`:

```tsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      await login(username, password);
      navigate("/");
    } catch {
      setError("Invalid credentials");
    }
  };

  return (
    <div className="flex items-center justify-center min-h-screen">
      <Card className="w-[400px]">
        <CardHeader>
          <CardTitle>Receiptory</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <Label htmlFor="username">Username</Label>
              <Input id="username" value={username} onChange={(e) => setUsername(e.target.value)} />
            </div>
            <div>
              <Label htmlFor="password">Password</Label>
              <Input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button type="submit" className="w-full">Login</Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
```

- [ ] **Step 9: Update main.tsx**

Replace `frontend/src/main.tsx`:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
```

- [ ] **Step 10: Verify frontend builds**

```bash
cd /e/Projects/Lev/SmallProjects/Receiptory/frontend
npm run build
```
Expected: builds to `frontend/dist/` without errors.

- [ ] **Step 11: Commit**

```bash
git add frontend/
git commit -m "feat: frontend scaffold with Vite, React, shadcn/ui, routing, auth"
```

---

## Task 20: Frontend — Dashboard Page

**Files:**
- Create: `frontend/src/pages/DashboardPage.tsx`
- Modify: `frontend/src/App.tsx` — replace Dashboard placeholder

- [ ] **Step 1: Create DashboardPage**

Create `frontend/src/pages/DashboardPage.tsx`:

```tsx
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

interface DashboardData {
  processed_this_month: number;
  pending_review_count: number;
  total_expenses_by_category: { category: string; total: number }[];
  recent_activity: { id: number; original_filename: string; status: string; submission_date: string }[];
}

interface QueueData {
  pending: number;
  processing: number;
}

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardData | null>(null);
  const [queue, setQueue] = useState<QueueData | null>(null);

  useEffect(() => {
    api.get<DashboardData>("/stats/dashboard").then(setStats);
    api.get<QueueData>("/queue/status").then(setQueue);
  }, []);

  if (!stats) return <div>Loading...</div>;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Dashboard</h1>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm font-medium">Processed This Month</CardTitle></CardHeader>
          <CardContent><div className="text-2xl font-bold">{stats.processed_this_month}</div></CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm font-medium">Needs Review</CardTitle></CardHeader>
          <CardContent><div className="text-2xl font-bold">{stats.pending_review_count}</div></CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm font-medium">Queue</CardTitle></CardHeader>
          <CardContent><div className="text-2xl font-bold">{queue?.pending ?? 0} pending</div></CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm font-medium">Processing</CardTitle></CardHeader>
          <CardContent><div className="text-2xl font-bold">{queue?.processing ?? 0}</div></CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Card>
          <CardHeader><CardTitle>Expenses by Category</CardTitle></CardHeader>
          <CardContent>
            {stats.total_expenses_by_category.length === 0 ? (
              <p className="text-muted-foreground">No data yet</p>
            ) : (
              <div className="space-y-2">
                {stats.total_expenses_by_category.map((e) => (
                  <div key={e.category} className="flex justify-between">
                    <span>{e.category || "Uncategorized"}</span>
                    <span className="font-mono">{e.total?.toFixed(2)}</span>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Recent Activity</CardTitle></CardHeader>
          <CardContent>
            <div className="space-y-2">
              {stats.recent_activity.map((a) => (
                <div key={a.id} className="flex items-center justify-between text-sm">
                  <a href={`/documents/${a.id}`} className="hover:underline truncate max-w-[200px]">
                    {a.original_filename}
                  </a>
                  <Badge variant={a.status === "processed" ? "default" : a.status === "failed" ? "destructive" : "secondary"}>
                    {a.status}
                  </Badge>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Wire up in App.tsx**

Replace the Dashboard placeholder route in `App.tsx`:

```tsx
import DashboardPage from "@/pages/DashboardPage";
// ...
<Route path="/" element={<ProtectedRoute><AppLayout><DashboardPage /></AppLayout></ProtectedRoute>} />
```

- [ ] **Step 3: Verify build**

```bash
cd /e/Projects/Lev/SmallProjects/Receiptory/frontend && npm run build
```
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/DashboardPage.tsx frontend/src/App.tsx
git commit -m "feat: dashboard page with stats cards and recent activity"
```

---

## Task 21: Frontend — Documents Page (Browser + Upload)

**Files:**
- Create: `frontend/src/pages/DocumentsPage.tsx`
- Create: `frontend/src/components/DocumentTable.tsx`
- Create: `frontend/src/components/FilterBar.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Create FilterBar component**

Create `frontend/src/components/FilterBar.tsx`:

```tsx
import { useEffect, useState } from "react";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

interface Category {
  id: number;
  name: string;
}

interface Filters {
  search: string;
  status: string;
  category_id: string;
  document_type: string;
  date_from: string;
  date_to: string;
}

interface FilterBarProps {
  filters: Filters;
  onChange: (filters: Filters) => void;
}

export default function FilterBar({ filters, onChange }: FilterBarProps) {
  const [categories, setCategories] = useState<Category[]>([]);

  useEffect(() => {
    api.get<Category[]>("/categories").then(setCategories);
  }, []);

  const update = (key: keyof Filters, value: string) => {
    onChange({ ...filters, [key]: value });
  };

  return (
    <div className="flex flex-wrap gap-2 items-end">
      <Input
        placeholder="Search..."
        value={filters.search}
        onChange={(e) => update("search", e.target.value)}
        className="w-48"
      />
      <Select value={filters.status} onValueChange={(v) => update("status", v)}>
        <SelectTrigger className="w-36"><SelectValue placeholder="Status" /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All statuses</SelectItem>
          <SelectItem value="pending">Pending</SelectItem>
          <SelectItem value="processed">Processed</SelectItem>
          <SelectItem value="needs_review">Needs Review</SelectItem>
          <SelectItem value="failed">Failed</SelectItem>
        </SelectContent>
      </Select>
      <Select value={filters.category_id} onValueChange={(v) => update("category_id", v)}>
        <SelectTrigger className="w-36"><SelectValue placeholder="Category" /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All categories</SelectItem>
          {categories.map((c) => (
            <SelectItem key={c.id} value={String(c.id)}>{c.name}</SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Select value={filters.document_type} onValueChange={(v) => update("document_type", v)}>
        <SelectTrigger className="w-40"><SelectValue placeholder="Type" /></SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All types</SelectItem>
          <SelectItem value="expense_receipt">Expense Receipt</SelectItem>
          <SelectItem value="issued_invoice">Issued Invoice</SelectItem>
          <SelectItem value="other_document">Other</SelectItem>
        </SelectContent>
      </Select>
      <Input type="date" value={filters.date_from} onChange={(e) => update("date_from", e.target.value)} className="w-36" />
      <Input type="date" value={filters.date_to} onChange={(e) => update("date_to", e.target.value)} className="w-36" />
      <Button variant="outline" onClick={() => onChange({ search: "", status: "all", category_id: "all", document_type: "all", date_from: "", date_to: "" })}>
        Clear
      </Button>
    </div>
  );
}
```

- [ ] **Step 2: Create DocumentTable component**

Create `frontend/src/components/DocumentTable.tsx`:

```tsx
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";

interface Document {
  id: number;
  receipt_date: string | null;
  vendor_name: string | null;
  total_amount: number | null;
  currency: string | null;
  status: string;
  category_name: string | null;
  original_filename: string;
  submission_date: string;
}

interface Props {
  documents: Document[];
  selected: Set<number>;
  onSelect: (id: number) => void;
  onSelectAll: () => void;
  sortBy: string;
  sortOrder: string;
  onSort: (field: string) => void;
}

export default function DocumentTable({ documents, selected, onSelect, onSelectAll, sortBy, sortOrder, onSort }: Props) {
  const sortIcon = (field: string) => {
    if (sortBy !== field) return "";
    return sortOrder === "asc" ? " \u2191" : " \u2193";
  };

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-8">
            <Checkbox checked={selected.size === documents.length && documents.length > 0} onCheckedChange={onSelectAll} />
          </TableHead>
          <TableHead className="cursor-pointer" onClick={() => onSort("receipt_date")}>Date{sortIcon("receipt_date")}</TableHead>
          <TableHead className="cursor-pointer" onClick={() => onSort("vendor_name")}>Vendor{sortIcon("vendor_name")}</TableHead>
          <TableHead className="cursor-pointer" onClick={() => onSort("total_amount")}>Amount{sortIcon("total_amount")}</TableHead>
          <TableHead>Category</TableHead>
          <TableHead className="cursor-pointer" onClick={() => onSort("status")}>Status{sortIcon("status")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {documents.map((doc) => (
          <TableRow key={doc.id} className="cursor-pointer" onClick={() => window.location.href = `/documents/${doc.id}`}>
            <TableCell onClick={(e) => e.stopPropagation()}>
              <Checkbox checked={selected.has(doc.id)} onCheckedChange={() => onSelect(doc.id)} />
            </TableCell>
            <TableCell>{doc.receipt_date || "-"}</TableCell>
            <TableCell>{doc.vendor_name || doc.original_filename}</TableCell>
            <TableCell className="font-mono">{doc.total_amount != null ? `${doc.total_amount.toFixed(2)} ${doc.currency || ""}` : "-"}</TableCell>
            <TableCell>{doc.category_name || "-"}</TableCell>
            <TableCell>
              <Badge variant={doc.status === "processed" ? "default" : doc.status === "failed" ? "destructive" : "secondary"}>
                {doc.status}
              </Badge>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
```

- [ ] **Step 3: Create DocumentsPage**

Create `frontend/src/pages/DocumentsPage.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import FilterBar from "@/components/FilterBar";
import DocumentTable from "@/components/DocumentTable";

interface Filters {
  search: string;
  status: string;
  category_id: string;
  document_type: string;
  date_from: string;
  date_to: string;
}

const defaultFilters: Filters = { search: "", status: "all", category_id: "all", document_type: "all", date_from: "", date_to: "" };

export default function DocumentsPage() {
  const [filters, setFilters] = useState<Filters>(defaultFilters);
  const [documents, setDocuments] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [sortBy, setSortBy] = useState("submission_date");
  const [sortOrder, setSortOrder] = useState("desc");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const pageSize = 20;

  const fetchDocs = useCallback(() => {
    const params = new URLSearchParams();
    if (filters.search) params.set("search", filters.search);
    if (filters.status !== "all") params.set("status", filters.status);
    if (filters.category_id !== "all") params.set("category_id", filters.category_id);
    if (filters.document_type !== "all") params.set("document_type", filters.document_type);
    if (filters.date_from) params.set("date_from", filters.date_from);
    if (filters.date_to) params.set("date_to", filters.date_to);
    params.set("sort_by", sortBy);
    params.set("sort_order", sortOrder);
    params.set("page", String(page));
    params.set("page_size", String(pageSize));

    api.get<{ documents: any[]; total: number }>(`/documents?${params}`).then((data) => {
      setDocuments(data.documents);
      setTotal(data.total);
    });
  }, [filters, page, sortBy, sortOrder]);

  useEffect(() => { fetchDocs(); }, [fetchDocs]);

  const handleSort = (field: string) => {
    if (sortBy === field) {
      setSortOrder(sortOrder === "asc" ? "desc" : "asc");
    } else {
      setSortBy(field);
      setSortOrder("desc");
    }
  };

  const handleSelect = (id: number) => {
    const next = new Set(selected);
    next.has(id) ? next.delete(id) : next.add(id);
    setSelected(next);
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files) return;
    await api.upload(Array.from(e.target.files));
    fetchDocs();
    e.target.value = "";
  };

  const handleBatchReprocess = async () => {
    if (selected.size === 0) return;
    await api.post("/documents/batch-reprocess", { document_ids: Array.from(selected) });
    setSelected(new Set());
    fetchDocs();
  };

  const totalPages = Math.ceil(total / pageSize);

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold">Documents</h1>
        <div className="flex gap-2">
          <label className="cursor-pointer">
            <Button asChild><span>Upload</span></Button>
            <input type="file" multiple className="hidden" onChange={handleUpload} accept=".pdf,.jpg,.jpeg,.png,.html,.htm" />
          </label>
          {selected.size > 0 && (
            <Button variant="outline" onClick={handleBatchReprocess}>Reprocess ({selected.size})</Button>
          )}
        </div>
      </div>

      <FilterBar filters={filters} onChange={(f) => { setFilters(f); setPage(1); }} />

      <DocumentTable
        documents={documents}
        selected={selected}
        onSelect={handleSelect}
        onSelectAll={() => {
          if (selected.size === documents.length) setSelected(new Set());
          else setSelected(new Set(documents.map((d) => d.id)));
        }}
        sortBy={sortBy}
        sortOrder={sortOrder}
        onSort={handleSort}
      />

      <div className="flex justify-between items-center">
        <span className="text-sm text-muted-foreground">{total} documents</span>
        <div className="flex gap-2">
          <Button variant="outline" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
          <span className="text-sm py-2">Page {page} of {totalPages || 1}</span>
          <Button variant="outline" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>Next</Button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Also add shadcn checkbox component**

```bash
cd /e/Projects/Lev/SmallProjects/Receiptory/frontend
npx shadcn@latest add checkbox
```

- [ ] **Step 5: Wire up in App.tsx**

Replace Documents placeholder:

```tsx
import DocumentsPage from "@/pages/DocumentsPage";
// ...
<Route path="/documents" element={<ProtectedRoute><AppLayout><DocumentsPage /></AppLayout></ProtectedRoute>} />
```

- [ ] **Step 6: Verify build**

```bash
cd /e/Projects/Lev/SmallProjects/Receiptory/frontend && npm run build
```

- [ ] **Step 7: Commit**

```bash
git add frontend/src/
git commit -m "feat: documents page with table, filters, search, upload, bulk actions"
```

---

## Task 22: Frontend — Document Detail Page

**Files:**
- Create: `frontend/src/pages/DocumentDetailPage.tsx`
- Create: `frontend/src/components/PageViewer.tsx`
- Create: `frontend/src/components/MetadataForm.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Create PageViewer component**

Create `frontend/src/components/PageViewer.tsx`:

```tsx
import { useState } from "react";
import { Button } from "@/components/ui/button";

const API_BASE = import.meta.env.DEV ? "http://localhost:8080/api" : "/api";

interface Props {
  docId: number;
  pageCount: number;
}

export default function PageViewer({ docId, pageCount }: Props) {
  const [currentPage, setCurrentPage] = useState(0);

  return (
    <div className="space-y-2">
      <img
        src={`${API_BASE}/documents/${docId}/pages/${currentPage}`}
        alt={`Page ${currentPage + 1}`}
        className="w-full border rounded"
      />
      {pageCount > 1 && (
        <div className="flex justify-center gap-2">
          <Button variant="outline" size="sm" disabled={currentPage === 0} onClick={() => setCurrentPage(currentPage - 1)}>
            Prev
          </Button>
          <span className="text-sm py-1">Page {currentPage + 1} of {pageCount}</span>
          <Button variant="outline" size="sm" disabled={currentPage >= pageCount - 1} onClick={() => setCurrentPage(currentPage + 1)}>
            Next
          </Button>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Create MetadataForm component**

Create `frontend/src/components/MetadataForm.tsx`:

```tsx
import { useState, useEffect } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";

interface Category { id: number; name: string; }

interface Props {
  doc: any;
  onSave: (updates: any) => void;
}

export default function MetadataForm({ doc, onSave }: Props) {
  const [categories, setCategories] = useState<Category[]>([]);
  const [form, setForm] = useState({
    receipt_date: doc.receipt_date || "",
    vendor_name: doc.vendor_name || "",
    vendor_tax_id: doc.vendor_tax_id || "",
    vendor_receipt_id: doc.vendor_receipt_id || "",
    document_title: doc.document_title || "",
    client_name: doc.client_name || "",
    client_tax_id: doc.client_tax_id || "",
    description: doc.description || "",
    total_amount: doc.total_amount ?? "",
    currency: doc.currency || "",
    category_id: doc.category_id ? String(doc.category_id) : "",
    document_type: doc.document_type || "",
    user_notes: doc.user_notes || "",
  });

  useEffect(() => {
    api.get<Category[]>("/categories").then(setCategories);
  }, []);

  const update = (key: string, value: string) => setForm({ ...form, [key]: value });

  const handleSave = () => {
    const updates: any = {};
    for (const [key, val] of Object.entries(form)) {
      if (key === "total_amount" && val !== "") updates[key] = parseFloat(val);
      else if (key === "category_id" && val) updates[key] = parseInt(val);
      else if (val !== "") updates[key] = val;
    }
    onSave(updates);
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Badge variant={doc.status === "processed" ? "default" : doc.status === "failed" ? "destructive" : "secondary"}>
          {doc.status}
        </Badge>
        {doc.extraction_confidence != null && (
          <span className="text-sm text-muted-foreground">Confidence: {(doc.extraction_confidence * 100).toFixed(0)}%</span>
        )}
        {doc.manually_edited && <Badge variant="outline">Edited</Badge>}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div><Label>Date</Label><Input value={form.receipt_date} onChange={(e) => update("receipt_date", e.target.value)} type="date" /></div>
        <div><Label>Vendor</Label><Input value={form.vendor_name} onChange={(e) => update("vendor_name", e.target.value)} /></div>
        <div><Label>Vendor Tax ID</Label><Input value={form.vendor_tax_id} onChange={(e) => update("vendor_tax_id", e.target.value)} /></div>
        <div><Label>Receipt/Invoice #</Label><Input value={form.vendor_receipt_id} onChange={(e) => update("vendor_receipt_id", e.target.value)} /></div>
        <div><Label>Title</Label><Input value={form.document_title} onChange={(e) => update("document_title", e.target.value)} /></div>
        <div><Label>Amount</Label><Input value={form.total_amount} onChange={(e) => update("total_amount", e.target.value)} type="number" step="0.01" /></div>
        <div><Label>Currency</Label><Input value={form.currency} onChange={(e) => update("currency", e.target.value)} /></div>
        <div>
          <Label>Type</Label>
          <Select value={form.document_type} onValueChange={(v) => update("document_type", v)}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="expense_receipt">Expense Receipt</SelectItem>
              <SelectItem value="issued_invoice">Issued Invoice</SelectItem>
              <SelectItem value="other_document">Other</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div><Label>Client</Label><Input value={form.client_name} onChange={(e) => update("client_name", e.target.value)} /></div>
        <div><Label>Client Tax ID</Label><Input value={form.client_tax_id} onChange={(e) => update("client_tax_id", e.target.value)} /></div>
        <div className="col-span-2">
          <Label>Category</Label>
          <Select value={form.category_id} onValueChange={(v) => update("category_id", v)}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              {categories.map((c) => <SelectItem key={c.id} value={String(c.id)}>{c.name}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div><Label>Description</Label><Input value={form.description} onChange={(e) => update("description", e.target.value)} /></div>
      <div><Label>Notes</Label><Textarea value={form.user_notes} onChange={(e) => update("user_notes", e.target.value)} /></div>

      <Button onClick={handleSave}>Save Changes</Button>
    </div>
  );
}
```

- [ ] **Step 3: Create DocumentDetailPage**

Create `frontend/src/pages/DocumentDetailPage.tsx`:

```tsx
import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import PageViewer from "@/components/PageViewer";
import MetadataForm from "@/components/MetadataForm";

export default function DocumentDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [doc, setDoc] = useState<any>(null);

  useEffect(() => {
    if (id) api.get(`/documents/${id}`).then(setDoc);
  }, [id]);

  if (!doc) return <div>Loading...</div>;

  const handleSave = async (updates: any) => {
    const updated = await api.patch(`/documents/${id}`, updates);
    setDoc(updated);
  };

  const handleReprocess = async () => {
    await api.post(`/documents/${id}/reprocess`);
    const updated = await api.get(`/documents/${id}`);
    setDoc(updated);
  };

  const handleDelete = async () => {
    await api.delete(`/documents/${id}`);
    navigate("/documents");
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h1 className="text-xl font-bold">{doc.original_filename}</h1>
        <div className="flex gap-2">
          <Button variant="outline" onClick={handleReprocess}>Reprocess</Button>
          <Button variant="destructive" onClick={handleDelete}>Delete</Button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <PageViewer docId={doc.id} pageCount={doc.page_count || 1} />
        <MetadataForm doc={doc} onSave={handleSave} />
      </div>

      {doc.edit_history && doc.edit_history.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground">Edit History ({doc.edit_history.length} changes)</summary>
          <div className="mt-2 space-y-1">
            {doc.edit_history.map((e: any, i: number) => (
              <div key={i} className="font-mono text-xs">
                {e.timestamp}: {e.field} changed from "{e.old_value}" to "{e.new_value}"
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Wire up in App.tsx**

```tsx
import DocumentDetailPage from "@/pages/DocumentDetailPage";
// ...
<Route path="/documents/:id" element={<ProtectedRoute><AppLayout><DocumentDetailPage /></AppLayout></ProtectedRoute>} />
```

- [ ] **Step 5: Verify build**

```bash
cd /e/Projects/Lev/SmallProjects/Receiptory/frontend && npm run build
```

- [ ] **Step 6: Commit**

```bash
git add frontend/src/
git commit -m "feat: document detail page with page viewer and editable metadata form"
```

---

## Task 23: Frontend — Export Page

**Files:**
- Create: `frontend/src/pages/ExportPage.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Create ExportPage**

Create `frontend/src/pages/ExportPage.tsx`:

```tsx
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";

export default function ExportPage() {
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [month, setMonth] = useState("");
  const [year, setYear] = useState(new Date().getFullYear().toString());
  const [exporting, setExporting] = useState(false);

  const doExport = async (body: any) => {
    setExporting(true);
    try {
      const blob = await api.exportDocs(body);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "receiptory_export.zip";
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Export</h1>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Card>
          <CardHeader><CardTitle>Quick Presets</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            <Button className="w-full" variant="outline" disabled={exporting} onClick={() => doExport({ preset: "since_last_export" })}>
              All Since Last Export
            </Button>
            <div className="flex gap-2">
              <Input type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
              <Button variant="outline" disabled={exporting || !month} onClick={() => doExport({ preset: "month", month })}>
                Export Month
              </Button>
            </div>
            <div className="flex gap-2">
              <Input type="number" value={year} onChange={(e) => setYear(e.target.value)} min="2020" max="2030" />
              <Button variant="outline" disabled={exporting} onClick={() => doExport({ preset: "full_year", year: parseInt(year) })}>
                Export Year
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Custom Date Range</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <div><Label>From</Label><Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} /></div>
            <div><Label>To</Label><Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} /></div>
            <Button disabled={exporting || (!dateFrom && !dateTo)} onClick={() => doExport({ date_from: dateFrom, date_to: dateTo })}>
              Export Range
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Wire up in App.tsx**

```tsx
import ExportPage from "@/pages/ExportPage";
// ...
<Route path="/export" element={<ProtectedRoute><AppLayout><ExportPage /></AppLayout></ProtectedRoute>} />
```

- [ ] **Step 3: Verify build**

```bash
cd /e/Projects/Lev/SmallProjects/Receiptory/frontend && npm run build
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/ExportPage.tsx frontend/src/App.tsx
git commit -m "feat: export page with presets and custom date range"
```

---

## Task 24: Frontend — Settings Page (All Tabs)

**Files:**
- Create: `frontend/src/pages/SettingsPage.tsx`
- Create: `frontend/src/components/CategoryManager.tsx`
- Create: `frontend/src/components/BackupPanel.tsx`
- Create: `frontend/src/components/LogViewer.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Create CategoryManager component**

Create `frontend/src/components/CategoryManager.tsx`:

```tsx
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";

interface Category {
  id: number;
  name: string;
  description: string | null;
  is_system: boolean;
}

export default function CategoryManager() {
  const [categories, setCategories] = useState<Category[]>([]);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editName, setEditName] = useState("");
  const [editDesc, setEditDesc] = useState("");

  const load = () => api.get<Category[]>("/categories").then(setCategories);
  useEffect(() => { load(); }, []);

  const handleCreate = async () => {
    if (!newName) return;
    await api.post("/categories", { name: newName, description: newDesc || null });
    setNewName(""); setNewDesc("");
    load();
  };

  const handleUpdate = async (id: number) => {
    await api.patch(`/categories/${id}`, { name: editName, description: editDesc });
    setEditingId(null);
    load();
  };

  const handleDelete = async (id: number) => {
    await api.delete(`/categories/${id}`);
    load();
  };

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <Input placeholder="Name" value={newName} onChange={(e) => setNewName(e.target.value)} className="w-40" />
        <Input placeholder="Description" value={newDesc} onChange={(e) => setNewDesc(e.target.value)} className="flex-1" />
        <Button onClick={handleCreate}>Add</Button>
      </div>
      <div className="space-y-2">
        {categories.map((c) => (
          <div key={c.id} className="flex items-center gap-2 p-2 border rounded">
            {editingId === c.id ? (
              <>
                <Input value={editName} onChange={(e) => setEditName(e.target.value)} className="w-40" />
                <Input value={editDesc} onChange={(e) => setEditDesc(e.target.value)} className="flex-1" />
                <Button size="sm" onClick={() => handleUpdate(c.id)}>Save</Button>
                <Button size="sm" variant="outline" onClick={() => setEditingId(null)}>Cancel</Button>
              </>
            ) : (
              <>
                <span className="font-medium">{c.name}</span>
                {c.is_system && <Badge variant="secondary">system</Badge>}
                <span className="text-sm text-muted-foreground flex-1">{c.description || ""}</span>
                {!c.is_system && (
                  <>
                    <Button size="sm" variant="outline" onClick={() => { setEditingId(c.id); setEditName(c.name); setEditDesc(c.description || ""); }}>Edit</Button>
                    <Button size="sm" variant="destructive" onClick={() => handleDelete(c.id)}>Delete</Button>
                  </>
                )}
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Create BackupPanel component**

Create `frontend/src/components/BackupPanel.tsx`:

```tsx
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

interface Backup {
  id: number;
  started_at: string;
  completed_at: string | null;
  status: string;
  size_bytes: number | null;
  backup_type: string;
  error: string | null;
}

export default function BackupPanel() {
  const [backups, setBackups] = useState<Backup[]>([]);
  const [triggering, setTriggering] = useState(false);

  const load = () => api.get<Backup[]>("/backup/history").then(setBackups);
  useEffect(() => { load(); }, []);

  const trigger = async () => {
    setTriggering(true);
    try {
      await api.post("/backup/trigger");
      load();
    } finally {
      setTriggering(false);
    }
  };

  return (
    <div className="space-y-4">
      <Button onClick={trigger} disabled={triggering}>{triggering ? "Running..." : "Trigger Backup Now"}</Button>
      <div className="space-y-2">
        {backups.map((b) => (
          <div key={b.id} className="flex items-center gap-2 p-2 border rounded text-sm">
            <span>{b.started_at}</span>
            <Badge variant={b.status === "completed" ? "default" : b.status === "failed" ? "destructive" : "secondary"}>{b.status}</Badge>
            <span>{b.backup_type}</span>
            {b.size_bytes && <span className="text-muted-foreground">{(b.size_bytes / 1024 / 1024).toFixed(1)} MB</span>}
            {b.error && <span className="text-destructive text-xs">{b.error}</span>}
          </div>
        ))}
        {backups.length === 0 && <p className="text-muted-foreground">No backups yet</p>}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Create LogViewer component**

Create `frontend/src/components/LogViewer.tsx`:

```tsx
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Button } from "@/components/ui/button";

export default function LogViewer() {
  const [lines, setLines] = useState<string[]>([]);
  const [level, setLevel] = useState("all");

  const load = () => {
    const params = level !== "all" ? `?level=${level}` : "";
    api.get<{ lines: string[] }>(`/logs${params}`).then((d) => setLines(d.lines));
  };

  useEffect(() => { load(); }, [level]);

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <Select value={level} onValueChange={setLevel}>
          <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All</SelectItem>
            <SelectItem value="ERROR">Error</SelectItem>
            <SelectItem value="WARNING">Warning</SelectItem>
            <SelectItem value="INFO">Info</SelectItem>
          </SelectContent>
        </Select>
        <Button variant="outline" onClick={load}>Refresh</Button>
      </div>
      <pre className="bg-muted p-4 rounded text-xs overflow-auto max-h-96 font-mono">
        {lines.length > 0 ? lines.join("") : "No logs available"}
      </pre>
    </div>
  );
}
```

- [ ] **Step 4: Create SettingsPage**

Create `frontend/src/pages/SettingsPage.tsx`:

```tsx
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import CategoryManager from "@/components/CategoryManager";
import BackupPanel from "@/components/BackupPanel";
import LogViewer from "@/components/LogViewer";

export default function SettingsPage() {
  const [settings, setSettings] = useState<any>({});
  const [costs, setCosts] = useState<any>(null);
  const [testResult, setTestResult] = useState<string | null>(null);

  useEffect(() => {
    api.get("/settings").then(setSettings);
    api.get("/stats/processing-costs").then(setCosts);
  }, []);

  const save = async (updates: Record<string, any>) => {
    await api.patch("/settings", { settings: updates });
    const fresh = await api.get("/settings");
    setSettings(fresh);
  };

  const testLlm = async () => {
    setTestResult("Testing...");
    try {
      const res = await api.post<{ status: string; response: string }>("/settings/test-llm");
      setTestResult(`OK: ${(res as any).response}`);
    } catch (e: any) {
      setTestResult(`Failed: ${e.message}`);
    }
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Settings</h1>
      <Tabs defaultValue="general">
        <TabsList>
          <TabsTrigger value="general">General</TabsTrigger>
          <TabsTrigger value="llm">LLM</TabsTrigger>
          <TabsTrigger value="categories">Categories</TabsTrigger>
          <TabsTrigger value="backup">Backup</TabsTrigger>
          <TabsTrigger value="logs">Logs</TabsTrigger>
        </TabsList>

        <TabsContent value="general" className="space-y-4">
          <Card>
            <CardHeader><CardTitle>Business Information</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <div>
                <Label>Business Names (comma-separated, multi-language)</Label>
                <Input
                  value={Array.isArray(settings.business_names) ? settings.business_names.join(", ") : ""}
                  onBlur={(e) => save({ business_names: e.target.value.split(",").map((s: string) => s.trim()).filter(Boolean) })}
                  onChange={(e) => setSettings({ ...settings, business_names: e.target.value })}
                />
              </div>
              <div>
                <Label>Business Addresses (comma-separated)</Label>
                <Input
                  value={Array.isArray(settings.business_addresses) ? settings.business_addresses.join(", ") : ""}
                  onBlur={(e) => save({ business_addresses: e.target.value.split(",").map((s: string) => s.trim()).filter(Boolean) })}
                  onChange={(e) => setSettings({ ...settings, business_addresses: e.target.value })}
                />
              </div>
              <div>
                <Label>Business Tax IDs (comma-separated)</Label>
                <Input
                  value={Array.isArray(settings.business_tax_ids) ? settings.business_tax_ids.join(", ") : ""}
                  onBlur={(e) => save({ business_tax_ids: e.target.value.split(",").map((s: string) => s.trim()).filter(Boolean) })}
                  onChange={(e) => setSettings({ ...settings, business_tax_ids: e.target.value })}
                />
              </div>
              <div>
                <Label>Reference Currency</Label>
                <Input value={settings.reference_currency || ""} onBlur={(e) => save({ reference_currency: e.target.value })} onChange={(e) => setSettings({ ...settings, reference_currency: e.target.value })} />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Authentication</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <div><Label>Username</Label><Input value={settings.auth_username || ""} onBlur={(e) => save({ auth_username: e.target.value })} onChange={(e) => setSettings({ ...settings, auth_username: e.target.value })} /></div>
              <div><Label>New Password</Label><Input type="password" placeholder="Enter new password" onBlur={(e) => { if (e.target.value) save({ auth_password_hash: e.target.value }); }} /></div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="llm" className="space-y-4">
          <Card>
            <CardHeader><CardTitle>LLM Configuration</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <div><Label>Model</Label><Input value={settings.llm_model || ""} onBlur={(e) => save({ llm_model: e.target.value })} onChange={(e) => setSettings({ ...settings, llm_model: e.target.value })} /></div>
              <div><Label>API Key</Label><Input type="password" value={settings.llm_api_key || ""} onBlur={(e) => { if (e.target.value && !e.target.value.includes("***")) save({ llm_api_key: e.target.value }); }} onChange={(e) => setSettings({ ...settings, llm_api_key: e.target.value })} /></div>
              <div><Label>Sleep Interval (seconds)</Label><Input type="number" step="0.1" value={settings.llm_sleep_interval ?? ""} onBlur={(e) => save({ llm_sleep_interval: parseFloat(e.target.value) })} onChange={(e) => setSettings({ ...settings, llm_sleep_interval: e.target.value })} /></div>
              <div><Label>Confidence Threshold</Label><Input type="number" step="0.05" min="0" max="1" value={settings.confidence_threshold ?? ""} onBlur={(e) => save({ confidence_threshold: parseFloat(e.target.value) })} onChange={(e) => setSettings({ ...settings, confidence_threshold: e.target.value })} /></div>
              <Button onClick={testLlm}>Test LLM Connection</Button>
              {testResult && <p className="text-sm">{testResult}</p>}
            </CardContent>
          </Card>
          {costs && (
            <Card>
              <CardHeader><CardTitle>Processing Costs</CardTitle></CardHeader>
              <CardContent>
                <p>Total cost: ${costs.total_cost_usd?.toFixed(4)}</p>
                <p>Total tokens: {costs.total_tokens_in?.toLocaleString()} in / {costs.total_tokens_out?.toLocaleString()} out</p>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="categories"><CategoryManager /></TabsContent>
        <TabsContent value="backup">
          <Card>
            <CardHeader><CardTitle>Backup Configuration</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <div><Label>Rclone Destination</Label><Input value={settings.backup_destination || ""} onBlur={(e) => save({ backup_destination: e.target.value })} onChange={(e) => setSettings({ ...settings, backup_destination: e.target.value })} /></div>
              <div><Label>Schedule (cron)</Label><Input value={settings.backup_schedule || ""} onBlur={(e) => save({ backup_schedule: e.target.value })} onChange={(e) => setSettings({ ...settings, backup_schedule: e.target.value })} /></div>
            </CardContent>
          </Card>
          <BackupPanel />
        </TabsContent>

        <TabsContent value="logs"><LogViewer /></TabsContent>
      </Tabs>
    </div>
  );
}
```

- [ ] **Step 5: Wire up in App.tsx**

```tsx
import SettingsPage from "@/pages/SettingsPage";
// ...
<Route path="/settings" element={<ProtectedRoute><AppLayout><SettingsPage /></AppLayout></ProtectedRoute>} />
```

- [ ] **Step 6: Verify build**

```bash
cd /e/Projects/Lev/SmallProjects/Receiptory/frontend && npm run build
```

- [ ] **Step 7: Commit**

```bash
git add frontend/src/
git commit -m "feat: settings page with general, LLM, categories, backup, and logs tabs"
```

---

## Task 25: Docker Setup

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`

- [ ] **Step 1: Create Dockerfile**

Create `Dockerfile`:

```dockerfile
# Stage 1: Build frontend
FROM node:20-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python runtime
FROM python:3.12-slim

# Install system dependencies for weasyprint and rclone
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 \
    libffi-dev libcairo2 libglib2.0-0 \
    rclone \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install Python dependencies
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

# Copy backend code
COPY backend/ ./backend/
COPY migrations/ ./migrations/

# Copy built frontend
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# Create data directory
RUN mkdir -p /app/data/storage/originals /app/data/storage/converted \
    /app/data/storage/filed /app/data/storage/page_cache /app/data/logs

EXPOSE 8080

CMD ["uv", "run", "uvicorn", "backend.main:create_app", "--host", "0.0.0.0", "--port", "8080", "--factory"]
```

- [ ] **Step 2: Create docker-compose.yml**

Create `docker-compose.yml`:

```yaml
services:
  receiptory:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - ./data:/app/data
    env_file:
      - .env
    restart: unless-stopped
    environment:
      - RECEIPTORY_DATA_DIR=/app/data
```

- [ ] **Step 3: Verify Docker build**

```bash
cd /e/Projects/Lev/SmallProjects/Receiptory
docker compose build
```
Expected: builds successfully.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile docker-compose.yml
git commit -m "feat: Docker setup with multi-stage build"
```

---

## Task 26: End-to-End Integration Test

**Files:**
- Create: `tests/test_e2e.py`

- [ ] **Step 1: Write E2E test**

Create `tests/test_e2e.py`:

```python
import os
import json
import pytest
import bcrypt
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.config import init_settings, set_setting
from backend.database import get_connection
from backend.processing.extract import ExtractionResult, LLMExtractionResult


MOCK_EXTRACTION = ExtractionResult(
    receipt_date="2026-01-15",
    document_title="Tax Invoice",
    vendor_name="Office Depot",
    vendor_tax_id="515234567",
    vendor_receipt_id="INV-001",
    description="Office supplies",
    line_items=[{"description": "Paper", "quantity": 1, "unit_price": 25.0}],
    subtotal=25.0,
    tax_amount=4.25,
    total_amount=29.25,
    currency="ILS",
    payment_method="credit_card",
    payment_identifier="4580",
    language="he",
    additional_fields=[],
    raw_extracted_text="Office Depot Tax Invoice Paper 25.00",
    document_type="expense_receipt",
    category_name="office_supplies",
    extraction_confidence=0.95,
)

MOCK_LLM_RESULT = LLMExtractionResult(
    extraction=MOCK_EXTRACTION,
    tokens_in=1000,
    tokens_out=500,
    model="gemini/gemini-3-flash-preview",
)


@pytest.fixture
def setup(db_path, tmp_data_dir):
    init_settings()
    pw_hash = bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode()
    set_setting("auth_password_hash", pw_hash)
    set_setting("llm_api_key", "test-key")
    app = create_app(str(tmp_data_dir), run_background=False)
    client = TestClient(app)
    client.post("/api/auth/login", json={"username": "admin", "password": "testpass"})
    return client, str(tmp_data_dir)


@patch("backend.processing.pipeline.extract_document")
def test_full_lifecycle(mock_extract, setup, sample_pdf_path):
    """Upload -> Process -> View -> Edit -> Export full lifecycle."""
    client, data_dir = setup
    mock_extract.return_value = MOCK_LLM_RESULT

    # 1. Upload
    with open(sample_pdf_path, "rb") as f:
        resp = client.post("/api/upload", files={"files": ("receipt.pdf", f, "application/pdf")})
    assert resp.status_code == 200
    doc_id = resp.json()["documents"][0]["id"]

    # 2. Verify pending
    resp = client.get(f"/api/documents/{doc_id}")
    assert resp.json()["status"] == "pending"

    # 3. Process manually (simulating queue)
    from backend.processing.pipeline import process_document
    process_document(doc_id, data_dir)

    # 4. Verify processed
    resp = client.get(f"/api/documents/{doc_id}")
    doc = resp.json()
    assert doc["status"] == "processed"
    assert doc["vendor_name"] == "Office Depot"
    assert doc["total_amount"] == 29.25

    # 5. Edit
    resp = client.patch(f"/api/documents/{doc_id}", json={"vendor_name": "Office Depot Inc."})
    assert resp.json()["vendor_name"] == "Office Depot Inc."
    assert resp.json()["manually_edited"] is True

    # 6. Search
    resp = client.get("/api/documents?search=Office")
    assert resp.json()["total"] >= 1

    # 7. Stats
    resp = client.get("/api/stats/dashboard")
    assert resp.status_code == 200

    # 8. Duplicate upload rejected
    with open(sample_pdf_path, "rb") as f:
        resp = client.post("/api/upload", files={"files": ("receipt.pdf", f, "application/pdf")})
    assert len(resp.json()["duplicates"]) == 1
```

- [ ] **Step 2: Run E2E test**

Run:
```bash
uv run pytest tests/test_e2e.py -v
```
Expected: test passes.

- [ ] **Step 3: Run full test suite**

Run:
```bash
uv run pytest tests/ -v --tb=short
```
Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_e2e.py
git commit -m "feat: end-to-end integration test covering full document lifecycle"
```
