import json
import os
import sqlite3
import pytest
from backend.backup.runner import build_backup, snapshot_database
from backend.backup.scheduler import determine_backup_type
from backend.database import get_connection, get_db_path
from backend.config import init_settings
from datetime import date


def test_determine_backup_type_daily():
    # 2026-03-10 is a Tuesday (not Sunday, not 1st)
    assert determine_backup_type(date(2026, 3, 10)) == "daily"


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

    # The point of a backup is that it restores. Open the copy on its own and
    # read the row back -- the original assertions here only proved a file of
    # some kind existed at that path.
    restored = sqlite3.connect(os.path.join(backup_dir, "receiptory.db"))
    try:
        assert restored.execute(
            "SELECT vendor_name FROM documents"
        ).fetchone()[0] == "Test Vendor"
    finally:
        restored.close()


def _open_writer(db_path):
    """A WAL writer connection plus a second open connection that pins the WAL.

    SQLite checkpoints and deletes the -wal when the LAST connection closes, so
    a single short-lived connection hides this bug entirely. The app is a single
    process running the processing queue and the backup concurrently, which is
    exactly the state reproduced here.
    """
    writer = sqlite3.connect(db_path, timeout=10)
    holder = sqlite3.connect(db_path, timeout=10)
    holder.execute("SELECT 1").fetchone()
    return writer, holder


def test_snapshot_captures_rows_still_sitting_in_the_wal(db_path, tmp_data_dir):
    """Regression: shutil.copy2 of receiptory.db loses committed data.

    In WAL mode a committed transaction lives in receiptory.db-wal until a
    checkpoint. Copying the main file alone restored a database missing every
    recent write -- and, taken early enough, missing the schema too.
    """
    path = get_db_path()
    writer, holder = _open_writer(path)
    try:
        writer.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes,
                                      status, submission_channel, vendor_name)
               VALUES ('wal.pdf', 'wal-hash', 10, 'processed', 'web_upload', 'WAL Vendor')"""
        )
        writer.commit()

        # Precondition: the data really is in the WAL, not the main file. Without
        # this the test could pass against a checkpointed database and prove nothing.
        assert os.path.exists(path + "-wal") and os.path.getsize(path + "-wal") > 0

        dest = str(tmp_data_dir / "snapshot.db")
        snapshot_database(path, dest)
    finally:
        writer.close()
        holder.close()

    restored = sqlite3.connect(dest)
    try:
        assert restored.execute(
            "SELECT vendor_name FROM documents WHERE file_hash = 'wal-hash'"
        ).fetchone()[0] == "WAL Vendor"
    finally:
        restored.close()


def test_snapshot_is_standalone_without_sidecar_files(db_path, tmp_data_dir):
    """The snapshot is fully checkpointed, so no -wal/-shm travels with it.

    rclone uploads the backup directory as-is. A backup that needed sidecar
    files would be restorable only if they were uploaded too, and they are not.
    """
    path = get_db_path()
    writer, holder = _open_writer(path)
    try:
        writer.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes,
                                      status, submission_channel)
               VALUES ('x.pdf', 'side-hash', 10, 'processed', 'web_upload')"""
        )
        writer.commit()
        dest = str(tmp_data_dir / "standalone.db")
        snapshot_database(path, dest)
    finally:
        writer.close()
        holder.close()

    assert not os.path.exists(dest + "-wal")
    assert not os.path.exists(dest + "-shm")

    restored = sqlite3.connect(dest)
    try:
        assert restored.execute(
            "SELECT COUNT(*) FROM documents WHERE file_hash = 'side-hash'"
        ).fetchone()[0] == 1
    finally:
        restored.close()


def test_snapshot_raises_when_the_copy_fails_integrity_check(db_path, tmp_data_dir, monkeypatch):
    """A snapshot that would not restore must fail the backup, not upload quietly."""
    real_connect = sqlite3.connect

    # A real Connection subclass, not a wrapper: backup() rejects anything that
    # is not an actual sqlite3.Connection.
    class _BadIntegrity(sqlite3.Connection):
        def execute(self, sql, *a):
            if "integrity_check" in sql:
                # A real cursor, so the caller's .fetchone() behaves as it does
                # against a genuinely corrupt database.
                return super().execute("SELECT 'malformed database page 3'")
            return super().execute(sql, *a)

    def fake_connect(target, *args, **kwargs):
        if str(target).endswith("bad.db"):
            kwargs["factory"] = _BadIntegrity
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr("backend.backup.runner.sqlite3.connect", fake_connect)

    with pytest.raises(RuntimeError, match="integrity check"):
        snapshot_database(get_db_path(), str(tmp_data_dir / "bad.db"))
