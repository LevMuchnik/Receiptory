import pytest
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
        # A real install at version 7 has documents, and the runner applies EVERY
        # later migration, not just 008 — 009 adds a column to this table. Without
        # it the fixture is a database no upgrade path ever produces, and the test
        # fails on whichever migration lands next rather than on its own subject.
        "CREATE TABLE documents (id INTEGER PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending');"
    )
    for v in range(1, 8):  # simulate migrations 001..007 already applied
        db.execute("INSERT INTO schema_version (version) VALUES (?)", (v,))
    db.execute("INSERT INTO settings (key, value) VALUES ('llm_temperature', '0.0')")
    db.commit()

    _run_migrations(db)  # discovers 008 (version 8 > current 7), applies once

    assert db.execute("SELECT value FROM settings WHERE key='llm_temperature'").fetchone()["value"] == "1.0"
    assert db.execute("SELECT COUNT(*) AS c FROM schema_version WHERE version=8").fetchone()["c"] == 1
    # 009 rides along on the same upgrade. Pin its effect rather than only
    # shaping the fixture so it does not explode: a no-op 009 would otherwise
    # pass every assertion here.
    assert db.execute("SELECT COUNT(*) AS c FROM schema_version WHERE version=9").fetchone()["c"] == 1
    assert "review_reason" in [r[1] for r in db.execute("PRAGMA table_info(documents)")]
    db.close()



def test_a_failed_migration_applies_nothing(tmp_path, monkeypatch):
    """Review #72: a crash or error mid-migration must not leave half the DDL
    applied without its version row, or the next start fails on
    'duplicate column name' for good."""
    import backend.database as database
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "001_base.sql").write_text("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT);\nCREATE TABLE t (a INTEGER);\n")
    (mig / "002_two_steps.sql").write_text("ALTER TABLE t ADD COLUMN b INTEGER;\nALTER TABLE missing ADD COLUMN c INTEGER;\n")
    monkeypatch.setattr(database, "MIGRATIONS_DIR", mig)
    db = sqlite3.connect(str(tmp_path / "m.db"))
    with pytest.raises(sqlite3.OperationalError):
        database._run_migrations(db)
    assert [r[1] for r in db.execute("PRAGMA table_info(t)")] == ["a"]
    assert [r[0] for r in db.execute("SELECT version FROM schema_version")] == [1]


@pytest.mark.parametrize("prefix", [
    "-- no pragma needed here\n",
    # Generated by /ship coverage audit (#72), extended row.
    # Value: protects=a trigger's BEGIN ... END (as in 001/003) keeps a migration in the one-transaction wrapper;
    # fails_when=_UNWRAPPED_RE is loosened to match any line-leading BEGIN, so trigger migrations half-apply;
    # why_new=the comment row only covers a comment; every real trigger migration succeeds either way;
    # seam=none; no_claim=synthetic migrations in tmp_path, not the shipped files
    "CREATE TRIGGER tr AFTER INSERT ON t\nBEGIN\n  SELECT 1;\nEND;\n",
])
def test_a_comment_naming_pragma_keeps_a_migration_atomic(tmp_path, monkeypatch, prefix):
    """Re-review F10: only a PRAGMA statement runs a script unwrapped."""
    import backend.database as database
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "001_base.sql").write_text("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT);\nCREATE TABLE t (a INTEGER);\n")
    (mig / "002_two_steps.sql").write_text(prefix + "ALTER TABLE t ADD COLUMN b INTEGER;\nALTER TABLE missing ADD COLUMN c INTEGER;\n")
    monkeypatch.setattr(database, "MIGRATIONS_DIR", mig)
    db = sqlite3.connect(str(tmp_path / "m.db"))
    with pytest.raises(sqlite3.OperationalError):
        database._run_migrations(db)
    assert [r[1] for r in db.execute("PRAGMA table_info(t)")] == ["a"]
    assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger'").fetchone()[0] == 0


def test_a_pragma_migration_runs_unwrapped_so_foreign_keys_off_takes_effect(tmp_path, monkeypatch):
    """Generated by /ship coverage audit (#72).

    Value: protects=a migration with a PRAGMA statement runs unwrapped, so foreign_keys = OFF works for a table rebuild (005);
    fails_when=the PRAGMA arm of _UNWRAPPED_RE or the unwrapped branch is dropped: DROP TABLE of a referenced parent fails;
    why_new=005 runs in every test, but on an empty database, where a wrapped rebuild also succeeds;
    seam=none; no_claim=a synthetic rebuild in tmp_path stands in for 005 on a populated pre-005 install
    """
    import backend.database as database
    mig = tmp_path / "migrations"
    mig.mkdir()
    (mig / "001_base.sql").write_text(
        "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT);\n"
        "CREATE TABLE parent (id INTEGER PRIMARY KEY, name TEXT);\n"
        "CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id));\n"
        "INSERT INTO parent VALUES (1, 'p');\nINSERT INTO child VALUES (1, 1);\n")
    (mig / "002_rebuild.sql").write_text(
        "PRAGMA foreign_keys = OFF;\n"
        "CREATE TABLE parent_new (id INTEGER PRIMARY KEY, name TEXT, extra TEXT);\n"
        "INSERT INTO parent_new (id, name) SELECT id, name FROM parent;\n"
        "DROP TABLE parent;\nALTER TABLE parent_new RENAME TO parent;\n"
        "PRAGMA foreign_keys = ON;\n")
    monkeypatch.setattr(database, "MIGRATIONS_DIR", mig)
    db = sqlite3.connect(str(tmp_path / "m.db"))
    db.execute("PRAGMA foreign_keys=ON")  # as init_db opens the migration connection
    database._run_migrations(db)
    assert [r[1] for r in db.execute("PRAGMA table_info(parent)")] == ["id", "name", "extra"]
    assert db.execute("SELECT parent_id FROM child").fetchall() == [(1,)]
    assert [r[0] for r in db.execute("SELECT version FROM schema_version ORDER BY version")] == [1, 2]


def test_migration_010_upgrades_a_schema_9_install_in_one_transaction(tmp_path):
    """#72: a live install sits at schema 9. 010 adds the rate cache, the
    fetched-window log and the two conversion columns, and runs wrapped (no
    PRAGMA statement), so a failure part-way leaves schema 9 intact."""
    import backend.database as database
    sql = (Path(__file__).resolve().parents[1] / "migrations/010_currency_conversion.sql").read_text(encoding="utf-8")
    assert database._UNWRAPPED_RE.search(sql) is None
    db = sqlite3.connect(str(tmp_path / "upgrade.db"))
    db.row_factory = sqlite3.Row
    db.executescript(
        "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT);"
        "CREATE TABLE documents (id INTEGER PRIMARY KEY, conversion_rate REAL, converted_amount REAL);"
    )
    for v in range(1, 10):
        db.execute("INSERT INTO schema_version (version) VALUES (?)", (v,))
    db.commit()
    database._run_migrations(db)
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"exchange_rates", "exchange_rate_fetches"} <= tables
    columns = [r[1] for r in db.execute("PRAGMA table_info(documents)")]
    assert {"conversion_rate_date", "conversion_ref"} <= set(columns)
    # once, with later migrations riding along on the same upgrade
    assert [r[0] for r in db.execute("SELECT version FROM schema_version WHERE version = 10")] == [10]
    db.close()



def test_a_conversion_write_does_not_reindex_the_document(db_conn):
    """Ship review: the FTS update trigger rewrote the whole search row (the
    full extracted text) on every rate write. It fires only when an indexed
    column is written, and search still follows those."""
    import backend.database as database
    sql = (Path(__file__).resolve().parents[1] / "migrations/011_fts_update_trigger_on_indexed_columns.sql").read_text(encoding="utf-8")
    # the trigger's BEGIN ... END is not a transaction: 011 runs wrapped, atomically
    assert database._UNWRAPPED_RE.search(sql) is None
    db_conn.execute(
        "INSERT INTO documents (original_filename, file_hash, file_size_bytes, status, submission_channel, vendor_name, raw_extracted_text) "
        "VALUES ('a.pdf', 'h', 1, 'processed', 'web_upload', 'Acme', 'invoice text')")
    db_conn.commit()
    before = db_conn.total_changes
    db_conn.execute("UPDATE documents SET conversion_rate = 3.0, converted_amount = 30.0, conversion_ref = 'ILS' WHERE file_hash = 'h'")
    assert db_conn.total_changes - before == 1  # the row itself, no FTS writes
    db_conn.execute("UPDATE documents SET vendor_name = 'Globex' WHERE file_hash = 'h'")
    db_conn.commit()
    hits = db_conn.execute("SELECT rowid FROM documents_fts WHERE documents_fts MATCH 'Globex'").fetchall()
    assert len(hits) == 1
    assert db_conn.execute("SELECT rowid FROM documents_fts WHERE documents_fts MATCH 'Acme'").fetchall() == []
    # Generated by /ship coverage audit (#72), extended assertion.
    # Value: protects=011's trigger fires for every column 003 indexes, so an edit of any of them stays searchable;
    # fails_when=a column (e.g. original_filename, missing from CLAUDE.md's FTS list) is left out of AFTER UPDATE OF;
    # why_new=the lines above write vendor_name only; seam=none; no_claim=the shipped migrations on a fresh DB, not an upgrade
    for column in ("raw_extracted_text", "description", "document_title", "original_filename"):
        token = f"zq{column.replace('_', '')}"
        db_conn.execute(f"UPDATE documents SET {column} = ? WHERE file_hash = 'h'", (token,))
        db_conn.commit()
        assert len(db_conn.execute("SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?", (token,)).fetchall()) == 1, column
