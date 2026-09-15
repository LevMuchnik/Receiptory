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

**Unreferenced** is neither, and is counted rather than listed as a problem: a
file in filed/ that no row names. Nothing is missing and no document is at
risk, so calling it damage would report an alert every night on a healthy
install and cost the damage count the only meaning it has.

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

from backend.atomic import is_contained_name

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
    """True if `name` is a plain filename that cannot escape its directory.

    Thin alias. The definition lives in backend.atomic so that storage.py's
    get_filed_path -- which guards a live os.unlink and the export zip -- and
    this restore-path guard cannot drift apart. It is imported from atomic and
    not from storage because storage.py imports fitz (PyMuPDF) at module level,
    and this module is deliberately stdlib-only so verifying a backup does not
    need an image library installed.
    """
    return is_contained_name(name)


def _is_contained_relpath(path: str, root: str) -> bool:
    """True if `path` is a relative path that stays inside `root`.

    The multi-segment sibling of _is_contained_name, for
    scanner_test_frames.frame_path. That column stores a path RELATIVE to
    data_dir (storage.save_scanner_test_frame returns
    "scanner_test_set/<yyyy-mm-dd>/<ts>-<id>.jpg" so the rows survive a data_dir
    relocation), which means it is a multi-segment path out of an untrusted
    database joined straight onto a real directory.

    This matters more since scanner_test_set became a backup tree: those rows
    now arrive alongside files a restore puts on disk, and api/scanner.py hands
    the joined result to FileResponse and to os.unlink, as root, through
    storage.get_scanner_test_frame_path -- which is a bare os.path.join. An
    absolute frame_path discards the prefix exactly the way stored_filename
    "/etc/hostname" did before the documents guard existed.
    """
    if not path or os.path.isabs(path):
        return False
    parts = path.replace(os.sep, "/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        return False
    return len(parts) > 1 and parts[0] == root


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

        # Tolerated, not required: a backup taken before migration 006 has no
        # such table, and refusing to verify an older artifact would make this
        # check a denial of service on the restore path it exists to protect.
        try:
            frames = conn.execute(
                "SELECT id, frame_path FROM scanner_test_frames"
            ).fetchall()
        except sqlite3.Error:
            frames = []
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
    malformed += [
        f"scanner frame {r['id']}: frame_path"
        for r in frames
        if r["frame_path"] and not _is_contained_relpath(r["frame_path"], "scanner_test_set")
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

    # A floor under "damage". Per-document damage is reported rather than
    # raised so one lost file cannot stop every future backup -- but an artifact
    # where EVERY document lost its file is not a damaged backup, it is a
    # database with no storage tree. That is exactly what a run taken while
    # storage/ was unreadable produces, and it is indistinguishable from a good
    # backup downstream: the run completes, uploads, and
    # scheduler.apply_retention then purges the good backups standing behind it.
    # Structural, NOT statistical. "Every document is damaged" is the wrong
    # test: on an install holding one document, its one lost file would satisfy
    # it, and a fatal error there is precisely the denial-of-service this
    # module's two-grade split exists to prevent. The condition that actually
    # means "the tree did not arrive" is that the directory is not there at all,
    # which no amount of per-document damage can produce.
    if any(r["file_hash"] for r in rows) and not os.path.isdir(
        os.path.join(storage, "originals")
    ):
        raise BackupVerificationError(
            f"{len(rows)} document row(s) but no storage/originals directory at all. "
            f"The storage tree did not arrive; this is a database, not a backup."
        )

    # Files in filed/ that no row names. NOT damage, and deliberately not in
    # `problems`.
    #
    # `problems` means "this document's bytes are missing or do not match their
    # hash" and drives the damage notification. An orphan is the opposite fact:
    # nothing is missing, something is extra. Folding it in would report damage
    # every night on a completely healthy install, which is the #48
    # verifier-must-not-become-its-own-outage rule in its noise form -- and it
    # would make the damage count unable to answer the one question it exists
    # for, which is "am I losing data".
    #
    # Soft-deleted rows count as referencing: delete_document sets is_deleted
    # and never unlinks, so 70 of 314 files here belong to deleted documents and
    # are not orphans.
    #
    # No TMP_PREFIX filter. _ignore_regenerable drops those during copytree, so
    # a backup cannot contain one, and a branch that cannot execute is the dead
    # defensive code this review rejected elsewhere.
    referenced = {r["stored_filename"] for r in rows if r["stored_filename"]}
    filed_dir = os.path.join(storage, "filed")
    try:
        # Files only. os.listdir also returns directories, and a directory can
        # never be a filed copy, so counting one would pin the number above zero
        # permanently -- which costs the count the only thing it is good for,
        # namely that any non-zero value means something changed.
        on_disk = {e.name for e in os.scandir(filed_dir) if e.is_file()}
    except OSError:
        # No filed/ at all: an install that has never filed anything, or an
        # older backup. Zero orphans, not an error.
        on_disk = set()
    orphans = sorted(on_disk - referenced)

    return {
        "schema_version": version,
        "documents": len(rows),
        "originals_verified": originals_verified,
        "filed_verified": filed_verified,
        "filed_orphans": len(orphans),
        # Full list, like `problems`. Callers slice at display time
        # (runner.py, scheduler.py and restore_backup.py all do that for
        # problems), so len(orphan_names) cannot silently disagree with
        # filed_orphans the way a pre-truncated list would.
        "orphan_names": orphans,
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
    # Reported separately from damage, and only when non-zero, so a steady-state
    # install stays quiet and any number at all means something changed.
    if report.get("filed_orphans"):
        line += f", {report['filed_orphans']} unreferenced"
    if report["problems"]:
        line += f" -- {len(report['problems'])} damaged"
    return line
