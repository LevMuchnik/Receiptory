import sqlite3
from pathlib import Path
from backend.database import init_db, get_connection, _get_current_version

def test_init_creates_tables(db_conn):
    tables = db_conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    table_names = [r["name"] for r in tables]
    assert "schema_version" in table_names
    assert "settings" in table_names
    assert "categories" in table_names
    assert "documents" in table_names
    assert "backups" in table_names

def test_wal_mode(db_conn):
    mode = db_conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"

def test_migration_version(db_conn):
    # Derive from the migrations dir so this assert can't go stale when a
    # migration is added (it sat at 5 while 006 and 007 landed).
    migration_count = len(list((Path(__file__).parent.parent / "migrations").glob("*.sql")))
    version = _get_current_version(db_conn)
    assert version == migration_count

def test_system_categories_seeded(db_conn):
    rows = db_conn.execute("SELECT name FROM categories WHERE is_system = 1 ORDER BY name").fetchall()
    names = [r["name"] for r in rows]
    assert "failed" in names
    assert "uncategorized" in names
    assert "pending" in names
    assert "unauthorized_sender" in names

def test_user_categories_seeded(db_conn):
    rows = db_conn.execute("SELECT name FROM categories WHERE is_system = 0 ORDER BY name").fetchall()
    names = [r["name"] for r in rows]
    assert "Office & Supplies" in names
    assert "Travel" in names
    assert "Utilities" in names
    assert "Other" in names
    # Issued categories
    assert "Tax Invoice" in names
    assert "Credit Note" in names

def test_idempotent_migration(tmp_data_dir):
    path = str(tmp_data_dir / "receiptory.db")
    init_db(path)
    init_db(path)
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) as c FROM categories").fetchone()["c"]
        assert count == 38  # 4 system + 27 expense + 7 issued


def _run_migration_008(db):
    sql = (Path(__file__).parent.parent / "migrations" / "008_backfill_llm_temperature.sql").read_text(encoding="utf-8")
    db.executescript(sql)
    db.commit()


def test_migration_008_backfills_stale_temperature(tmp_path):
    # Issue #11: the code default change is a no-op for existing installs (db > default),
    # so this migration is the load-bearing part of the fix. It must rewrite the stale
    # seeded default (0.0, and the int encoding 0) to 1.0 while leaving any custom value alone.
    db = sqlite3.connect(str(tmp_path / "settings.db"))
    db.executescript(
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT, "
        "updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')));"
    )
    db.execute("INSERT INTO settings (key, value) VALUES ('llm_temperature', '0.0')")
    db.commit()

    _run_migration_008(db)
    assert db.execute("SELECT value FROM settings WHERE key='llm_temperature'").fetchone()[0] == "1.0"

    # Int-encoded default (json.dumps(0)) also backfilled.
    db.execute("UPDATE settings SET value='0' WHERE key='llm_temperature'")
    db.commit()
    _run_migration_008(db)
    assert db.execute("SELECT value FROM settings WHERE key='llm_temperature'").fetchone()[0] == "1.0"

    # A custom (non-zero) temperature is preserved.
    db.execute("UPDATE settings SET value='0.5' WHERE key='llm_temperature'")
    db.commit()
    _run_migration_008(db)
    assert db.execute("SELECT value FROM settings WHERE key='llm_temperature'").fetchone()[0] == "0.5"

    # Idempotent: re-running after backfill leaves 1.0 untouched.
    db.execute("UPDATE settings SET value='1.0' WHERE key='llm_temperature'")
    db.commit()
    _run_migration_008(db)
    assert db.execute("SELECT value FROM settings WHERE key='llm_temperature'").fetchone()[0] == "1.0"
    db.close()


def test_migration_008_applies_through_runner_on_upgrade(tmp_path):
    # Faithful upgrade path: an existing install sitting at schema_version 7 with a
    # stale llm_temperature=0.0 row. The real runner must discover 008, parse its
    # version, gate on schema_version, apply it exactly once, and rewrite 0.0 -> 1.0.
    from backend.database import _run_migrations
    db = sqlite3.connect(str(tmp_path / "upgrade.db"))
    db.row_factory = sqlite3.Row
    db.executescript(
        "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, "
        "applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')));"
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
        "updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')));"
    )
    for v in range(1, 8):  # simulate migrations 001..007 already applied
        db.execute("INSERT INTO schema_version (version) VALUES (?)", (v,))
    db.execute("INSERT INTO settings (key, value) VALUES ('llm_temperature', '0.0')")
    db.commit()

    _run_migrations(db)  # discovers 008 (version 8 > current 7), applies once

    assert db.execute("SELECT value FROM settings WHERE key='llm_temperature'").fetchone()["value"] == "1.0"
    assert db.execute("SELECT COUNT(*) AS c FROM schema_version WHERE version=8").fetchone()["c"] == 1
    db.close()
