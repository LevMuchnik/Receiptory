import sqlite3
import os
import logging
import threading
from pathlib import Path
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_db_path: str | None = None
_lock = threading.Lock()

MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


def init_db(db_path: str) -> None:
    global _db_path
    with _lock:
        _db_path = db_path
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    _run_migrations(conn)
    conn.close()


def get_db_path() -> str | None:
    """Get the current database path (thread-safe)."""
    with _lock:
        return _db_path


@contextmanager
def get_connection():
    path = get_db_path()
    if path is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _get_current_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT MAX(version) as v FROM schema_version").fetchone()
        return row["v"] if row["v"] is not None else -1
    except sqlite3.OperationalError:
        return -1


def _run_migrations(conn: sqlite3.Connection) -> None:
    current = _get_current_version(conn)
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    for path in migration_files:
        version = int(path.name.split("_")[0])
        if version <= current:
            continue
        logger.info(f"Applying migration {path.name}")
        sql = path.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        conn.commit()
        logger.info(f"Migration {path.name} applied successfully")
