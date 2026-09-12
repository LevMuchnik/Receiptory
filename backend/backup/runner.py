import json
import os
import shutil
import sqlite3
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

    # Snapshot SQLite database
    db_path = os.path.join(data_dir, "receiptory.db")
    if os.path.exists(db_path):
        snapshot_database(db_path, os.path.join(backup_dir, "receiptory.db"))

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

    # Export settings (with sensitive values masked)
    from backend.config import get_all_settings_masked
    settings = get_all_settings_masked()
    with open(os.path.join(backup_dir, "settings.json"), "w") as f:
        json.dump(settings, f, indent=2, default=str)

    logger.info(f"Backup assembled at {backup_dir}")
    return backup_dir


def snapshot_database(db_path: str, dest_path: str) -> None:
    """Copy the database as a consistent, restorable snapshot.

    A plain file copy is wrong here, and silently so. The database runs in WAL
    mode (see init_db), where a committed transaction lives in receiptory.db-wal
    until something checkpoints it. Copying only receiptory.db therefore yields a
    backup missing everything since the last checkpoint -- and since the schema
    itself arrives through the same WAL, a copy taken early enough restores to a
    database with no tables at all. Copying the -wal and -shm alongside it is not
    the fix either: the three files are only coherent together at an instant the
    copy cannot pin, so a busy moment gives a torn set.

    The online backup API takes a read lock and writes a fully checkpointed
    standalone database that needs no sidecar files, which is also why the
    backup directory contains no -wal or -shm.

    Raises if the snapshot fails its integrity check, so a bad backup fails the
    run loudly instead of being uploaded and discovered at restore time.
    """
    source = sqlite3.connect(db_path, timeout=30)
    try:
        dest = sqlite3.connect(dest_path)
        try:
            source.backup(dest)
            result = dest.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            dest.close()
    finally:
        source.close()

    if result != "ok":
        raise RuntimeError(f"Backup snapshot failed integrity check: {result}")


def _export_jsonl(output_path: str) -> None:
    """Export all document metadata as JSONL."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT d.*, c.name as category_name, c.section as category_section
               FROM documents d
               LEFT JOIN categories c ON d.category_id = c.id"""
        ).fetchall()

    with open(output_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(dict(row), default=str, ensure_ascii=False) + "\n")
