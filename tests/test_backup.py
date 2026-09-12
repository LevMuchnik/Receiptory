import asyncio
import contextlib
import json
import os
import shutil
import sqlite3
import subprocess
import stat
import threading
import time
import pytest
from backend.backup.runner import (
    build_backup,
    snapshot_database,
    SNAPSHOT_TIMEOUT_S,
)
from backend.backup.scheduler import (
    determine_backup_type,
    run_backup,
    run_backup_scheduler,
    upload_status,
    reset_stuck_backups,
)
from backend.database import get_connection
from backend.config import init_settings, set_setting
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


# --- run_backup upload reporting (issue #43) -------------------------------
#
# Before this, every upload exception was caught per destination, logged, and
# then fall-through marked the row 'completed' and fired backup_ok. With
# notify_*_backup_ok off by default, a backup that reached no cloud at all was
# not just green in the UI -- it was completely silent.


def _backup_row(backup_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT status, error, size_bytes, local_path FROM backups WHERE id = ?",
            (backup_id,),
        ).fetchone()
    return dict(row)


@pytest.fixture
def backup_env(db_path, tmp_data_dir, monkeypatch):
    """Stub build_backup and capture notifications; no real files, no rclone."""
    init_settings()
    sent = []

    fake_dir = str(tmp_data_dir / "assembled")
    os.makedirs(fake_dir, exist_ok=True)
    with open(os.path.join(fake_dir, "receiptory.db"), "wb") as f:
        f.write(b"x" * 2048)

    monkeypatch.setattr("backend.backup.scheduler.build_backup", lambda d: fake_dir)
    monkeypatch.setattr(
        "backend.notifications.notifier.notify",
        lambda event, payload: sent.append((event, payload)),
    )
    return sent, fake_dir


def test_upload_status_maps_destination_counts():
    assert upload_status(0, 0) == "completed"   # nothing configured, local by choice
    assert upload_status(2, 2) == "completed"
    assert upload_status(2, 1) == "partial"
    assert upload_status(2, 0) == "failed"
    assert upload_status(1, 0) == "failed"


async def test_backup_that_reached_no_destination_is_not_reported_as_success(
    backup_env, tmp_data_dir, monkeypatch
):
    sent, _ = backup_env
    set_setting("backup_destination", "gdrive:receipts")

    def boom(*a, **kw):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("backend.backup.scheduler.upload_backup", boom)

    backup_id = await run_backup(str(tmp_data_dir), trigger="scheduled")
    row = _backup_row(backup_id)

    assert row["status"] == "failed"
    assert "connection refused" in row["error"]
    assert [e for e, _ in sent] == ["backup_failed"], sent
    assert "gdrive:receipts" in sent[0][1]["error"]
    assert "no destination accepted the upload" in sent[0][1]["error"]


async def test_partial_upload_is_neither_completed_nor_silent(
    backup_env, tmp_data_dir, monkeypatch
):
    sent, _ = backup_env
    set_setting("backup_destination", "gdrive:receipts, onedrive:receipts")

    def upload(backup_dir, dest, backup_type, backup_date):
        if dest.startswith("onedrive"):
            raise RuntimeError("quota exceeded")

    monkeypatch.setattr("backend.backup.scheduler.upload_backup", upload)
    monkeypatch.setattr("backend.backup.scheduler.apply_retention", lambda *a, **kw: None)

    backup_id = await run_backup(str(tmp_data_dir), trigger="scheduled")
    row = _backup_row(backup_id)

    assert row["status"] == "partial"
    assert "onedrive:receipts: quota exceeded" in row["error"]
    assert "gdrive" not in row["error"]  # the one that worked is not blamed
    # backup_ok is off by default, so a partial routed there would say nothing.
    assert [e for e, _ in sent] == ["backup_failed"], sent
    assert "only 1 of 2" in sent[0][1]["error"]


async def test_all_uploads_succeeding_still_reports_completed(
    backup_env, tmp_data_dir, monkeypatch
):
    sent, fake_dir = backup_env
    set_setting("backup_destination", "gdrive:receipts")
    monkeypatch.setattr("backend.backup.scheduler.upload_backup", lambda *a, **kw: None)
    monkeypatch.setattr("backend.backup.scheduler.apply_retention", lambda *a, **kw: None)

    backup_id = await run_backup(str(tmp_data_dir), trigger="scheduled")
    row = _backup_row(backup_id)

    assert row["status"] == "completed"
    assert row["error"] is None
    assert row["local_path"] == fake_dir
    assert row["size_bytes"] > 0
    assert [e for e, _ in sent] == ["backup_ok"], sent


async def test_no_destination_configured_is_a_completed_local_backup(
    backup_env, tmp_data_dir
):
    sent, _ = backup_env
    set_setting("backup_destination", "")

    backup_id = await run_backup(str(tmp_data_dir), trigger="scheduled")
    row = _backup_row(backup_id)

    assert row["status"] == "completed"
    assert row["error"] is None
    assert [e for e, _ in sent] == ["backup_ok"], sent


async def test_retention_failure_does_not_demote_a_successful_upload(
    backup_env, tmp_data_dir, monkeypatch
):
    """The copy is off-site, which is what matters. Stale remote copies are
    untidy, not a data risk -- but they are still reported."""
    sent, _ = backup_env
    set_setting("backup_destination", "gdrive:receipts")
    monkeypatch.setattr("backend.backup.scheduler.upload_backup", lambda *a, **kw: None)

    def purge_fails(*a, **kw):
        raise RuntimeError("rclone purge failed for 2026-01-01-daily")

    monkeypatch.setattr("backend.backup.scheduler.apply_retention", purge_fails)

    backup_id = await run_backup(str(tmp_data_dir), trigger="scheduled")
    row = _backup_row(backup_id)

    assert row["status"] == "completed"
    assert "retention" in row["error"]
    # backup_ok is off by default and its template never renders an error, so a
    # warning routed there would reach the owner nowhere.
    assert [e for e, _ in sent] == ["backup_failed"], sent
    assert "uploaded successfully" in sent[0][1]["error"]


async def test_retention_never_runs_for_a_destination_that_failed_to_upload(
    backup_env, tmp_data_dir, monkeypatch
):
    """Retention purges old backups by date. Running it after a failed upload
    would delete good history and replace it with nothing."""
    sent, _ = backup_env
    set_setting("backup_destination", "gdrive:receipts")
    retention_calls = []

    monkeypatch.setattr(
        "backend.backup.scheduler.upload_backup",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("network down")),
    )
    monkeypatch.setattr(
        "backend.backup.scheduler.apply_retention",
        lambda *a, **kw: retention_calls.append(a),
    )

    await run_backup(str(tmp_data_dir), trigger="scheduled")
    assert retention_calls == []


async def test_a_failure_building_the_backup_still_reports_failed(
    backup_env, tmp_data_dir, monkeypatch
):
    sent, _ = backup_env
    monkeypatch.setattr(
        "backend.backup.scheduler.build_backup",
        lambda d: (_ for _ in ()).throw(RuntimeError("snapshot integrity check failed")),
    )

    backup_id = await run_backup(str(tmp_data_dir), trigger="scheduled")
    row = _backup_row(backup_id)

    assert row["status"] == "failed"
    assert "integrity check" in row["error"]
    assert [e for e, _ in sent] == ["backup_failed"], sent


# --- rclone purge return code (issue #43) ----------------------------------


def _fake_run(listing, purge_rc, purge_stderr="", calls=None, lsf_rc=0):
    """Stand in for subprocess.run over the rclone calls apply_retention makes.

    Honours text=True instead of ignoring kwargs, and returns a real
    CompletedProcess. A fake that swallows kwargs lets text=True be deleted from
    the production call with every test still green, while the error quietly
    becomes b'...' in the database and in the failure alert.
    """
    def run(cmd, **kwargs):
        if calls is not None:
            calls.append(cmd)
        as_text = kwargs.get("text") is True
        if cmd[1] == "lsf":
            return subprocess.CompletedProcess(
                cmd, lsf_rc,
                stdout=listing if as_text else listing.encode(),
                stderr="" if as_text else b"",
            )
        return subprocess.CompletedProcess(
            cmd, purge_rc,
            stdout="" if as_text else b"",
            stderr=purge_stderr if as_text else purge_stderr.encode(),
        )

    return run


def test_retention_raises_when_rclone_purge_fails(db_path, monkeypatch):
    """A purge that silently fails leaves expired backups on the remote forever
    while the log line above it claims they were deleted."""
    init_settings()
    monkeypatch.setattr(
        "backend.backup.rclone.subprocess.run",
        _fake_run("2020-01-01-daily/\n", purge_rc=1, purge_stderr="directory not found"),
    )
    from backend.backup.rclone import apply_retention

    with pytest.raises(RuntimeError, match="2020-01-01-daily"):
        apply_retention("gdrive:receipts", "/tmp")


def test_retention_purges_only_what_is_past_its_policy(db_path, monkeypatch):
    """Quarterly is never auto-deleted, and a recent daily is left alone."""
    init_settings()
    calls = []
    listing = "\n".join([
        "2020-01-01-daily/",       # long expired
        "2020-01-01-quarterly/",   # never auto-deleted
        f"{date.today().isoformat()}-daily/",  # today, keep
        "not-a-backup-dir/",       # unparseable, skip
    ])
    monkeypatch.setattr(
        "backend.backup.rclone.subprocess.run",
        _fake_run(listing, purge_rc=0, calls=calls),
    )
    from backend.backup.rclone import apply_retention

    apply_retention("gdrive:receipts", "/tmp")

    purged = [c[2] for c in calls if c[1] == "purge"]
    assert purged == ["gdrive:receipts/2020-01-01-daily"]


# --- gaps found by the /review of this PR ------------------------------------


async def test_every_failed_destination_is_named(backup_env, tmp_data_dir, monkeypatch):
    """With one error per test, "; ".join(errors) and errors[-1] are
    indistinguishable -- a mutant reporting only the last failure survives."""
    sent, _ = backup_env
    set_setting("backup_destination", "gdrive:receipts, onedrive:receipts")

    def upload(backup_dir, dest, backup_type, backup_date):
        raise RuntimeError("quota exceeded" if dest.startswith("onedrive") else "connection refused")

    monkeypatch.setattr("backend.backup.scheduler.upload_backup", upload)

    backup_id = await run_backup(str(tmp_data_dir), trigger="scheduled")
    row = _backup_row(backup_id)

    assert row["status"] == "failed"
    assert "gdrive:receipts: connection refused" in row["error"]
    assert "onedrive:receipts: quota exceeded" in row["error"]


async def test_a_cancelled_run_does_not_stay_running(backup_env, tmp_data_dir, monkeypatch):
    """CancelledError is a BaseException, so `except Exception` never sees it.
    Without its own handler the row sits at 'running' forever after a shutdown
    or a client disconnect, and the panel renders that as in-progress.

    Cancels the real task: raising CancelledError inside the executor thread
    does NOT exercise the handler.
    """
    release = threading.Event()

    def blocking_build(_data_dir):
        release.wait(timeout=10)  # hold the await open so there is something to cancel
        return str(tmp_data_dir / "assembled")

    monkeypatch.setattr("backend.backup.scheduler.build_backup", blocking_build)

    task = asyncio.create_task(run_backup(str(tmp_data_dir), trigger="scheduled"))
    try:
        for _ in range(200):  # wait until the row exists and the await is live
            await asyncio.sleep(0.02)
            with get_connection() as conn:
                if conn.execute("SELECT COUNT(*) FROM backups").fetchone()[0]:
                    break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()

    with get_connection() as conn:
        row = dict(conn.execute(
            "SELECT status, error, completed_at FROM backups ORDER BY id DESC LIMIT 1"
        ).fetchone())
    assert row["status"] == "failed"
    assert row["completed_at"] is not None
    assert "cancelled" in row["error"]


async def test_the_scheduler_sweeps_interrupted_runs_on_start(db_path, tmp_data_dir):
    """Through run_backup_scheduler, not by calling the helper: the unit test
    below passes even with the call removed, leaving the sweep wired to nothing."""
    init_settings()
    set_setting("backup_destination", "")  # no destination: the loop just sleeps
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO backups (backup_type, destination, status) VALUES ('daily', 'x:y', 'running')"
        )

    task = asyncio.create_task(run_backup_scheduler(str(tmp_data_dir)))
    await asyncio.sleep(0.15)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    with get_connection() as conn:
        status = conn.execute("SELECT status FROM backups ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert status == "failed", "the scheduler did not sweep the interrupted run"


def test_interrupted_runs_are_swept(db_path):
    """A process killed mid-backup leaves 'running'. Nothing else resolves it."""
    init_settings()
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO backups (backup_type, destination, status) VALUES ('daily', 'x:y', 'running')"
        )
        conn.execute(
            "INSERT INTO backups (backup_type, destination, status) VALUES ('daily', 'x:y', 'completed')"
        )

    assert reset_stuck_backups() == 1

    with get_connection() as conn:
        rows = [dict(r) for r in conn.execute("SELECT status, error FROM backups ORDER BY id")]
    assert rows[0]["status"] == "failed"
    assert "interrupted" in rows[0]["error"]
    assert rows[1]["status"] == "completed"  # a finished run is left alone


async def test_a_retention_failure_reaches_a_channel_that_is_on(
    backup_env, tmp_data_dir, monkeypatch
):
    """backup_ok is off by default and format_backup_ok never renders an error,
    so a retention failure routed there would be invisible to the owner."""
    sent, _ = backup_env
    set_setting("backup_destination", "gdrive:receipts")
    monkeypatch.setattr("backend.backup.scheduler.upload_backup", lambda *a, **kw: None)
    monkeypatch.setattr(
        "backend.backup.scheduler.apply_retention",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("purge denied")),
    )

    backup_id = await run_backup(str(tmp_data_dir), trigger="scheduled")

    assert _backup_row(backup_id)["status"] == "completed"
    assert [e for e, _ in sent] == ["backup_failed"], sent
    assert "uploaded successfully" in sent[0][1]["error"]
    assert "purge denied" in sent[0][1]["error"]


async def test_a_destination_set_to_nothing_usable_is_a_failure(backup_env, tmp_data_dir):
    """" , " is truthy, so the scheduler's `if not destination` gate lets it run.
    The owner believes a remote is configured; 'completed' under the
    local-by-choice rule would be the old lie in a new place."""
    sent, _ = backup_env
    set_setting("backup_destination", " , ")

    backup_id = await run_backup(str(tmp_data_dir), trigger="scheduled")
    row = _backup_row(backup_id)

    assert row["status"] == "failed"
    assert "no usable remote" in row["error"]
    assert [e for e, _ in sent] == ["backup_failed"], sent


async def test_credentials_in_a_destination_never_reach_the_row_or_the_alert(
    backup_env, tmp_data_dir, monkeypatch
):
    """rclone accepts inline connection strings carrying live secrets, and the
    destination is interpolated into both the row and the notification."""
    sent, _ = backup_env
    secret = "wJalrXUtnFEMI_SUPER_SECRET_KEY"
    set_setting("backup_destination", f":s3,access_key_id=AKIA,secret_access_key={secret}:bucket")
    monkeypatch.setattr(
        "backend.backup.scheduler.upload_backup",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("access denied")),
    )

    backup_id = await run_backup(str(tmp_data_dir), trigger="scheduled")
    row = _backup_row(backup_id)

    assert secret not in row["error"]
    assert "[redacted]" in row["error"]
    assert secret not in sent[0][1]["error"]


async def test_recorded_errors_are_capped(backup_env, tmp_data_dir, monkeypatch):
    """rclone stderr can run to kilobytes; the text is persisted, returned for
    50 rows at a time, and sent to Telegram, which rejects over 4096 chars."""
    sent, _ = backup_env
    set_setting("backup_destination", "gdrive:receipts")
    monkeypatch.setattr(
        "backend.backup.scheduler.upload_backup",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("x" * 20000)),
    )

    backup_id = await run_backup(str(tmp_data_dir), trigger="scheduled")
    assert len(_backup_row(backup_id)["error"]) < 1000


def test_failure_alerts_are_on_by_default_and_success_alerts_are_not(db_path):
    """run_backup routes degraded runs to backup_failed precisely because
    backup_ok is off. If these defaults flip, that routing is wrong and every
    other test here still passes."""
    init_settings()
    from backend.config import get_setting

    assert get_setting("notify_telegram_backup_failed") is True
    assert get_setting("notify_email_backup_failed") is True
    assert get_setting("notify_telegram_backup_ok") is False
    assert get_setting("notify_email_backup_ok") is False


def test_retention_error_is_text_not_bytes(db_path, monkeypatch):
    """Dropping text=True leaves stderr as bytes, and the error reads b'...' in
    the database and in the alert."""
    init_settings()
    monkeypatch.setattr(
        "backend.backup.rclone.subprocess.run",
        _fake_run("2020-01-01-daily/\n", purge_rc=1, purge_stderr="directory not found"),
    )
    from backend.backup.rclone import apply_retention

    with pytest.raises(RuntimeError) as exc:
        apply_retention("gdrive:receipts", "/tmp")
    assert "b'" not in str(exc.value)
    assert "directory not found" in str(exc.value)


def test_one_unpurgeable_backup_does_not_abandon_the_rest(db_path, monkeypatch):
    """Raising on the first failure abandoned every later expired directory, so
    one permanently stuck folder froze retention for the whole remote."""
    init_settings()
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1] == "lsf":
            return subprocess.CompletedProcess(
                cmd, 0, stdout="2020-01-01-daily/\n2020-01-02-daily/\n", stderr="")
        if cmd[2].endswith("2020-01-01-daily"):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="locked")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("backend.backup.rclone.subprocess.run", run)
    from backend.backup.rclone import apply_retention

    with pytest.raises(RuntimeError, match="locked"):
        apply_retention("gdrive:receipts", "/tmp")

    assert [c[2] for c in calls if c[1] == "purge"] == [
        "gdrive:receipts/2020-01-01-daily",
        "gdrive:receipts/2020-01-02-daily",
    ]


def test_retention_raises_when_the_listing_fails(db_path, monkeypatch):
    """A silent return meant retention never ran while the backup was still
    recorded as fully healthy -- the same silence one line below it."""
    init_settings()
    monkeypatch.setattr(
        "backend.backup.rclone.subprocess.run",
        _fake_run("", purge_rc=0, lsf_rc=1),
    )
    from backend.backup.rclone import apply_retention

    with pytest.raises(RuntimeError, match="lsf failed"):
        apply_retention("gdrive:receipts", "/tmp")


def test_weekly_and_monthly_retention_windows(db_path, monkeypatch):
    """The weekly (*7) and monthly (*30) branches were uncovered, so a swapped
    multiplier would silently purge history early."""
    init_settings()
    calls = []
    today = date.today()
    old = today.replace(year=today.year - 1)
    listing = "\n".join([
        f"{old}-weekly/",
        f"{old}-monthly/",
        f"{today.isoformat()}-weekly/",
        f"{today.isoformat()}-monthly/",
    ])
    monkeypatch.setattr(
        "backend.backup.rclone.subprocess.run",
        _fake_run(listing, purge_rc=0, calls=calls),
    )
    from backend.backup.rclone import apply_retention

    apply_retention("gdrive:receipts", "/tmp")

    purged = sorted(c[2].rsplit("/", 1)[1] for c in calls if c[1] == "purge")
    assert purged == sorted([f"{old}-monthly", f"{old}-weekly"])


# --- backup notification rendering -------------------------------------------


def test_backup_failure_alert_survives_rclone_stderr_with_markup():
    """Both sinks parse HTML (Telegram parse_mode="HTML", the email body is a
    text/html part) and send_telegram_notification only LOGS a send failure, so
    bad markup drops the alert silently."""
    from backend.notifications.templates import format_backup_failed

    stderr = 'Failed to purge: <nil> pointer & "quota" >100% for a<b'
    out = format_backup_failed({"error": stderr})

    for field in ("caption", "html"):
        assert "<nil>" not in out[field], f"raw markup reached {field}"
        assert "&lt;nil&gt;" in out[field]
        assert "&amp;" in out[field]
    assert out["caption"].startswith("❌ <b>Backup Failed</b>")
    assert "<p><b>Error:</b>" in out["html"]


def test_backup_failure_alert_is_capped_below_the_telegram_limit():
    from backend.notifications.templates import format_backup_failed

    out = format_backup_failed({"error": "x" * 9000})
    assert len(out["caption"]) < 4096
    assert "[truncated]" in out["caption"]


def test_backup_success_alert_escapes_the_destination():
    """The rclone remote string is owner-configured and reaches the same sinks."""
    from backend.notifications.templates import format_backup_ok

    out = format_backup_ok({
        "backup_type": "daily",
        "size_bytes": 1048576,
        "destination": "gdrive:a&b<c>",
    })
    for field in ("caption", "html"):
        assert "a&b<c>" not in out[field]
        assert "a&amp;b&lt;c&gt;" in out[field]
