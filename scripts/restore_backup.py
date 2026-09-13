#!/usr/bin/env python3
"""Rebuild a working data directory from a Receiptory backup.

The inverse of backend.backup.runner.build_backup. Until this existed there was
no restore path at all -- no script, no written procedure, no test -- which is
why a backup whose database restored to zero tables shipped green for months.

    # is this backup any good?
    uv run python scripts/restore_backup.py --verify-only /path/to/backup

    # rebuild into a fresh directory (then swap it in)
    uv run python scripts/restore_backup.py /path/to/backup /path/to/new-data

    # overwrite an existing data directory in place
    uv run python scripts/restore_backup.py /path/to/backup data --force

Restoring writes over real documents, so a non-empty target is refused unless
--force is given. Without it the safe move is to restore beside the live
directory and swap, which is a reversible mv.

SECRETS ARE NOT IN THE BACKUP. The snapshot is stripped of API keys, tokens and
the login password before it leaves the machine, because it is uploaded to cloud
storage unencrypted. They must be re-entered after a restore; this script prints
exactly which ones and reads settings.json to show you what was set.
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.backup.verify import (  # noqa: E402
    verify_backup,
    format_report,
    BackupVerificationError,
)
from backend.backup.runner import SNAPSHOT_REDACTED_KEYS  # noqa: E402
from backend.database import init_db  # noqa: E402


class RestoreRefused(RuntimeError):
    """The restore declined to run. Raised before anything is written."""


class TargetWritten(RuntimeError):
    """Raised after the target has been modified. The target is NOT usable."""

# The trees build_backup writes, so the two cannot drift apart. Note that
# page_cache (regenerable page renders) and tmp (ingestion scratch) live INSIDE
# storage/, so they are carried into the backup and back out again wholesale --
# excluding them is tracked separately, not done here.
BACKUP_TREES = ("storage", "logs")


def _is_occupied(path: str) -> bool:
    return os.path.isdir(path) and any(os.scandir(path))


def _in_use(target: str) -> bool:
    """True if something has the target's database open.

    The -shm file exists for as long as any connection is open and SQLite
    deletes it on the last close, which makes it the reliable signal. Measured:
    BEGIN EXCLUSIVE from a second connection SUCCEEDS while the application is
    connected and idle, so a lock probe would have detected nothing.
    """
    return os.path.exists(os.path.join(target, "receiptory.db-shm"))


def _document_count(db_path: str) -> int | None:
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _free_bytes(path: str) -> int:
    while not os.path.exists(path):
        parent = os.path.dirname(os.path.abspath(path))
        if parent == path:
            break
        path = parent
    return shutil.disk_usage(path).free


def _dir_bytes(path: str) -> int:
    return sum(
        os.path.getsize(os.path.join(root, f))
        for root, _, files in os.walk(path)
        for f in files
        if not os.path.islink(os.path.join(root, f))
    )


def restore(backup_dir: str, target: str, *, force: bool = False) -> dict:
    """Rebuild `target` from `backup_dir`. Returns the verification report.

    The target is replaced, never merged, and never left half-written: the
    restore is assembled in a sibling directory, verified there, and only then
    swapped into place with two renames. Whatever was in the target is moved
    aside rather than deleted, so the whole operation is undone by a rename.
    """
    if not os.path.isdir(backup_dir):
        raise RestoreRefused(f"no such backup directory: {backup_dir}")

    print(f"Verifying {backup_dir} before touching anything ...")
    report = verify_backup(backup_dir)
    print(f"  ok: {format_report(report)}")
    if report["problems"]:
        print(
            f"  WARNING: {len(report['problems'])} document(s) are damaged in this "
            f"backup and will restore incomplete:"
        )
        for p in report["problems"][:10]:
            print(f"    - {p}")

    occupied = _is_occupied(target)
    if occupied and not force:
        # A literal path, not sys.argv[0]: restore() is also called in-process,
        # where argv[0] is pytest, and this is the one message an operator reads
        # under pressure.
        raise RestoreRefused(
            f"refusing to restore into non-empty {target}.\n"
            f"Restore beside it and swap, which is reversible:\n"
            f"  scripts/restore_backup.py {backup_dir} {target}.restored\n"
            f"  mv {target} {target}.old && mv {target}.restored {target}\n"
            f"Or pass --force, which moves the current {target} aside and replaces it."
        )

    if occupied and _in_use(target):
        raise RestoreRefused(
            f"{target} is in use -- something has receiptory.db open.\n"
            f"Stop Receiptory first (docker compose stop receiptory), then re-run.\n"
            f"Restoring under a running app would let it flush cached pages into "
            f"the freshly restored database and destroy both copies.\n"
            f"If Receiptory is already stopped, this is a stale file left by a "
            f"crash -- remove {os.path.join(target, 'receiptory.db-shm')} and re-run."
        )

    # Say what replacing this target costs, before doing it. The old directory
    # is kept, so the peak requirement is the backup plus what is already there.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    staging = f"{target}.restoring-{stamp}"
    kept = f"{target}.pre-restore-{stamp}"

    needed = _dir_bytes(backup_dir)
    free = _free_bytes(os.path.dirname(os.path.abspath(target)) or ".")
    if free < needed:
        raise RestoreRefused(
            f"not enough space: the restore needs {needed // (1 << 20)} MB and "
            f"{free // (1 << 20)} MB is free. The current data directory is kept "
            f"until you delete it, so both copies must fit."
        )

    if occupied:
        before = _document_count(os.path.join(target, "receiptory.db"))
        if before is not None and before > report["documents"]:
            print(
                f"\n  NOTE: {target} currently holds {before} documents and this "
                f"backup holds {report['documents']}. {before - report['documents']} "
                f"document(s) ingested since the backup will not be in the restored\n"
                f"  system. They are not destroyed -- the current directory is kept "
                f"at {kept}."
            )

    # Assemble beside the target, so an interruption leaves the live directory
    # untouched instead of a hybrid of two installs.
    if os.path.exists(staging):
        shutil.rmtree(staging)
    os.makedirs(staging)

    db_dst = os.path.join(staging, "receiptory.db")
    print(f"Assembling restore in {staging} ...")
    # No sidecar handling needed: the staging directory was created moments ago,
    # so there is nothing stale for SQLite to replay. Assembling beside the
    # target rather than into it is what removed that hazard.
    shutil.copy2(os.path.join(backup_dir, "receiptory.db"), db_dst)

    for tree in BACKUP_TREES:
        src = os.path.join(backup_dir, tree)
        if not os.path.isdir(src):
            continue
        print(f"  {tree}/")
        shutil.copytree(src, os.path.join(staging, tree))

    # Bring an older snapshot forward. A backup taken before a schema change
    # restores at its own version, and the app expects the current one.
    # NOTE: init_db sets the process-global _db_path in backend.database, so
    # calling restore() in-process repoints the whole application at the target.
    print("Applying migrations ...")
    init_db(db_dst)

    print("Verifying the assembled restore ...")
    try:
        restored = verify_backup(staging)
    except BackupVerificationError as e:
        # Still nothing has touched the target: the failure is contained to the
        # staging directory, which is left in place for inspection.
        raise RestoreRefused(
            f"{e}\nThe restore was assembled but did not verify, so {target} was "
            f"left untouched. The failed attempt is at {staging}."
        ) from e
    print(f"  ok: {format_report(restored)}")

    # Two renames on the same filesystem, both atomic. Between them the target
    # briefly does not exist; it is never a mixture of two installs.
    if occupied:
        os.rename(target, kept)
    elif os.path.isdir(target):
        os.rmdir(target)  # empty dir created by an earlier run or by the user
    try:
        os.rename(staging, target)
    except OSError:
        if occupied:
            os.rename(kept, target)  # put it back rather than leave nothing
        raise

    if occupied:
        print(f"\nThe previous data directory is kept at {kept}")
        print("Delete it once you are satisfied with the restore.")
    return restored


def _secret_state(saved: dict | None, key: str) -> str:
    """What the backup can honestly say about a stripped secret.

    Three states, not two. settings.json comes from get_all_settings_masked,
    which only covers keys in DEFAULTS -- llm_api_keys is DB-only and bypasses
    it, so it never appears there whether or not it was set. Reporting that as
    "not set" tells someone mid-recovery they had no API keys when they did.
    """
    if saved is None or key not in saved:
        return "unknown, the backup does not record it"
    return "was set" if saved.get(key) else "not set at backup time"


def _print_secret_checklist(backup_dir: str) -> None:
    settings_path = os.path.join(backup_dir, "settings.json")
    saved = None
    if os.path.exists(settings_path):
        try:
            with open(settings_path) as f:
                saved = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass

    print("\nSecrets are NOT in the backup and must be re-entered:")
    for key in sorted(SNAPSHOT_REDACTED_KEYS):
        print(f"  - {key}  ({_secret_state(saved, key)})")
    print(
        "\nSet them in Administration > Settings, or in .env for anything you pin\n"
        "there. Cloud backup also needs its OAuth remotes reconnected, and\n"
        "rclone.conf is not part of the backup."
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild a Receiptory data directory from a backup.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("backup_dir", help="a backup directory (contains receiptory.db)")
    parser.add_argument("target", nargs="?", help="data directory to create or overwrite")
    parser.add_argument(
        "--force", action="store_true",
        help=("replace the target even if it already holds data. The current "
              "directory is moved to <target>.pre-restore-<timestamp>, not deleted; "
              "documents ingested since the backup will not be in the restored system"),
    )
    parser.add_argument(
        "--verify-only", action="store_true",
        help="check that the backup would restore, then exit without writing anything",
    )
    args = parser.parse_args()

    try:
        if args.verify_only:
            report = verify_backup(args.backup_dir)
            print(json.dumps(report, indent=2))
            if report["problems"]:
                print(
                    f"\nThis backup would restore, but {len(report['problems'])} "
                    f"document(s) are damaged and would come back incomplete."
                )
            else:
                print("\nThis backup would restore.")
            return 0

        if not args.target:
            parser.error("target is required unless --verify-only is given")

        restore(args.backup_dir, args.target, force=args.force)
    except TargetWritten as e:
        print(f"\nRESTORE FAILED AFTER WRITING: {e}", file=sys.stderr)
        return 1
    except BackupVerificationError as e:
        print(f"\nVERIFICATION FAILED: {e}", file=sys.stderr)
        print("Nothing was written.", file=sys.stderr)
        return 1
    except RestoreRefused as e:
        print(f"\n{e}", file=sys.stderr)
        print("Nothing was written.", file=sys.stderr)
        return 2

    _print_secret_checklist(args.backup_dir)
    print("\nRestore complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
