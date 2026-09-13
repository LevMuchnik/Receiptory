"""Assert that a backup directory would actually restore.

This exists because nothing ever tried. A backup was called good if rclone
exited 0, so a database that restored to zero tables (the WAL bug) and a file
tree nobody had ever cross-checked both shipped green for months. The check
below is the one that would have caught it, and it runs on every backup.

Safe to run against a live system: every ingestion path writes the file before
inserting the row (upload.py:53 then :58, telegram.py:122 then :140, gmail.py:337
then :360) and the pipeline files before it records stored_filename
(pipeline.py:99 then :117). A row captured in the snapshot therefore always has
its bytes on disk already, so a document arriving mid-backup cannot make a good
backup look broken.
"""

import glob
import hashlib
import os
import sqlite3

# Reading every original costs about a second per 100MB. Cheap next to being
# wrong about whether the backup restores.
_HASH_CHUNK = 1 << 20


class BackupVerificationError(RuntimeError):
    """The artifact would not restore. Never raised for a merely empty install."""


def _sha256(path: str) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK), b""):
            sha.update(chunk)
    return sha.hexdigest()


def verify_backup(backup_dir: str, *, check_hashes: bool = True) -> dict:
    """Check a backup directory end to end. Raises BackupVerificationError.

    Returns a report so callers can log what was actually checked rather than
    just that nothing blew up.
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

        try:
            version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        except sqlite3.Error as e:
            # The exact shape the old file copy produced: a structurally valid
            # database with no tables in it.
            raise BackupVerificationError(f"database has no schema_version table: {e}") from e
        if version is None:
            raise BackupVerificationError("database has an empty schema_version table")

        rows = conn.execute(
            "SELECT id, file_hash, stored_filename FROM documents"
        ).fetchall()
    finally:
        conn.close()

    storage = os.path.join(backup_dir, "storage")
    missing: list[str] = []
    corrupt: list[str] = []
    checked_originals = 0
    checked_filed = 0

    for row in rows:
        file_hash = row["file_hash"]
        if file_hash:
            # Glob rather than rebuilding the extension from original_filename:
            # the hash is the filename stem, and the suffix varies by source.
            found = glob.glob(os.path.join(storage, "originals", f"{file_hash}.*"))
            if not found:
                missing.append(f"document {row['id']}: originals/{file_hash}.*")
            else:
                checked_originals += 1
                if check_hashes and _sha256(found[0]) != file_hash:
                    # The name IS the sha256 of the contents, so this catches a
                    # file copied while it was still being written.
                    corrupt.append(f"document {row['id']}: {os.path.basename(found[0])}")

        if row["stored_filename"]:
            filed = os.path.join(storage, "filed", row["stored_filename"])
            if not os.path.exists(filed):
                missing.append(f"document {row['id']}: filed/{row['stored_filename']}")
            else:
                checked_filed += 1

    if missing or corrupt:
        parts = []
        if missing:
            parts.append(f"{len(missing)} referenced file(s) absent: {'; '.join(missing[:5])}")
        if corrupt:
            parts.append(f"{len(corrupt)} file(s) do not match their hash: {'; '.join(corrupt[:5])}")
        raise BackupVerificationError(
            f"backup would restore with broken documents -- {' | '.join(parts)}"
        )

    return {
        "schema_version": version,
        "documents": len(rows),
        "originals_verified": checked_originals,
        "filed_verified": checked_filed,
        "hashes_checked": check_hashes,
    }
