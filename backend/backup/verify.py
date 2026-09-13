"""Assert that a backup directory would actually restore.

This exists because nothing ever tried. A backup was called good if rclone
exited 0, so a database that restored to zero tables (the WAL bug) and a file
tree nobody had ever cross-checked both shipped green for months.

Two grades of problem, deliberately:

**Fatal** means the artifact is worthless and must not be mistaken for a
backup -- the database is missing, corrupt, or has no schema, the file tree
contains symlinks a restore would follow outside itself, or a row carries a
path that would escape the storage directory. These raise.

**Damage** means one document's bytes are missing or do not match their hash.
These are reported, not raised. Refusing to back up 313 intact documents
because the 314th lost its file years ago would turn one bad row into zero
backups, forever -- a verifier that becomes a denial of service on the thing
it protects. The caller records the damage against the run so the owner is
told, and still keeps the artifact.

Safe against a live system: every ingestion path writes the file before
inserting the row (api/upload.py, ingestion/telegram.py and ingestion/gmail.py
both call save_original before their INSERT, as does ingestion/watched_folder.py)
and processing/pipeline.py calls save_filed before recording stored_filename.
A row captured in the snapshot therefore always has its bytes on disk already,
so a document arriving mid-backup cannot make a good backup look broken.
"""

import glob
import hashlib
import os
import re
import sqlite3

_HASH_CHUNK = 1 << 20

# How many offending entries to name before truncating. Enough to see a pattern,
# short enough to fit in a Telegram message and a database column.
_MAX_LISTED = 5

# The shapes the application itself produces: a sha256 hex digest, and a
# filename from processing/filing.py's generate_stored_filename. A backup
# database is untrusted input on the restore path -- an absolute
# stored_filename silently discards the directory prefix in os.path.join, and
# api/export.py joins the same value to build a zip. Verified: without this
# check a row naming "/etc/hostname" verified clean and counted as a restorable
# file. The schema has no CHECK constraint, so the invariant does not survive
# an imported database on its own.
_HASH_RE = re.compile(r"[0-9a-f]{64}")


class BackupVerificationError(RuntimeError):
    """The artifact is not a backup. Never raised for merely damaged documents."""


def _sha256(path: str) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _is_contained_name(name: str) -> bool:
    """True if `name` is a plain filename that cannot escape its directory."""
    return bool(name) and not os.path.isabs(name) and os.path.basename(name) == name


def schema_version(conn: sqlite3.Connection) -> int | None:
    """MAX(schema_version.version), or None if the table is not there at all."""
    try:
        return conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    except sqlite3.Error:
        return None


def _find_original(storage: str, file_hash: str) -> str | None:
    """Locate originals/<hash><ext>, whatever the extension turned out to be.

    api/upload.py derives the extension with splitext(filename or ".pdf"), whose
    `or` guards a None filename and not a missing one, so a file uploaded as
    "receipt" is stored as originals/<hash> with no dot at all. Globbing for
    "<hash>.*" alone misses it, and treating that as damage on every run would
    have made one odd upload look like permanent corruption.
    """
    bare = os.path.join(storage, "originals", file_hash)
    if os.path.exists(bare):
        return bare
    # glob.escape: the hash is validated as hex before this is called, but the
    # escape keeps that a belt-and-braces property of this function rather than
    # an invariant a future caller has to remember.
    matches = sorted(glob.glob(glob.escape(bare) + ".*"))
    return matches[0] if matches else None


def verify_backup(backup_dir: str) -> dict:
    """Check a backup end to end.

    Raises BackupVerificationError if the artifact is not a usable backup.
    Returns a report; `problems` lists per-document damage, which is reported
    rather than raised.
    """
    db_path = os.path.join(backup_dir, "receiptory.db")
    if not os.path.exists(db_path):
        raise BackupVerificationError(f"no receiptory.db in {backup_dir}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise BackupVerificationError(f"database fails integrity check: {integrity}")

        version = schema_version(conn)
        if version is None:
            # The exact artifact the old file copy produced: structurally valid,
            # integrity_check says ok, and it restores to nothing.
            raise BackupVerificationError("database has no schema_version table")

        # Soft-deleted rows are checked on purpose: deletion is a flag
        # (api/documents.py sets is_deleted = 1) and never unlinks the files, so
        # those bytes are still present and still have to restore.
        rows = conn.execute(
            "SELECT id, file_hash, stored_filename FROM documents"
        ).fetchall()
    finally:
        conn.close()

    storage = os.path.join(backup_dir, "storage")

    # A well-formed backup contains regular files only. shutil.copytree follows
    # symlinks by default (verified: a link in the source is materialised as a
    # real file holding the content it points at), so restoring a directory that
    # somehow acquired one would silently pull in whatever is on the other end.
    #
    # Walks the WHOLE backup, not just storage/. This used to root at
    # backup_dir/storage, which quietly made the promise above true of one tree
    # out of three once scanner_test_set joined the backup -- and the restore
    # script copytrees that one with symlinks followed too. Scanning the
    # directory itself rather than a list of names means a tree added later is
    # covered without anyone remembering to come back here.
    links = [
        os.path.relpath(os.path.join(root, name), backup_dir)
        for root, dirs, files in os.walk(backup_dir)
        for name in dirs + files
        if os.path.islink(os.path.join(root, name))
    ]
    if links:
        raise BackupVerificationError(
            f"backup contains {len(links)} symlink(s), which a restore would follow "
            f"outside the backup: {'; '.join(links[:_MAX_LISTED])}"
        )

    malformed = [
        f"document {r['id']}: {'file_hash' if r['file_hash'] and not _HASH_RE.fullmatch(r['file_hash']) else 'stored_filename'}"
        for r in rows
        if (r["file_hash"] and not _HASH_RE.fullmatch(r["file_hash"]))
        or (r["stored_filename"] and not _is_contained_name(r["stored_filename"]))
    ]
    if malformed:
        raise BackupVerificationError(
            f"database carries {len(malformed)} row(s) whose paths would escape the "
            f"storage directory: {'; '.join(malformed[:_MAX_LISTED])}"
        )

    problems: list[str] = []
    originals_verified = 0
    filed_verified = 0

    for row in rows:
        file_hash = row["file_hash"]
        if file_hash:
            found = _find_original(storage, file_hash)
            if not found:
                problems.append(f"document {row['id']}: original missing")
            elif _sha256(found) != file_hash:
                # The name IS the sha256 of the contents, so this catches a file
                # copied while it was still being written.
                problems.append(
                    f"document {row['id']}: {os.path.basename(found)} does not match its hash"
                )
            else:
                originals_verified += 1

        if row["stored_filename"]:
            if os.path.exists(os.path.join(storage, "filed", row["stored_filename"])):
                filed_verified += 1
            else:
                problems.append(f"document {row['id']}: filed copy missing")

    return {
        "schema_version": version,
        "documents": len(rows),
        "originals_verified": originals_verified,
        "filed_verified": filed_verified,
        "problems": problems,
    }


def format_report(report: dict) -> str:
    """One line summarising a verification result, for logs and the CLI."""
    line = (
        f"{report['documents']} documents, "
        f"{report['originals_verified']} originals hash-checked, "
        f"{report['filed_verified']} filed present, "
        f"schema {report['schema_version']}"
    )
    if report["problems"]:
        line += f" -- {len(report['problems'])} damaged"
    return line
