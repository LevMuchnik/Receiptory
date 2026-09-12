import json
import os
import shutil
import sqlite3
import stat
import threading
import time
import pytest
from backend.backup.runner import (
    build_backup,
    snapshot_database,
    SNAPSHOT_TIMEOUT_S,
)
from backend.backup.scheduler import determine_backup_type
from backend.database import get_connection
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


def _open_writer(path):
    """A WAL writer connection plus a second open connection that pins the WAL.

    SQLite checkpoints and deletes the -wal when the LAST connection closes, so
    a single short-lived connection hides this bug entirely. The app is a single
    process running the processing queue and the backup concurrently, which is
    exactly the state reproduced here.
    """
    writer = sqlite3.connect(path, timeout=10)
    holder = sqlite3.connect(path, timeout=10)
    holder.execute("SELECT 1").fetchone()
    return writer, holder


def _insert_doc(conn, file_hash, vendor="Vendor"):
    conn.execute(
        """INSERT INTO documents (original_filename, file_hash, file_size_bytes,
                                  status, submission_channel, vendor_name)
           VALUES ('x.pdf', ?, 10, 'processed', 'web_upload', ?)""",
        (file_hash, vendor),
    )
    conn.commit()


def _scalar(path, sql):
    """Open a database file standalone and read one value out of it."""
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def test_build_backup_snapshots_rows_still_sitting_in_the_wal(db_path, tmp_data_dir):
    """The call site, not just the helper.

    The three tests below call snapshot_database() directly, so they all pass
    even if build_backup goes back to shutil.copy2 -- runner.py:24 is the only
    production line this change touches, and without this test it is an
    uncovered mutant. test_build_backup_creates_archive cannot cover it either:
    its get_connection() calls close, which checkpoints and deletes the -wal, so
    a plain file copy looks correct there.
    """
    init_settings()
    writer, holder = _open_writer(db_path)
    try:
        _insert_doc(writer, "build-hash", "Build Vendor")
        assert os.path.getsize(db_path + "-wal") > 0
        backup_dir = build_backup(str(tmp_data_dir))
    finally:
        writer.close()
        holder.close()

    copied = os.path.join(backup_dir, "receiptory.db")
    assert not os.path.exists(copied + "-wal")
    assert _scalar(
        copied, "SELECT vendor_name FROM documents WHERE file_hash = 'build-hash'"
    ) == "Build Vendor"


def test_snapshot_captures_rows_still_sitting_in_the_wal(db_path, tmp_data_dir):
    """Regression: shutil.copy2 of receiptory.db loses committed data.

    In WAL mode a committed transaction lives in receiptory.db-wal until a
    checkpoint. Copying the main file alone restored a database missing every
    recent write -- and, taken early enough, missing the schema too.
    """
    writer, holder = _open_writer(db_path)
    try:
        _insert_doc(writer, "wal-hash", "WAL Vendor")

        # Precondition: the data really is in the WAL, not the main file. Without
        # this the test could pass against a checkpointed database and prove nothing.
        assert os.path.exists(db_path + "-wal") and os.path.getsize(db_path + "-wal") > 0

        dest = str(tmp_data_dir / "snapshot.db")
        snapshot_database(db_path, dest)
    finally:
        writer.close()
        holder.close()

    assert _scalar(
        dest, "SELECT vendor_name FROM documents WHERE file_hash = 'wal-hash'"
    ) == "WAL Vendor"


def test_snapshot_is_standalone_without_sidecar_files(db_path, tmp_data_dir):
    """The snapshot is fully checkpointed, so no -wal/-shm travels with it.

    rclone uploads the backup directory as-is. A backup that needed sidecar
    files would be restorable only if they were uploaded too, and they are not.
    """
    writer, holder = _open_writer(db_path)
    try:
        _insert_doc(writer, "side-hash")
        dest = str(tmp_data_dir / "standalone.db")
        snapshot_database(db_path, dest)
    finally:
        writer.close()
        holder.close()

    assert not os.path.exists(dest + "-wal")
    assert not os.path.exists(dest + "-shm")
    assert _scalar(
        dest, "SELECT COUNT(*) FROM documents WHERE file_hash = 'side-hash'"
    ) == 1


def test_snapshot_keeps_the_source_permission_bits(db_path, tmp_data_dir):
    """The snapshot holds everything the database holds, including secrets.

    shutil.copy2 carried the source mode across; sqlite3.connect creates the
    destination at 0644 & ~umask, so without the explicit chmod a 0600 database
    would be published as world-readable inside the backup.
    """
    os.chmod(db_path, 0o600)
    dest = str(tmp_data_dir / "perms.db")
    snapshot_database(db_path, dest)
    assert stat.S_IMODE(os.stat(dest).st_mode) == 0o600


def test_snapshot_strips_secrets_and_leaves_no_residue(db_path, tmp_data_dir):
    """The backup is uploaded to cloud storage with no encryption of its own.

    settings.json beside it is masked, so the database must not hand the same
    secrets over in plaintext. Asserts on the raw file bytes, not just the rows:
    a plain DELETE leaves the old values readable in freed pages.
    """
    init_settings()
    token = "telegram-token-SHOULD-NOT-APPEAR"
    api_key = "sk-live-SHOULD-NOT-APPEAR"
    with get_connection() as conn:
        for key, value in (
            ("telegram_bot_token", json.dumps(token)),
            ("llm_api_keys", json.dumps([{"label": "main", "key": api_key}])),
            ("page_render_dpi", json.dumps(200)),
        ):
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
            )

    dest = str(tmp_data_dir / "redacted.db")
    snapshot_database(db_path, dest)

    assert _scalar(
        dest,
        "SELECT COUNT(*) FROM settings WHERE key IN ('telegram_bot_token', 'llm_api_keys')",
    ) == 0
    # A non-secret setting is untouched, so this is redaction and not a wipe.
    assert _scalar(dest, "SELECT COUNT(*) FROM settings WHERE key = 'page_render_dpi'") == 1

    with open(dest, "rb") as f:
        raw = f.read()
    assert token.encode() not in raw, "secret still present in the snapshot's bytes"
    assert api_key.encode() not in raw, "secret still present in the snapshot's bytes"

    # The live database keeps its secrets; only the copy is stripped.
    with get_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM settings WHERE key = 'telegram_bot_token'"
        ).fetchone()[0] == 1


def test_snapshot_drops_residue_from_a_rotated_secret(db_path, tmp_data_dir):
    """A secret the owner rotated is still in the source file's free pages.

    The backup API copies free pages verbatim, so the old value rides into the
    snapshot even though no row references it, and deleting the current row does
    not touch it. Only the VACUUM in _redact_secrets rewrites the file from live
    content and leaves it behind. Measured: without the VACUUM this fails.
    """
    init_settings()
    rotated = "sk-OLD-ROTATED-KEY-SHOULD-NOT-LEAVE-THE-MACHINE" * 4
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('llm_api_keys', ?)",
            (json.dumps([{"label": "old", "key": rotated}]),),
        )
    with get_connection() as conn:  # the owner rotates the key away
        conn.execute("DELETE FROM settings WHERE key = 'llm_api_keys'")
    with get_connection() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    with open(db_path, "rb") as f:
        assert rotated.encode() in f.read(), "fixture did not leave residue to scrub"

    dest = str(tmp_data_dir / "rotated.db")
    snapshot_database(db_path, dest)

    with open(dest, "rb") as f:
        assert rotated.encode() not in f.read(), "rotated secret leaked into the backup"


def _run_snapshot_in_thread(db_path, dest):
    result = {}

    def run():
        started = time.monotonic()
        try:
            snapshot_database(db_path, dest)
            result["ok"] = True
        except Exception as e:  # noqa: BLE001 - recorded and re-asserted by callers
            result["error"] = e
        result["elapsed"] = time.monotonic() - started

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, result


def test_snapshot_waits_out_a_blocking_lock(db_path, tmp_data_dir):
    """A transient lock must not fail the nightly backup.

    A plain concurrent writer proves nothing: WAL readers do not block on
    writers, so a snapshot sails past a BEGIN EXCLUSIVE without waiting. Only
    locking_mode=EXCLUSIVE takes the file-level lock that shuts readers out.
    """
    blocker = sqlite3.connect(db_path, timeout=10)
    blocker.execute("PRAGMA locking_mode=EXCLUSIVE")
    _insert_doc(blocker, "lock-hash", "Lock Vendor")

    dest = str(tmp_data_dir / "contended.db")
    t, result = _run_snapshot_in_thread(db_path, dest)
    time.sleep(3)
    assert t.is_alive(), "snapshot did not block on the exclusive lock"
    blocker.close()  # only closing releases an EXCLUSIVE locking_mode lock
    t.join(timeout=30)

    assert not t.is_alive()
    assert result.get("ok") is True, f"snapshot failed under contention: {result.get('error')}"
    assert _scalar(
        dest, "SELECT COUNT(*) FROM documents WHERE file_hash = 'lock-hash'"
    ) == 1


def test_snapshot_gives_up_on_a_lock_that_is_never_released(db_path, tmp_data_dir, monkeypatch):
    """Anchors SNAPSHOT_TIMEOUT_S, and guards against a silent hang.

    Connection.backup() retries SQLITE_BUSY itself and ignores the connection's
    busy timeout -- measured: a source opened with timeout=0 still waited on a
    locked database, and waits forever if the lock never lifts. Backups run in
    an executor thread, so without the deadline the thread hangs, the backups
    row stays 'running', and no failure notification is ever sent.
    """
    monkeypatch.setattr("backend.backup.runner.SNAPSHOT_TIMEOUT_S", 2)

    blocker = sqlite3.connect(db_path, timeout=10)
    blocker.execute("PRAGMA locking_mode=EXCLUSIVE")
    _insert_doc(blocker, "stuck-hash", "Stuck Vendor")
    try:
        t, result = _run_snapshot_in_thread(db_path, str(tmp_data_dir / "stuck.db"))
        t.join(timeout=30)
        assert not t.is_alive(), "snapshot hung instead of giving up"
        assert isinstance(result.get("error"), TimeoutError), result
        assert "gave up after 2s" in str(result["error"])
    finally:
        blocker.close()


def test_snapshot_rejects_a_structurally_sound_but_empty_copy(db_path, tmp_data_dir, monkeypatch):
    """integrity_check reports "ok" for a schema-less database.

    Verified directly: a freshly created sqlite file with zero rows in
    sqlite_master passes integrity_check. That is precisely the artifact the old
    file copy produced, so the structural check alone cannot detect the bug this
    change exists to prevent -- hence the schema_version comparison.
    """
    class _NoopBackup(sqlite3.Connection):
        def backup(self, target, *a, **kw):
            return None  # leaves the destination empty but structurally valid

    real_connect = sqlite3.connect

    def fake_connect(target, *args, **kwargs):
        if str(target) == db_path:
            kwargs["factory"] = _NoopBackup
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", fake_connect)

    dest = str(tmp_data_dir / "empty.db")
    with pytest.raises(RuntimeError, match="not a faithful copy"):
        snapshot_database(db_path, dest)
    assert not os.path.exists(dest)


def test_metadata_jsonl_is_exported_from_the_snapshot_not_the_live_db(
    db_path, tmp_data_dir, monkeypatch
):
    """Both files in the backup must describe the same instant.

    The snapshot pins a moment; the live database keeps moving while the backup
    assembles. A row written during the storage copy (simulated here) must not
    appear in metadata.jsonl, or the directory ships two sources of truth that
    disagree and nothing says which is right.
    """
    init_settings()
    os.makedirs(str(tmp_data_dir / "storage"), exist_ok=True)
    with open(str(tmp_data_dir / "storage" / "f.txt"), "w") as f:
        f.write("x")

    writer, holder = _open_writer(db_path)
    real_copytree = shutil.copytree

    written = []

    def copytree_then_write(*args, **kwargs):
        # Runs after the snapshot, before the JSONL export. build_backup calls
        # copytree once for storage and once for logs; write on the first only.
        if not written:
            written.append(True)
            _insert_doc(writer, "during-hash", "During Vendor")
        return real_copytree(*args, **kwargs)

    # Patches the stdlib symbol for the test; undone at teardown.
    monkeypatch.setattr(shutil, "copytree", copytree_then_write)

    try:
        _insert_doc(writer, "before-hash", "Before Vendor")
        backup_dir = build_backup(str(tmp_data_dir))
    finally:
        writer.close()
        holder.close()

    with open(os.path.join(backup_dir, "metadata.jsonl")) as f:
        hashes = {json.loads(line)["file_hash"] for line in f if line.strip()}

    assert "before-hash" in hashes
    assert "during-hash" not in hashes, "metadata.jsonl read the live DB, not the snapshot"
    assert _scalar(
        os.path.join(backup_dir, "receiptory.db"),
        "SELECT COUNT(*) FROM documents WHERE file_hash = 'during-hash'",
    ) == 0


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

    # runner imports the sqlite3 module, so this patches the stdlib symbol
    # process-wide for the test. fake_connect delegates for every other path and
    # monkeypatch undoes it at teardown.
    monkeypatch.setattr(sqlite3, "connect", fake_connect)

    dest = str(tmp_data_dir / "bad.db")
    # Match the interpolated detail, not just the prefix: that text is the only
    # thing telling an operator why the backup failed.
    with pytest.raises(RuntimeError, match=r"integrity check: malformed database page 3"):
        snapshot_database(db_path, dest)

    # The unusable file must not be left sitting in the backup directory.
    assert not os.path.exists(dest)
