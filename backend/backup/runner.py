import json
import os
import shutil
import sqlite3
import stat
import logging
import tempfile
import time
from datetime import datetime, timezone

from backend.database import get_connection
from backend.config import get_all_settings, SENSITIVE_KEYS

# Settings rows stripped from the snapshot before it leaves the machine. The
# backup is uploaded to cloud storage by rclone with no encryption of its own,
# and settings.json beside it is already masked -- without this the database
# next to it handed over the same secrets in plaintext, defeating that masking.
# llm_api_keys is not in SENSITIVE_KEYS because it is DB-only and bypasses
# get_all_settings entirely, so it was never masked anywhere.
SNAPSHOT_REDACTED_KEYS = SENSITIVE_KEYS | {"llm_api_keys"}

logger = logging.getLogger(__name__)

# Wall-clock ceiling on one snapshot. Connection.backup() retries SQLITE_BUSY
# by itself (its own `sleep` argument) and does NOT honour the connection's
# busy timeout -- measured: a source opened with timeout=0 still waited 8.3s on
# a locked database, and waits forever if the lock is never released. Backups
# run in an executor thread, so an unbounded wait would hang there silently with
# the backups row stuck at 'running' and no failure notification ever sent. The
# progress callback below turns that hang into an error the scheduler can report.
SNAPSHOT_TIMEOUT_S = 120

# Pages per backup step. Only a multi-step backup calls the progress callback
# often enough to enforce the deadline. Large enough that a database this size
# finishes in one or two steps, since SQLite restarts the copy if the source is
# written between steps.
SNAPSHOT_PAGE_STEP = 1024


def build_backup(data_dir: str) -> str:
    """Assemble backup contents into a temporary directory. Returns path."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = os.path.join(tempfile.gettempdir(), f"receiptory_backup_{timestamp}")
    os.makedirs(backup_dir, exist_ok=True)

    # Snapshot SQLite database
    db_path = os.path.join(data_dir, "receiptory.db")
    snapshot_path = os.path.join(backup_dir, "receiptory.db")
    if os.path.exists(db_path):
        snapshot_database(db_path, snapshot_path)

    # Copy storage files
    storage_dir = os.path.join(data_dir, "storage")
    if os.path.exists(storage_dir):
        shutil.copytree(storage_dir, os.path.join(backup_dir, "storage"), dirs_exist_ok=True)

    # Copy logs
    logs_dir = os.path.join(data_dir, "logs")
    if os.path.exists(logs_dir):
        shutil.copytree(logs_dir, os.path.join(backup_dir, "logs"), dirs_exist_ok=True)

    # Export JSONL metadata from the snapshot, not the live database. The
    # snapshot is a pinned instant; the live database keeps moving while the
    # backup assembles (build_backup runs in an executor while uploads and the
    # processing queue carry on). Reading the live database here would put two
    # sources of truth in one backup directory that disagree about the same
    # documents, with nothing to tell a restorer which one is right.
    _export_jsonl(
        os.path.join(backup_dir, "metadata.jsonl"),
        snapshot_path if os.path.exists(snapshot_path) else None,
    )

    # Export settings (with sensitive values masked)
    from backend.config import get_all_settings_masked
    settings = get_all_settings_masked()
    with open(os.path.join(backup_dir, "settings.json"), "w") as f:
        json.dump(settings, f, indent=2, default=str)

    logger.info(f"Backup assembled at {backup_dir}")
    return backup_dir


def _schema_version(conn: sqlite3.Connection) -> int | None:
    """MAX(schema_version.version), or None if the table is not there at all."""
    try:
        return conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    except sqlite3.Error:
        return None


def _redact_secrets(conn: sqlite3.Connection) -> None:
    """Delete the secret-bearing settings rows from a snapshot, for real.

    The VACUUM is the load-bearing half, not a tidy-up. DELETE alone leaves the
    old bytes readable in freed pages, and PRAGMA secure_delete is not enough
    either: it only zeroes what this function deletes, while the snapshot also
    inherits the source's free pages. Measured -- a secret the owner rotated
    weeks ago is still in the source file, is copied into the snapshot by the
    backup API, and survives secure_delete. VACUUM rewrites the file from live
    content only, so that residue does not leave the machine.
    """
    keys = sorted(SNAPSHOT_REDACTED_KEYS)
    conn.execute(
        f"DELETE FROM settings WHERE key IN ({','.join('?' * len(keys))})", keys
    )
    conn.commit()
    conn.isolation_level = None  # VACUUM cannot run inside a transaction
    conn.execute("VACUUM")


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
    run loudly instead of being uploaded and discovered at restore time. The
    unusable file is removed first: it sits inside a directory that otherwise
    looks like a complete backup, and nothing downstream would tell an operator
    the receiptory.db in it is the one that failed.

    The secret-bearing settings rows (SNAPSHOT_REDACTED_KEYS) are stripped from
    the copy, so RESTORING FROM A BACKUP REQUIRES RE-ENTERING the LLM API keys,
    the Telegram token, the Gmail app password, the cloud OAuth credentials and
    the login password. settings.json in the same directory lists those keys with
    masked values, so a restorer can see what needs filling back in.
    """
    deadline = time.monotonic() + SNAPSHOT_TIMEOUT_S

    def _abort_if_stuck(status, remaining, total):
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"Backup snapshot gave up after {SNAPSHOT_TIMEOUT_S}s waiting for "
                f"the database lock ({remaining} of {total} pages left)"
            )

    source = sqlite3.connect(db_path, timeout=SNAPSHOT_TIMEOUT_S)
    try:
        dest = sqlite3.connect(dest_path)
        try:
            source.backup(dest, pages=SNAPSHOT_PAGE_STEP, progress=_abort_if_stuck)
            result = dest.execute("PRAGMA integrity_check").fetchone()[0]
            expected_version = _schema_version(source)
            actual_version = _schema_version(dest)
            if result == "ok" and actual_version == expected_version:
                _redact_secrets(dest)
        finally:
            dest.close()
    finally:
        source.close()

    if result != "ok":
        os.unlink(dest_path)
        raise RuntimeError(f"Backup snapshot failed integrity check: {result}")

    # integrity_check alone is not enough: it reports "ok" for an empty,
    # schema-less database, which is exactly the artifact the old file copy
    # produced. Structural soundness says the pages are not corrupt; it says
    # nothing about whether the tables arrived.
    if actual_version != expected_version:
        os.unlink(dest_path)
        raise RuntimeError(
            f"Backup snapshot is not a faithful copy: schema_version is "
            f"{actual_version}, source is {expected_version}"
        )

    # sqlite3.connect creates the destination at 0644 & ~umask. shutil.copy2 used
    # to carry the source's mode across, and this file holds everything the
    # database holds, so keep that property rather than quietly widening it.
    os.chmod(dest_path, stat.S_IMODE(os.stat(db_path).st_mode))


_METADATA_QUERY = """SELECT d.*, c.name as category_name, c.section as category_section
                     FROM documents d
                     LEFT JOIN categories c ON d.category_id = c.id"""


def _export_jsonl(output_path: str, source_db: str | None = None) -> None:
    """Export all document metadata as JSONL.

    Reads `source_db` when given (the snapshot, so the export matches the
    database shipped beside it), falling back to the live connection when there
    is no snapshot to read.
    """
    if source_db:
        conn = sqlite3.connect(source_db)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(_METADATA_QUERY).fetchall()
        finally:
            conn.close()
    else:
        with get_connection() as conn:
            rows = conn.execute(_METADATA_QUERY).fetchall()

    with open(output_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(dict(row), default=str, ensure_ascii=False) + "\n")
