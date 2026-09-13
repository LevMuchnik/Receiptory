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

    # A document row plus the files on disk it points at. Rows without files
    # are rejected by verification now, correctly: such a backup cannot restore.
    with get_connection() as conn:
        _insert_doc(conn, "archive", "Test Vendor", data_dir=data_dir)

    backup_dir, _ = build_backup(data_dir)
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


def _insert_doc(conn, label, vendor="Vendor", data_dir=None):
    """Insert a document row. With data_dir, also write the files it points at.

    The hash is derived from the content rather than being the label, because
    verify_backup checks that originals/<hash> really hashes to <hash> -- a row
    naming a file that cannot exist is not a fixture, it is a backup that would
    not restore.
    """
    import hashlib
    body = f"%PDF-1.4 {label}".encode()
    file_hash = hashlib.sha256(body).hexdigest()
    stored = f"2026-01-01-{label}.pdf"

    if data_dir:
        originals = os.path.join(data_dir, "storage", "originals")
        filed_dir = os.path.join(data_dir, "storage", "filed")
        os.makedirs(originals, exist_ok=True)
        os.makedirs(filed_dir, exist_ok=True)
        with open(os.path.join(originals, f"{file_hash}.pdf"), "wb") as f:
            f.write(body)
        with open(os.path.join(filed_dir, stored), "wb") as f:
            f.write(body)

    conn.execute(
        """INSERT INTO documents (original_filename, file_hash, file_size_bytes,
                                  status, submission_channel, vendor_name, stored_filename)
           VALUES ('x.pdf', ?, ?, 'processed', 'web_upload', ?, ?)""",
        (file_hash, len(body), vendor, stored if data_dir else None),
    )
    conn.commit()
    return file_hash


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
        build_hash = _insert_doc(writer, "build", "Build Vendor", data_dir=str(tmp_data_dir))
        assert os.path.getsize(db_path + "-wal") > 0
        backup_dir, _ = build_backup(str(tmp_data_dir))
    finally:
        writer.close()
        holder.close()

    copied = os.path.join(backup_dir, "receiptory.db")
    assert not os.path.exists(copied + "-wal")
    assert _scalar(
        copied, f"SELECT vendor_name FROM documents WHERE file_hash = '{build_hash}'"
    ) == "Build Vendor"


def test_snapshot_captures_rows_still_sitting_in_the_wal(db_path, tmp_data_dir):
    """Regression: shutil.copy2 of receiptory.db loses committed data.

    In WAL mode a committed transaction lives in receiptory.db-wal until a
    checkpoint. Copying the main file alone restored a database missing every
    recent write -- and, taken early enough, missing the schema too.
    """
    writer, holder = _open_writer(db_path)
    try:
        wal_hash = _insert_doc(writer, "wal", "WAL Vendor")

        # Precondition: the data really is in the WAL, not the main file. Without
        # this the test could pass against a checkpointed database and prove nothing.
        assert os.path.exists(db_path + "-wal") and os.path.getsize(db_path + "-wal") > 0

        dest = str(tmp_data_dir / "snapshot.db")
        snapshot_database(db_path, dest)
    finally:
        writer.close()
        holder.close()

    assert _scalar(
        dest, f"SELECT vendor_name FROM documents WHERE file_hash = '{wal_hash}'"
    ) == "WAL Vendor"


def test_snapshot_is_standalone_without_sidecar_files(db_path, tmp_data_dir):
    """The snapshot is fully checkpointed, so no -wal/-shm travels with it.

    rclone uploads the backup directory as-is. A backup that needed sidecar
    files would be restorable only if they were uploaded too, and they are not.
    """
    writer, holder = _open_writer(db_path)
    try:
        side_hash = _insert_doc(writer, "side")
        dest = str(tmp_data_dir / "standalone.db")
        snapshot_database(db_path, dest)
    finally:
        writer.close()
        holder.close()

    assert not os.path.exists(dest + "-wal")
    assert not os.path.exists(dest + "-shm")
    assert _scalar(
        dest, f"SELECT COUNT(*) FROM documents WHERE file_hash = '{side_hash}'"
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
    lock_hash = _insert_doc(blocker, "lock", "Lock Vendor")

    dest = str(tmp_data_dir / "contended.db")
    t, result = _run_snapshot_in_thread(db_path, dest)
    time.sleep(3)
    assert t.is_alive(), "snapshot did not block on the exclusive lock"
    blocker.close()  # only closing releases an EXCLUSIVE locking_mode lock
    t.join(timeout=30)

    assert not t.is_alive()
    assert result.get("ok") is True, f"snapshot failed under contention: {result.get('error')}"
    assert _scalar(
        dest, f"SELECT COUNT(*) FROM documents WHERE file_hash = '{lock_hash}'"
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
    _insert_doc(blocker, "stuck", "Stuck Vendor")
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
    during = []

    def copytree_then_write(*args, **kwargs):
        # Runs after the snapshot, before the JSONL export. build_backup calls
        # copytree once per entry in BACKUP_TREES; write on the first only,
        # which is the storage tree (BACKUP_TREES keeps it first for exactly
        # this reason).
        if not written:
            written.append(True)
            during.append(_insert_doc(writer, "during", "During Vendor", data_dir=str(tmp_data_dir)))
        return real_copytree(*args, **kwargs)

    # Patches the stdlib symbol for the test; undone at teardown.
    monkeypatch.setattr(shutil, "copytree", copytree_then_write)

    try:
        before_hash = _insert_doc(writer, "before", "Before Vendor", data_dir=str(tmp_data_dir))
        backup_dir, _ = build_backup(str(tmp_data_dir))
    finally:
        writer.close()
        holder.close()

    with open(os.path.join(backup_dir, "metadata.jsonl")) as f:
        hashes = {json.loads(line)["file_hash"] for line in f if line.strip()}

    assert before_hash in hashes
    assert during[0] not in hashes, "metadata.jsonl read the live DB, not the snapshot"
    assert _scalar(
        os.path.join(backup_dir, "receiptory.db"),
        f"SELECT COUNT(*) FROM documents WHERE file_hash = '{during[0]}'",
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

    _clean = {"documents": 0, "originals_verified": 0, "filed_verified": 0,
              "schema_version": 9, "problems": []}
    monkeypatch.setattr("backend.backup.scheduler.build_backup", lambda d: (fake_dir, _clean))
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

    # The destination column sits beside error and /backup/history returns it
    # with SELECT *, so redacting only the error text would prove nothing.
    with get_connection() as conn:
        stored = conn.execute(
            "SELECT destination FROM backups WHERE id = ?", (backup_id,)
        ).fetchone()[0]
    assert secret not in stored
    assert "[redacted]" in stored


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


# --- restore (issue #44) -----------------------------------------------------
#
# Nothing ever tried to restore a backup, which is why a database that restored
# to zero tables shipped green for months. These go through the real
# build_backup -> restore round trip.


def _seed_document(data_dir, file_hash, body=b"%PDF-1.4 fake", vendor="Vendor"):
    """A document row plus the files on disk it points at, as ingestion does."""
    originals = os.path.join(data_dir, "storage", "originals")
    filed_dir = os.path.join(data_dir, "storage", "filed")
    os.makedirs(originals, exist_ok=True)
    os.makedirs(filed_dir, exist_ok=True)

    import hashlib
    real_hash = hashlib.sha256(body).hexdigest() if file_hash is None else file_hash
    with open(os.path.join(originals, f"{real_hash}.pdf"), "wb") as f:
        f.write(body)
    stored = f"2026-01-01-{real_hash[:8]}.pdf"
    with open(os.path.join(filed_dir, stored), "wb") as f:
        f.write(body)

    with get_connection() as conn:
        conn.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes,
                                      status, submission_channel, vendor_name, stored_filename)
               VALUES ('doc.pdf', ?, ?, 'processed', 'web_upload', ?, ?)""",
            (real_hash, len(body), vendor, stored),
        )
    return real_hash, stored


def test_a_backup_restores_into_a_working_data_dir(db_path, tmp_data_dir, tmp_path):
    """The round trip nobody had ever run."""
    from scripts.restore_backup import restore

    init_settings()
    file_hash, stored = _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 restore me")

    backup_dir, _ = build_backup(str(tmp_data_dir))

    target = str(tmp_path / "restored")
    report = restore(backup_dir, target)

    assert report["documents"] == 1
    assert report["originals_verified"] == 1

    # The database came back and is queryable standalone.
    assert _scalar(
        os.path.join(target, "receiptory.db"),
        "SELECT vendor_name FROM documents WHERE file_hash = ?" .replace("?", f"'{file_hash}'"),
    ) == "Vendor"
    # And the bytes its rows point at came with it.
    assert os.path.exists(os.path.join(target, "storage", "originals", f"{file_hash}.pdf"))
    assert os.path.exists(os.path.join(target, "storage", "filed", stored))
    # No sidecars ride along into the restored directory.
    assert not os.path.exists(os.path.join(target, "receiptory.db-wal"))


def test_restore_refuses_a_non_empty_target_without_force(db_path, tmp_data_dir, tmp_path):
    """Restoring writes over real documents. The default must not be able to
    destroy a live install by accident."""
    from scripts.restore_backup import restore, RestoreRefused

    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 a")
    backup_dir, _ = build_backup(str(tmp_data_dir))

    target = tmp_path / "occupied"
    target.mkdir()
    (target / "receiptory.db").write_bytes(b"precious")

    with pytest.raises(RestoreRefused, match="refusing to restore"):
        restore(backup_dir, str(target))

    assert (target / "receiptory.db").read_bytes() == b"precious"

    restore(backup_dir, str(target), force=True)
    assert (target / "receiptory.db").read_bytes() != b"precious"
    # Replaced, not merged: the old directory is kept beside it, not destroyed.
    kept = [p for p in tmp_path.iterdir() if ".pre-restore-" in p.name]
    assert len(kept) == 1
    assert (kept[0] / "receiptory.db").read_bytes() == b"precious"


def test_restore_refuses_a_backup_that_would_not_restore(db_path, tmp_data_dir, tmp_path):
    """Verification runs before anything is written, so a bad backup cannot
    half-overwrite a good directory."""
    from scripts.restore_backup import restore
    from backend.backup.verify import BackupVerificationError

    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 b")
    backup_dir, _ = build_backup(str(tmp_data_dir))

    # A fatal condition: the database has no schema. A merely missing file is
    # damage now and must not block a restore.
    os.remove(os.path.join(backup_dir, "receiptory.db"))
    sqlite3.connect(os.path.join(backup_dir, "receiptory.db")).close()

    target = str(tmp_path / "should_stay_empty")
    with pytest.raises(BackupVerificationError, match="no schema_version"):
        restore(backup_dir, target)
    assert not os.path.exists(os.path.join(target, "receiptory.db"))


def test_a_missing_file_is_reported_without_stopping_the_backup(db_path, tmp_data_dir):
    """A lost file must not become a permanent backup outage.

    Verification used to raise here, which turned one damaged document into
    zero backups forever -- and api/upload.py can produce an original with no
    extension at all, so it was reachable from an ordinary upload. 313 intact
    documents are worth keeping when the 314th lost its file.
    """
    init_settings()
    with get_connection() as conn:
        file_hash = _insert_doc(conn, "vanish", "Gone Vendor", data_dir=str(tmp_data_dir))
    os.remove(os.path.join(str(tmp_data_dir), "storage", "originals", f"{file_hash}.pdf"))

    backup_dir, report = build_backup(str(tmp_data_dir))

    assert os.path.exists(os.path.join(backup_dir, "receiptory.db"))
    assert any("original missing" in p for p in report["problems"])


async def test_damaged_documents_reach_the_owner(backup_env, tmp_data_dir, monkeypatch):
    """Reported, not silent: the damage rides the same channel a failed upload
    uses, which is on by default."""
    sent, fake_dir = backup_env
    set_setting("backup_destination", "")
    monkeypatch.setattr(
        "backend.backup.scheduler.build_backup",
        lambda d: (fake_dir, {"documents": 2, "originals_verified": 1,
                              "filed_verified": 1, "schema_version": 9,
                              "problems": ["document 7: original missing"]}),
    )

    backup_id = await run_backup(str(tmp_data_dir), trigger="scheduled")
    row = _backup_row(backup_id)

    assert "could not be verified" in row["error"]
    assert "document 7" in row["error"]
    assert [e for e, _ in sent] == ["backup_failed"], sent


def test_an_original_with_no_extension_is_found(db_path, tmp_data_dir):
    """api/upload.py's `splitext(filename or ".pdf")` guards a None filename,
    not a missing extension, so a file uploaded as "receipt" is stored as
    originals/<hash> with no dot. Globbing "<hash>.*" alone misses it."""
    import hashlib
    init_settings()
    body = b"%PDF-1.4 no extension"
    file_hash = hashlib.sha256(body).hexdigest()
    originals = os.path.join(str(tmp_data_dir), "storage", "originals")
    os.makedirs(originals, exist_ok=True)
    with open(os.path.join(originals, file_hash), "wb") as f:  # no suffix
        f.write(body)
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes,
                                      status, submission_channel)
               VALUES ('receipt', ?, ?, 'processed', 'web_upload')""",
            (file_hash, len(body)),
        )

    _, report = build_backup(str(tmp_data_dir))
    assert report["problems"] == []
    assert report["originals_verified"] == 1


def test_verify_rejects_a_database_whose_paths_escape_the_storage_dir(db_path, tmp_data_dir):
    """A backup database is untrusted input on the restore path. An absolute
    stored_filename discards the prefix in os.path.join, and api/export.py joins
    the same value to build a zip. Verified: without this check a row naming
    "/etc/hostname" verified clean and counted as a restorable file."""
    from backend.backup.verify import verify_backup, BackupVerificationError

    init_settings()
    with get_connection() as conn:
        _insert_doc(conn, "ok", "Vendor", data_dir=str(tmp_data_dir))
    backup_dir, _ = build_backup(str(tmp_data_dir))

    conn = sqlite3.connect(os.path.join(backup_dir, "receiptory.db"))
    conn.execute("UPDATE documents SET stored_filename = '/etc/hostname'")
    conn.commit()
    conn.close()

    with pytest.raises(BackupVerificationError, match="escape the storage directory"):
        verify_backup(backup_dir)


def test_verify_rejects_a_non_hex_file_hash(db_path, tmp_data_dir):
    """file_hash is interpolated into a glob pattern; a metacharacter changes
    which file is matched and read."""
    from backend.backup.verify import verify_backup, BackupVerificationError

    init_settings()
    with get_connection() as conn:
        _insert_doc(conn, "ok", "Vendor", data_dir=str(tmp_data_dir))
    backup_dir, _ = build_backup(str(tmp_data_dir))

    conn = sqlite3.connect(os.path.join(backup_dir, "receiptory.db"))
    conn.execute("UPDATE documents SET file_hash = '../../etc/passwd'")
    conn.commit()
    conn.close()

    with pytest.raises(BackupVerificationError, match="escape the storage directory"):
        verify_backup(backup_dir)


def test_verify_rejects_a_database_with_no_tables(tmp_path):
    """The exact artifact the old file copy produced: structurally valid,
    integrity_check says ok, and it restores to nothing."""
    from backend.backup.verify import verify_backup, BackupVerificationError

    backup_dir = tmp_path / "empty_backup"
    backup_dir.mkdir()
    sqlite3.connect(str(backup_dir / "receiptory.db")).close()

    with pytest.raises(BackupVerificationError, match="no schema_version table"):
        verify_backup(str(backup_dir))


def test_verify_catches_a_file_that_does_not_match_its_hash(db_path, tmp_data_dir):
    """The filename IS the sha256 of the contents, so a file copied while it was
    still being written is detectable."""
    from backend.backup.verify import verify_backup

    init_settings()
    file_hash, _stored = _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 d")
    backup_dir, _ = build_backup(str(tmp_data_dir))

    victim = os.path.join(backup_dir, "storage", "originals", f"{file_hash}.pdf")
    with open(victim, "wb") as f:
        f.write(b"truncated")

    report = verify_backup(backup_dir)
    assert any("does not match its hash" in p for p in report["problems"])


def test_the_secret_checklist_does_not_claim_a_key_was_unset_when_it_cannot_tell():
    """settings.json only covers keys in DEFAULTS. llm_api_keys is DB-only and
    bypasses get_all_settings, so it never appears there whether or not it was
    set -- reporting "not set" would tell someone mid-recovery they had no API
    keys when they did."""
    from scripts.restore_backup import _secret_state

    saved = {"telegram_bot_token": "ab***yz", "gmail_app_password": ""}
    assert _secret_state(saved, "telegram_bot_token") == "was set"
    assert _secret_state(saved, "gmail_app_password") == "not set at backup time"
    assert "unknown" in _secret_state(saved, "llm_api_keys")
    assert "unknown" in _secret_state(None, "telegram_bot_token")


def test_verify_rejects_a_backup_containing_symlinks(db_path, tmp_data_dir, tmp_path):
    """shutil.copytree follows symlinks by default, so restoring a directory
    that acquired one would materialise whatever is on the other end as a real
    file inside the restored data directory."""
    from backend.backup.verify import verify_backup, BackupVerificationError

    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 sym")
    backup_dir, _ = build_backup(str(tmp_data_dir))

    outside = tmp_path / "outside.txt"
    outside.write_text("content from outside the backup")
    os.symlink(str(outside), os.path.join(backup_dir, "storage", "originals", "sneaky.pdf"))

    with pytest.raises(BackupVerificationError, match="symlink"):
        verify_backup(backup_dir)


# --- gaps the review mutation-proved -----------------------------------------


def test_restore_runs_migrations_on_the_assembled_copy(
    db_path, tmp_data_dir, tmp_path, monkeypatch
):
    """Bringing an older snapshot forward is the stated reason the step exists,
    and a round trip of an already-current backup cannot tell whether it ran.

    (The stale-sidecar hazard this used to guard is gone: the restore is
    assembled in a directory created moments earlier, so there is nothing for
    SQLite to replay.)
    """
    import backend.database as db_mod
    from scripts.restore_backup import restore

    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 migrate")
    backup_dir, _ = build_backup(str(tmp_data_dir))

    target = tmp_path / "occupied"
    target.mkdir()
    (target / "receiptory.db").write_bytes(b"old install")

    seen = []
    real_init = db_mod.init_db

    def spy(path):
        seen.append({"path": path, "wal": os.path.exists(path + "-wal")})
        return real_init(path)

    monkeypatch.setattr("scripts.restore_backup.init_db", spy)
    restore(backup_dir, str(target), force=True)

    assert seen, "migrations never ran"
    # Migrations run on the assembled copy, before it is swapped in.
    assert ".restoring-" in seen[0]["path"], f"migrations ran on {seen[0]['path']}"
    assert seen[0]["wal"] is False
    # And the process global is left pointing somewhere that still exists.
    assert seen[-1]["path"] == os.path.join(str(target), "receiptory.db")


def test_verify_reports_a_missing_filed_copy(db_path, tmp_data_dir):
    """Half the file checking. Mutation-proven: replacing the whole
    stored_filename block with `pass` left every test green."""
    from backend.backup.verify import verify_backup

    init_settings()
    _file_hash, stored = _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 filed")
    backup_dir, _ = build_backup(str(tmp_data_dir))

    os.remove(os.path.join(backup_dir, "storage", "filed", stored))

    report = verify_backup(backup_dir)
    assert any("filed copy missing" in p for p in report["problems"])
    assert report["filed_verified"] == 0


def test_an_unfiled_document_does_not_fail_verification(db_path, tmp_data_dir):
    """A pending row has its original on disk but no stored_filename yet. Every
    real install has some; if this guard broke, every backup everywhere fails."""
    from backend.backup.verify import verify_backup

    init_settings()
    with get_connection() as conn:
        _insert_doc(conn, "filed-doc", "Filed Vendor", data_dir=str(tmp_data_dir))
        pending_hash = _insert_doc(conn, "pending-doc", "Pending Vendor")

    # Ingestion writes the original before inserting the row; filing has not run.
    originals = os.path.join(str(tmp_data_dir), "storage", "originals")
    with open(os.path.join(originals, f"{pending_hash}.pdf"), "wb") as f:
        f.write(b"%PDF-1.4 pending-doc")

    _backup_dir, report = build_backup(str(tmp_data_dir))
    assert report["documents"] == 2
    assert report["originals_verified"] == 2
    assert report["filed_verified"] == 1
    assert report["problems"] == []


def test_a_fatal_verification_does_not_leak_the_assembled_backup(db_path, tmp_data_dir, monkeypatch):
    """The directory holds a full copy of storage/ by then, and nothing
    downstream ever removes it: run_backup never gets its backup_dir."""
    from backend.backup.verify import BackupVerificationError

    init_settings()
    with get_connection() as conn:
        _insert_doc(conn, "leak", "Vendor", data_dir=str(tmp_data_dir))

    seen = {}

    def fatal(backup_dir):
        seen["dir"] = backup_dir
        raise BackupVerificationError("pretend the artifact is unusable")

    monkeypatch.setattr("backend.backup.runner.verify_backup", fatal)

    with pytest.raises(BackupVerificationError):
        build_backup(str(tmp_data_dir))

    assert not os.path.exists(seen["dir"]), "a failed backup left its directory behind"


def test_cli_verify_only_exits_zero_and_writes_nothing(db_path, tmp_data_dir, monkeypatch, capsys):
    """main() is the entire user-facing entry point of this feature."""
    import sys as _sys
    from scripts import restore_backup

    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 cli")
    backup_dir, _ = build_backup(str(tmp_data_dir))

    monkeypatch.setattr(_sys, "argv", ["restore_backup.py", "--verify-only", backup_dir])
    assert restore_backup.main() == 0

    out = capsys.readouterr().out
    assert json.loads(out[: out.index("}") + 1])["documents"] == 1
    assert "would restore" in out


def test_cli_refuses_a_full_target_with_exit_code_two(db_path, tmp_data_dir, tmp_path, monkeypatch, capsys):
    """A refusal is a different outcome from a bad backup, and the message an
    operator reads must not name pytest as the command to run."""
    import sys as _sys
    from scripts import restore_backup

    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 cli2")
    backup_dir, _ = build_backup(str(tmp_data_dir))

    target = tmp_path / "occupied"
    target.mkdir()
    (target / "receiptory.db").write_bytes(b"precious")

    monkeypatch.setattr(_sys, "argv", ["restore_backup.py", backup_dir, str(target)])
    assert restore_backup.main() == 2

    err = capsys.readouterr().err
    assert "refusing to restore" in err
    assert "Nothing was written." in err
    # The suggested command must be the script path, not argv[0] (which is the
    # test runner here, and would print an instruction that does not work).
    hint = [ln for ln in err.splitlines() if ln.strip().endswith(".restored")]
    assert hint and hint[0].strip().startswith("scripts/restore_backup.py"), err
    assert (target / "receiptory.db").read_bytes() == b"precious"


# --- restore replaces rather than merges (issue #50) --------------------------


def test_force_replaces_the_target_instead_of_merging_into_it(
    db_path, tmp_data_dir, tmp_path
):
    """Merging left rows from the backup beside files from the newer install:
    documents ingested since the backup lost their rows but kept their bytes as
    unreachable orphans, and the post-restore check could not notice because it
    only asserts that referenced files exist."""
    from scripts.restore_backup import restore

    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 in-backup")
    backup_dir, _ = build_backup(str(tmp_data_dir))

    target = tmp_path / "live"
    (target / "storage" / "originals").mkdir(parents=True)
    (target / "receiptory.db").write_bytes(b"older install")
    orphan = target / "storage" / "originals" / "newer-document.pdf"
    orphan.write_bytes(b"ingested after the backup")

    restore(backup_dir, str(target), force=True)

    # The newer file is NOT left lying in the restored tree.
    assert not orphan.exists(), "restore merged instead of replacing"
    # It is not destroyed either -- it moved aside with the rest.
    kept = [p for p in tmp_path.iterdir() if ".pre-restore-" in p.name]
    assert len(kept) == 1
    assert (kept[0] / "storage" / "originals" / "newer-document.pdf").exists()


def test_restore_refuses_while_the_app_has_the_database_open(
    db_path, tmp_data_dir, tmp_path
):
    """Restoring under a running app lets it flush cached pages into the
    freshly restored file and destroy both copies.

    Keyed on -shm, which exists while any connection is open: measured, a
    BEGIN EXCLUSIVE probe SUCCEEDS against a connected idle app and would have
    detected nothing.
    """
    from scripts.restore_backup import restore, RestoreRefused

    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 inuse")
    backup_dir, _ = build_backup(str(tmp_data_dir))

    target = tmp_path / "running"
    target.mkdir()
    live = sqlite3.connect(str(target / "receiptory.db"))
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("CREATE TABLE t(a)")
    live.commit()
    try:
        assert (target / "receiptory.db-shm").exists()  # precondition
        with pytest.raises(RestoreRefused, match="in use"):
            restore(backup_dir, str(target), force=True)
    finally:
        live.close()

    # Nothing was staged or swapped.
    assert not any(".pre-restore-" in p.name for p in tmp_path.iterdir())
    assert not any(".restoring-" in p.name for p in tmp_path.iterdir())


def test_a_restore_that_fails_verification_leaves_the_target_untouched(
    db_path, tmp_data_dir, tmp_path, monkeypatch
):
    """Assembled beside the target, so a failure is contained. Previously the
    target was written first and the operator was told nothing had been."""
    from scripts.restore_backup import restore, RestoreRefused
    from backend.backup.verify import BackupVerificationError
    import scripts.restore_backup as rb

    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 fail")
    backup_dir, _ = build_backup(str(tmp_data_dir))

    target = tmp_path / "live"
    target.mkdir()
    (target / "receiptory.db").write_bytes(b"do not touch me")

    real_verify = rb.verify_backup
    calls = []

    def verify_then_fail(path):
        calls.append(path)
        if len(calls) == 1:          # the pre-flight check on the backup
            return real_verify(path)
        raise BackupVerificationError("pretend the assembled copy is bad")

    monkeypatch.setattr(rb, "verify_backup", verify_then_fail)

    with pytest.raises(RestoreRefused, match="left untouched"):
        restore(backup_dir, str(target), force=True)

    assert (target / "receiptory.db").read_bytes() == b"do not touch me"
    assert not any(".pre-restore-" in p.name for p in tmp_path.iterdir())
    # The second check must be of the ASSEMBLED copy, not the source again --
    # its whole purpose is catching a copy that went wrong in transit.
    assert calls[0] == backup_dir
    assert ".restoring-" in calls[1], f"post-assembly verify read {calls[1]}"


def test_restore_refuses_when_there_is_not_enough_space(
    db_path, tmp_data_dir, tmp_path, monkeypatch
):
    """The old directory is kept, so both copies have to fit. Running out
    halfway would leave neither install whole."""
    from scripts.restore_backup import restore, RestoreRefused
    import scripts.restore_backup as rb

    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 space")
    backup_dir, _ = build_backup(str(tmp_data_dir))

    monkeypatch.setattr(rb, "_free_bytes", lambda p: 1)

    with pytest.raises(RestoreRefused, match="not enough space"):
        restore(backup_dir, str(tmp_path / "nope"))


def test_local_state_the_backup_never_held_survives_a_replace(
    db_path, tmp_data_dir, tmp_path
):
    """Two different policies happen to have the same outcome here.

    rclone.conf is FROM_TARGET: never in a backup at all, because the archive
    travels unencrypted to the very service its credentials unlock. Replacing
    the target would take it away, so the restored system would stop backing up
    immediately after a disaster recovery.

    scanner_test_set is BACKUP_THEN_TARGET, and this is the critical case for
    it: the backup here predates the change that started including frames, so
    the fallback is what keeps them. Without it, restoring any backup made
    before that change would delete the 57 labelled frames off a machine that
    still had them, while the scanner_test_frames rows went on pointing at them.
    """
    from scripts.restore_backup import restore

    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 carry")
    backup_dir, _ = build_backup(str(tmp_data_dir))

    target = tmp_path / "live"
    (target / "scanner_test_set").mkdir(parents=True)
    (target / "receiptory.db").write_bytes(b"older install")
    (target / "rclone.conf").write_text("[receiptory_onedrive]\ntype = onedrive\n")
    (target / "scanner_test_set" / "frame1.jpg").write_bytes(b"frame")

    restore(backup_dir, str(target), force=True)

    assert (target / "rclone.conf").read_text().startswith("[receiptory_onedrive]")
    assert (target / "scanner_test_set" / "frame1.jpg").read_bytes() == b"frame"
    # Still a replace, not a merge: the backup's own content is what landed.
    assert (target / "receiptory.db").read_bytes() != b"older install"


def test_a_failed_swap_puts_the_original_back(db_path, tmp_data_dir, tmp_path, monkeypatch):
    """The window between the two renames is the only moment the data directory
    does not exist. If the second fails, the first must be undone rather than
    leaving the owner with nothing where their data used to be."""
    from scripts.restore_backup import restore, RestoreRefused

    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 swapfail")
    backup_dir, _ = build_backup(str(tmp_data_dir))

    target = tmp_path / "live"
    target.mkdir()
    (target / "receiptory.db").write_bytes(b"the only copy")

    real_rename = os.rename
    calls = []

    def rename_failing_on_the_second(src, dst):
        calls.append((src, dst))
        if len(calls) == 2:
            raise OSError("pretend the second rename failed")
        return real_rename(src, dst)

    monkeypatch.setattr(os, "rename", rename_failing_on_the_second)

    with pytest.raises(RestoreRefused, match="put back unchanged"):
        restore(backup_dir, str(target), force=True)

    # The rollback ran: the data is where it started, not stranded under a name
    # nobody printed.
    assert (target / "receiptory.db").read_bytes() == b"the only copy"
    assert not any(".pre-restore-" in p.name for p in tmp_path.iterdir())


def test_restore_refuses_a_symlinked_target_by_resolving_it(
    db_path, tmp_data_dir, tmp_path
):
    """os.rename does not follow symlinks: renaming a symlinked target moves the
    LINK, so the kept path would point at the live data and "delete it once you
    are satisfied" would destroy the real install."""
    from scripts.restore_backup import restore

    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 symtarget")
    backup_dir, _ = build_backup(str(tmp_data_dir))

    real = tmp_path / "real_data"
    real.mkdir()
    (real / "receiptory.db").write_bytes(b"older install")
    link = tmp_path / "data_link"
    os.symlink(str(real), str(link))

    restore(backup_dir, str(link), force=True)

    # The link still points at a real directory holding the restored install,
    # and the kept copy is a real directory, not a dangling link.
    assert os.path.isdir(str(link))
    kept = [p for p in tmp_path.iterdir() if ".pre-restore-" in p.name]
    assert len(kept) == 1 and not kept[0].is_symlink()
    assert (kept[0] / "receiptory.db").read_bytes() == b"older install"


def test_restore_warns_that_the_restored_system_has_no_password(
    db_path, tmp_data_dir, tmp_path, capsys
):
    """The snapshot strips auth_password_hash, so config.init_settings finds no
    row and seeds bcrypt("admin") at the next start. The restored install is
    reachable with admin/admin and the secret checklist only says "was set"."""
    from scripts.restore_backup import restore

    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 pw")
    backup_dir, _ = build_backup(str(tmp_data_dir))

    restore(backup_dir, str(tmp_path / "restored"))

    out = capsys.readouterr().out
    assert "NO PASSWORD SET" in out
    assert "admin / admin" in out


def test_restore_refuses_a_target_that_is_a_file(db_path, tmp_data_dir, tmp_path):
    """Neither branch of the swap handles it, and the rename would fail ENOTDIR
    after the whole backup had already been copied."""
    from scripts.restore_backup import restore, RestoreRefused

    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 file")
    backup_dir, _ = build_backup(str(tmp_data_dir))

    target = tmp_path / "not_a_dir"
    target.write_text("i am a file")

    with pytest.raises(RestoreRefused, match="not a directory"):
        restore(backup_dir, str(target))


def test_restore_into_an_existing_empty_directory(db_path, tmp_data_dir, tmp_path):
    """The unoccupied-but-present branch: rename onto an existing empty dir."""
    from scripts.restore_backup import restore

    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 empty")
    backup_dir, _ = build_backup(str(tmp_data_dir))

    target = tmp_path / "empty"
    target.mkdir()

    report = restore(backup_dir, str(target))
    assert report["documents"] == 1
    assert (target / "receiptory.db").exists()
    assert not any(".pre-restore-" in p.name for p in tmp_path.iterdir())


def test_counting_the_targets_documents_does_not_write_to_it(
    db_path, tmp_data_dir, tmp_path
):
    """The count is a courtesy message, and it reads the copy being kept as the
    owner's undo. Opening read-write would recover and checkpoint a hot WAL."""
    import scripts.restore_backup as rb

    init_settings()
    target = tmp_path / "live"
    target.mkdir()
    db = target / "receiptory.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO documents DEFAULT VALUES")
    conn.commit()
    conn.close()

    # A HOT wal: copy the files out mid-transaction so the copy has an
    # unreplayed -wal and no open connection, the shape a crash leaves behind.
    # Without one, a read-write open looks harmless and the test proves nothing.
    live = sqlite3.connect(str(db))
    live.execute("INSERT INTO documents DEFAULT VALUES")
    live.commit()
    crashed = tmp_path / "crashed"
    crashed.mkdir()
    for suffix in ("", "-wal", "-shm"):
        src = str(db) + suffix
        if os.path.exists(src):
            shutil.copy2(src, str(crashed / ("receiptory.db" + suffix)))
    live.close()

    hot = crashed / "receiptory.db"
    assert (crashed / "receiptory.db-wal").stat().st_size > 0  # precondition
    before = (hot.stat().st_size, (crashed / "receiptory.db-wal").stat().st_size)

    rb._document_count(str(hot))

    after = (hot.stat().st_size, (crashed / "receiptory.db-wal").stat().st_size)
    assert after == before, "counting recovered the hot WAL and mutated the kept copy"


def test_the_swap_rechecks_that_the_target_is_still_idle(
    db_path, tmp_data_dir, tmp_path, monkeypatch
):
    """The first check happens minutes before the swap, on the far side of a
    full copy of the storage tree. Docker's restart policy can start the app in
    that window; renaming a directory whose files are open succeeds, so the app
    would carry on writing into the directory the owner is told to delete."""
    from scripts.restore_backup import restore, RestoreRefused
    import scripts.restore_backup as rb

    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 racy")
    backup_dir, _ = build_backup(str(tmp_data_dir))

    target = tmp_path / "live"
    target.mkdir()
    (target / "receiptory.db").write_bytes(b"the only copy")

    checks = []

    def busy_the_second_time(path):
        checks.append(path)
        return len(checks) > 1  # idle at the pre-flight check, busy at the swap

    monkeypatch.setattr(rb, "_in_use", busy_the_second_time)

    with pytest.raises(RestoreRefused, match="became busy"):
        restore(backup_dir, str(target), force=True)

    assert len(checks) == 2, "the swap did not re-check"
    assert (target / "receiptory.db").read_bytes() == b"the only copy"
    assert not any(".pre-restore-" in p.name for p in tmp_path.iterdir())


# --- issue #45: what a backup contains, and what it must not -----------------


def _backup_names(backup_dir, *parts):
    path = os.path.join(backup_dir, *parts)
    return set(os.listdir(path)) if os.path.isdir(path) else set()


def test_page_cache_and_tmp_are_not_in_the_backup(db_path, tmp_data_dir):
    """131MB of the 296MB live tree is regenerable page renders, and tmp is
    ingestion scratch. Both rebuild on demand -- render_page mkdirs its own
    cache directory, and url_fetcher.fetch_url (the only public entry point
    that writes to tmp) mkdirs before every download."""
    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 excl")
    cache = tmp_data_dir / "storage" / "page_cache" / "7"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "page_0.png").write_bytes(b"\x89PNG regenerable")
    tmp = tmp_data_dir / "storage" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "half-download.pdf").write_bytes(b"transient")

    backup_dir, _ = build_backup(str(tmp_data_dir))

    assert "page_cache" not in _backup_names(backup_dir, "storage")
    assert "tmp" not in _backup_names(backup_dir, "storage")
    assert "originals" in _backup_names(backup_dir, "storage")


def test_a_nested_directory_named_page_cache_is_kept(db_path, tmp_data_dir):
    """Only the two at the ROOT of storage/ are regenerable. shutil.ignore_patterns
    would match the name at every level, and a hash-named directory could in
    principle be called page_cache."""
    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 nested")
    nested = tmp_data_dir / "storage" / "originals" / "page_cache"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "real.pdf").write_bytes(b"%PDF-1.4 not regenerable")

    backup_dir, _ = build_backup(str(tmp_data_dir))

    assert "page_cache" in _backup_names(backup_dir, "storage", "originals")
    assert _backup_names(backup_dir, "storage", "originals", "page_cache") == {"real.pdf"}


def test_paired_converted_scratch_of_the_same_size_is_dropped(db_path, tmp_data_dir):
    """normalize.py leaves <stem>_converted.pdf behind after pipeline.py copies
    it to <hash>.pdf. Nothing ever deletes it, so converted/ is roughly twice
    the size it needs to be."""
    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 conv")
    conv = tmp_data_dir / "storage" / "converted"
    (conv / "abc123.pdf").write_bytes(b"%PDF-1.4 the real one")
    (conv / "abc123_converted.pdf").write_bytes(b"%PDF-1.4 the real one")

    backup_dir, _ = build_backup(str(tmp_data_dir))

    assert _backup_names(backup_dir, "storage", "converted") == {"abc123.pdf"}


def test_converted_scratch_is_kept_when_its_partner_is_a_different_size(
    db_path, tmp_data_dir
):
    """The reachable bad state: save_converted's existence guard permanently
    pins a <hash>.pdf left truncated by a crashed copy, and nothing verifies
    converted/ yet. Dropping the scratch there would discard the only intact
    copy."""
    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 trunc")
    conv = tmp_data_dir / "storage" / "converted"
    (conv / "abc123.pdf").write_bytes(b"%PDF-1.4 trun")          # crashed copy
    (conv / "abc123_converted.pdf").write_bytes(b"%PDF-1.4 the whole thing")

    backup_dir, _ = build_backup(str(tmp_data_dir))

    assert _backup_names(backup_dir, "storage", "converted") == {
        "abc123.pdf",
        "abc123_converted.pdf",
    }


def test_unpaired_converted_scratch_is_kept(db_path, tmp_data_dir):
    """gmail.py:175 normalizes a system tempfile, so a failure before its unlink
    leaks converted/tmpXXXXXXXX_converted.pdf -- a name nothing will ever match.
    Keep it rather than guess."""
    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 orphan")
    conv = tmp_data_dir / "storage" / "converted"
    (conv / "tmpq7x1_converted.pdf").write_bytes(b"%PDF-1.4 no partner")

    backup_dir, _ = build_backup(str(tmp_data_dir))

    assert "tmpq7x1_converted.pdf" in _backup_names(backup_dir, "storage", "converted")


def test_in_flight_temp_files_never_enter_a_backup(db_path, tmp_data_dir):
    """atomic.py writes to .rcpt-tmp-* before renaming. A backup that caught one
    mid-write would ship a partial file under a name nothing references."""
    from backend.atomic import TMP_PREFIX

    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 inflight")
    originals = tmp_data_dir / "storage" / "originals"
    (originals / f"{TMP_PREFIX}abcd").write_bytes(b"half a file")

    backup_dir, _ = build_backup(str(tmp_data_dir))

    assert not [
        n for n in _backup_names(backup_dir, "storage", "originals")
        if n.startswith(TMP_PREFIX)
    ]


def test_scanner_test_set_is_backed_up(db_path, tmp_data_dir):
    """scanner_test_frames rows come across in the database and point at these
    files by path, so leaving the frames behind restored a Lab full of dangling
    references. 14MB of labelled frames that cannot be re-shot."""
    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 frames")
    frames = tmp_data_dir / "scanner_test_set" / "2026-09-01"
    frames.mkdir(parents=True)
    (frames / "shot.jpg").write_bytes(b"\xff\xd8 labelled frame")

    backup_dir, _ = build_backup(str(tmp_data_dir))

    assert (
        open(os.path.join(backup_dir, "scanner_test_set", "2026-09-01", "shot.jpg"), "rb").read()
        == b"\xff\xd8 labelled frame"
    )


def test_a_missing_scanner_test_set_is_not_an_error(db_path, tmp_data_dir):
    """A fresh install has never opened the Lab."""
    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 nolab")
    assert not (tmp_data_dir / "scanner_test_set").exists()

    backup_dir, report = build_backup(str(tmp_data_dir))

    assert report["problems"] == []
    assert not os.path.exists(os.path.join(backup_dir, "scanner_test_set"))


def test_the_database_is_snapshotted_before_the_files_are_copied(
    db_path, tmp_data_dir, monkeypatch
):
    """Issue #45 proposed reversing this. It must not be.

    Every ingestion path writes the file before inserting the row, so DB-first
    means every row in the snapshot already had its bytes on disk. Files-first
    inverts that into a row whose file was written after the copy walked past
    its directory -- a dangling row, which is not recoverable.

    Asserted at the CALL SITE: swapping the two statements in build_backup makes
    this fail, which a docstring or a helper-level test would not.
    """
    import backend.backup.runner as runner_mod

    order = []
    real_snapshot = runner_mod.snapshot_database
    real_copytree = shutil.copytree

    def tracking_snapshot(*a, **k):
        order.append("snapshot")
        return real_snapshot(*a, **k)

    def tracking_copytree(*a, **k):
        order.append("copytree")
        return real_copytree(*a, **k)

    monkeypatch.setattr(runner_mod, "snapshot_database", tracking_snapshot)
    monkeypatch.setattr(shutil, "copytree", tracking_copytree)

    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 order")
    build_backup(str(tmp_data_dir))

    assert "copytree" in order, "the storage tree was never copied"
    assert order[0] == "snapshot", (
        f"the database must be snapshotted before the files are copied; got {order}"
    )


def test_a_file_that_vanishes_mid_copy_is_retried(db_path, tmp_data_dir, monkeypatch):
    """os.replace is not atomic for EXISTENCE on the fuse mount that holds the
    data directory: measured 118 ENOENT in 46,337 reads racing 3,174 replaces,
    against 0 in 172,022 on btrfs. copytree turns one such miss into a
    shutil.Error that fails the ENTIRE backup, so a document being reprocessed
    during a backup would take the whole run down."""
    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 vanish")

    real_copy2 = shutil.copy2
    failed_once = []

    def vanishing_copy2(src, dst, **kwargs):
        if not failed_once and str(src).endswith(".pdf"):
            failed_once.append(src)
            raise FileNotFoundError(src)
        return real_copy2(src, dst, **kwargs)

    monkeypatch.setattr(shutil, "copy2", vanishing_copy2)

    backup_dir, report = build_backup(str(tmp_data_dir))

    assert failed_once, "the test never exercised the ENOENT path"
    assert report["problems"] == [], "the retry should have landed the file"


def test_a_symlink_anywhere_in_the_backup_is_fatal(db_path, tmp_data_dir, tmp_path):
    """The symlink scan used to root at backup_dir/storage, which quietly made
    'a well-formed backup contains regular files only' true of one tree out of
    three once scanner_test_set joined the backup -- and restore copytrees that
    one with symlinks followed too."""
    from backend.backup.verify import verify_backup, BackupVerificationError

    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 symlab")
    frames = tmp_data_dir / "scanner_test_set" / "2026-09-01"
    frames.mkdir(parents=True)
    (frames / "shot.jpg").write_bytes(b"\xff\xd8 frame")
    backup_dir, _ = build_backup(str(tmp_data_dir))

    outside = tmp_path / "outside.txt"
    outside.write_text("content from outside the backup")
    os.symlink(
        str(outside),
        os.path.join(backup_dir, "scanner_test_set", "2026-09-01", "sneaky.jpg"),
    )

    with pytest.raises(BackupVerificationError, match="symlink"):
        verify_backup(backup_dir)


def test_scanner_frames_come_from_the_backup_when_it_has_them(
    db_path, tmp_data_dir, tmp_path
):
    """The other half of BACKUP_THEN_TARGET. A restore reproduces the backup, so
    once backups carry the frames the backup's copy is authoritative and the
    target's is replaced -- otherwise a restore quietly becomes a merge again,
    which is what #50 removed."""
    from scripts.restore_backup import restore

    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 framesrc")
    backed_up = tmp_data_dir / "scanner_test_set" / "2026-09-01"
    backed_up.mkdir(parents=True)
    (backed_up / "from-backup.jpg").write_bytes(b"\xff\xd8 backup copy")
    backup_dir, _ = build_backup(str(tmp_data_dir))

    target = tmp_path / "live"
    (target / "scanner_test_set" / "2026-01-01").mkdir(parents=True)
    (target / "receiptory.db").write_bytes(b"older install")
    (target / "scanner_test_set" / "2026-01-01" / "stale.jpg").write_bytes(b"old")

    restore(backup_dir, str(target), force=True)

    assert (
        target / "scanner_test_set" / "2026-09-01" / "from-backup.jpg"
    ).read_bytes() == b"\xff\xd8 backup copy"
    assert not (target / "scanner_test_set" / "2026-01-01").exists(), (
        "the backup is authoritative when it has the tree; a restore is a "
        "replace, not a merge"
    )


def test_restoring_an_old_backup_onto_a_fresh_target_does_not_crash(
    db_path, tmp_data_dir, tmp_path
):
    """Disaster recovery: nothing on the machine, and a backup that predates
    frames being included. There is nothing to fall back to and that is fine --
    the restore must complete rather than trip over the missing tree."""
    from scripts.restore_backup import restore

    init_settings()
    _seed_document(str(tmp_data_dir), None, b"%PDF-1.4 freshdr")
    backup_dir, _ = build_backup(str(tmp_data_dir))

    target = tmp_path / "brand-new"

    restore(backup_dir, str(target), force=True)

    assert (target / "receiptory.db").exists()
    assert not (target / "scanner_test_set").exists()


def test_every_restore_source_is_something_the_backup_writes_or_target_holds(db_path):
    """The two-tuple shape this replaced promised disjointness that nothing
    enforced, and could not express 'from the backup, else from the machine'
    without breaking its own promise. Pin that every FROM_BACKUP entry is
    actually a tree build_backup writes, so the reader cannot drift from the
    writer."""
    from backend.backup.runner import BACKUP_TREES
    from scripts.restore_backup import RESTORE_SOURCES, FROM_BACKUP, FROM_TARGET

    for tree in BACKUP_TREES:
        assert tree in RESTORE_SOURCES, f"{tree} is backed up but never restored"
        assert RESTORE_SOURCES[tree] != FROM_TARGET

    from_backup_only = {n for n, p in RESTORE_SOURCES.items() if p == FROM_BACKUP}
    assert from_backup_only <= set(BACKUP_TREES), (
        "a FROM_BACKUP entry that build_backup never writes would restore as "
        "silently absent"
    )


# --- issue #45: rclone.conf is rewritten, not truncated ----------------------


def test_rclone_config_is_written_atomically_and_stays_private(tmp_path, monkeypatch):
    """The file holds OAuth refresh tokens. mkstemp would decide the mode, so it
    is passed explicitly."""
    import configparser
    from backend.backup.cloud_auth import _write_rclone_config

    conf = tmp_path / "rclone.conf"
    config = configparser.ConfigParser()
    config.add_section("receiptory_onedrive")
    config.set("receiptory_onedrive", "type", "onedrive")

    seen = []
    real_replace = os.replace
    monkeypatch.setattr(
        os, "replace", lambda a, b: (seen.append(a), real_replace(a, b))[1]
    )

    _write_rclone_config(config, str(conf))

    assert "[receiptory_onedrive]" in conf.read_text()
    assert stat.S_IMODE(os.stat(conf).st_mode) == 0o600
    assert len(seen) == 1 and os.path.basename(seen[0]).startswith(".rcpt-tmp-")


def test_a_failed_rclone_config_write_leaves_every_remote_intact(tmp_path, monkeypatch):
    """open(conf,"w") truncates first, so a crash in that window empties the file
    and takes every configured remote with it. An OAuth remote heals on the next
    startup via restore_rclone_config; a hand-added sftp/S3/local remote lives
    here and nowhere else, because rclone.conf is deliberately not in a backup."""
    import configparser
    from backend.backup.cloud_auth import _write_rclone_config

    conf = tmp_path / "rclone.conf"
    conf.write_text("[my_sftp]\ntype = sftp\nhost = offsite\n")

    config = configparser.ConfigParser()
    config.add_section("receiptory_gdrive")
    config.set("receiptory_gdrive", "type", "drive")

    def die(*a, **k):
        raise OSError("power cut")

    monkeypatch.setattr(os, "replace", die)

    with pytest.raises(OSError):
        _write_rclone_config(config, str(conf))

    assert conf.read_text() == "[my_sftp]\ntype = sftp\nhost = offsite\n"
    assert not [n for n in os.listdir(tmp_path) if n.startswith(".rcpt-tmp-")]


def test_removing_a_remote_goes_through_the_atomic_writer(db_path, tmp_path, monkeypatch):
    """Call-site test. Testing the helper alone would pass while the two real
    writers still used open(conf,"w")."""
    import backend.backup.cloud_auth as cloud_auth

    init_settings()
    conf = tmp_path / "rclone.conf"
    conf.write_text("[receiptory_gdrive]\ntype = drive\n")
    monkeypatch.setattr(cloud_auth, "rclone_config_path", lambda: str(conf))

    calls = []
    real = cloud_auth._write_rclone_config
    monkeypatch.setattr(
        cloud_auth,
        "_write_rclone_config",
        lambda cfg, path: (calls.append(path), real(cfg, path))[1],
    )

    cloud_auth.remove_rclone_remote("gdrive")

    assert calls == [str(conf)], "remove_rclone_remote bypassed the atomic writer"
    assert "receiptory_gdrive" not in conf.read_text()
