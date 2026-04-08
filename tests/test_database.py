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
    version = _get_current_version(db_conn)
    assert version == 5

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
