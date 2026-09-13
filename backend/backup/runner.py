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
from backend.atomic import TMP_PREFIX
from backend.backup.verify import verify_backup, format_report, schema_version

# The directories a backup contains, relative to data_dir. Defined here because
# build_backup writes them, and imported by scripts/restore_backup.py so the
# writer and the reader cannot disagree about what a backup is. It used to be
# re-declared over there with a comment promising the two "cannot drift apart",
# which nothing enforced.
#
# storage MUST stay first: tests/test_backup.py writes a row during the first
# copytree call to prove the snapshot is pinned, and that test means the
# storage tree.
#
# scanner_test_set sits at the data_dir root rather than inside storage/. It is
# here because scanner_test_frames rows (migration 006) come across in the
# database and point at those files, so leaving the frames behind restored a
# Lab full of dangling references. 14MB of labelled camera frames that cannot
# be re-shot, against the 131MB of page cache this change stops shipping.
BACKUP_TREES = ("storage", "logs", "scanner_test_set")

# Subtrees of storage/ that never need to leave the machine. page_cache is
# 131MB of 200-DPI PNG re-renders that storage.render_page rebuilds on demand
# (and recreates the directory itself, so its absence after a restore is not a
# failure). tmp is ingestion scratch; url_fetcher.fetch_url is the only public
# entry point that writes there and it mkdirs first, so that directory is also
# recreated on demand.
EXCLUDED_STORAGE_DIRS = frozenset({"page_cache", "tmp"})

# normalize.py writes storage/converted/<stem>_converted.pdf as a scratch file,
# pipeline.py copies it to <hash>.pdf, and nothing ever deletes the scratch --
# so converted/ is roughly twice the size it needs to be (5.7MB of duplicate
# here). Dropping the scratch from the backup is lossless because originals/ is
# in the backup and the conversion regenerates from it, NOT because the two
# files are equal: measured, 31 of 33 pairs are byte-identical and 2 differ
# (same size, different bytes -- PDF /CreationDate), because save_converted's
# existence guard makes <hash>.pdf the OLDEST conversion while the scratch is
# rewritten on every reprocess. See _paired_scratch for why comparing them
# would not buy anything.
_SCRATCH_SUFFIX = "_converted.pdf"

# Pause before retrying a copy that hit ENOENT. See _copy_tolerating_rename.
_RENAME_RETRY_DELAY_S = 0.05

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


def _paired_scratch(names: list[str]) -> set[str]:
    """Scratch conversions whose real <hash>.pdf counterpart is present.

    This deliberately does NOT compare the two files. An earlier version
    dropped the scratch only when the sizes matched, on the theory that a
    <hash>.pdf left truncated by a crashed copy made the scratch the only
    intact conversion. That theory is wrong: nothing ever READS
    converted/<stem>_converted.pdf. get_file_path (storage.py) and serve_page
    (api/documents.py) both resolve converted/<hash>.pdf and nothing else, so
    carrying the scratch into the backup rescues nothing -- the restored system
    still serves the truncated file.

    The exclusion is lossless for a different reason: originals/ is always in
    the backup and pipeline.py regenerates the conversion from it. Detecting a
    truncated <hash>.pdf is verification's job, not this function's (issue #51).

    An UNPAIRED scratch file is kept. gmail.py normalizes a system tempfile, so
    a failure before its unlink leaks converted/tmpXXXXXXXX_converted.pdf --
    a name nothing will ever match, and not ours to guess about.
    """
    present = set(names)
    return {
        name
        for name in names
        if name.endswith(_SCRATCH_SUFFIX)
        and name[: -len(_SCRATCH_SUFFIX)] + ".pdf" in present
    }


def _ignore_regenerable(storage_root: str):
    """copytree ignore callable: drop regenerable, transient and in-flight files.

    Scoped by exact directory rather than by name pattern. shutil.ignore_patterns
    matches at every level, and a hash-named directory could in principle be
    called `tmp` -- only the two at the root of storage/ are meant here.
    """
    storage_root = os.path.abspath(storage_root)
    converted_root = os.path.join(storage_root, "converted")

    def _ignore(directory: str, names: list[str]) -> set[str]:
        # At any depth: a file another thread is writing right now. Excluding
        # these also keeps a stale one from a SIGKILL out of every future
        # backup.
        drop = {n for n in names if n.startswith(TMP_PREFIX)}
        here = os.path.abspath(directory)
        if here == storage_root:
            drop |= EXCLUDED_STORAGE_DIRS.intersection(names)
        elif here == converted_root:
            drop |= _paired_scratch(names)
        return drop

    return _ignore


def _copy_tolerating_rename(src: str, dst: str, **kwargs) -> str:
    """copy2, retried once when the source vanishes mid-walk.

    os.replace is not atomic for EXISTENCE on the fuse mount that holds the
    data directory. Measured on this install: racing 3,174 replaces against
    continuous readers gave 118 ENOENT in 46,337 reads and zero torn reads; the
    same probe on btrfs gave 0 ENOENT in 172,022. So a reader sees complete-old,
    complete-new, or nothing.

    copytree collects a per-file failure into shutil.Error and raises at the end
    of the walk, which would fail the ENTIRE backup because one document was
    being reprocessed while the copy ran. save_original and save_converted have
    existence guards and never rewrite, and TMP_PREFIX files are excluded before
    they are reached, so save_filed during a reprocess is the only writer that
    opens this window. A retry a moment later reads the settled file.
    """
    try:
        return shutil.copy2(src, dst, **kwargs)
    except FileNotFoundError:
        time.sleep(_RENAME_RETRY_DELAY_S)
        logger.warning("Retrying %s: vanished mid-copy (rename window)", src)
        return shutil.copy2(src, dst, **kwargs)


def build_backup(data_dir: str) -> tuple[str, dict]:
    """Assemble a backup. Returns (directory, verification report).

    The report carries `problems`: per-document damage that is reported rather
    than raised, so one lost file cannot stop every future backup.
    """
    # mkdtemp, not a bare timestamp. The name used to be
    # receiptory_backup_<UTC to the SECOND> created with exist_ok=True, and the
    # trees are copied with dirs_exist_ok=True -- so two runs in the same second
    # assembled into the SAME directory and merged, one inheriting the other's
    # files. mkdtemp also creates at 0700 rather than 0755, which is the right
    # mode for a directory holding the whole database.
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = tempfile.mkdtemp(prefix=f"receiptory_backup_{timestamp}_")

    # Snapshot the database FIRST, then copy the files. This order is
    # load-bearing and issue #45 proposed reversing it; do not.
    #
    # Every ingestion path writes the file before inserting the row
    # (api/upload.py:53->58, ingestion/telegram.py:122->140,
    # ingestion/gmail.py:337->360 and :524->537,
    # ingestion/watched_folder.py:31->36), and pipeline.py calls save_filed
    # before recording stored_filename. So every row in the snapshot already
    # had its bytes on disk when the snapshot was taken, and those bytes are
    # still there when the copy runs. The worst case is a file that arrives
    # between the two with no row pointing at it: orphan bytes, recoverable by
    # hand from originals/<hash><ext>.
    #
    # Copying files first inverts that into a row in the snapshot whose file
    # was written after the copy already walked past its directory -- a
    # dangling row, which is not recoverable and which verify_backup reports as
    # damage on every backup that overlaps an upload.
    db_path = os.path.join(data_dir, "receiptory.db")
    snapshot_path = os.path.join(backup_dir, "receiptory.db")

    # One try around EVERYTHING that writes into backup_dir, not just the
    # verification. Nothing downstream would ever remove this directory: on any
    # failure run_backup never reaches its `backup_dir` assignment, so
    # local_path is never recorded and no retention knows the directory exists.
    # shutil.copytree in particular accumulates per-file failures and raises
    # shutil.Error at the END of the walk, so a copy fault used to leak a
    # partly-assembled copy of the whole storage tree into /tmp -- ~160MB, once
    # per run, every night, for as long as the fault persisted.
    try:
        if os.path.exists(db_path):
            snapshot_database(db_path, snapshot_path)

        ignore = _ignore_regenerable(os.path.join(data_dir, "storage"))
        for tree in BACKUP_TREES:
            src = os.path.join(data_dir, tree)
            if not os.path.exists(src):
                continue
            shutil.copytree(
                src,
                os.path.join(backup_dir, tree),
                dirs_exist_ok=True,
                ignore=ignore,
                copy_function=_copy_tolerating_rename,
            )

        # Export JSONL metadata from the snapshot, not the live database. The
        # snapshot is a pinned instant; the live database keeps moving while the
        # backup assembles (build_backup runs in an executor while uploads and
        # the processing queue carry on). Reading the live database here would
        # put two sources of truth in one backup directory that disagree about
        # the same documents, with nothing to tell a restorer which is right.
        _export_jsonl(
            os.path.join(backup_dir, "metadata.jsonl"),
            snapshot_path if os.path.exists(snapshot_path) else None,
        )

        # Export settings (with sensitive values masked)
        from backend.config import get_all_settings_masked
        settings = get_all_settings_masked()
        with open(os.path.join(backup_dir, "settings.json"), "w") as f:
            json.dump(settings, f, indent=2, default=str)

        # Prove the artifact restores before anyone is told it exists. rclone
        # exiting 0 only means bytes moved; it says nothing about whether the
        # database has tables in it or whether the files its rows point at came
        # along. Raising here fails the run, so a backup that would not restore
        # is reported as failed instead of uploaded and found years later.
        report = verify_backup(backup_dir)
    except BaseException:
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise

    logger.info(f"Backup verified: {format_report(report)}")
    if report["problems"]:
        # Damage is reported, not fatal. The caller records it against the run.
        logger.error(
            f"Backup has {len(report['problems'])} damaged document(s): "
            + "; ".join(report["problems"][:5])
        )

    logger.info(f"Backup assembled at {backup_dir}")
    return backup_dir, report


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
            expected_version = schema_version(source)
            actual_version = schema_version(dest)
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
