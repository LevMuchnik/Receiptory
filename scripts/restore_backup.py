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
from backend.backup.runner import SNAPSHOT_REDACTED_KEYS, BACKUP_TREES  # noqa: E402
from backend.database import init_db  # noqa: E402


class RestoreRefused(RuntimeError):
    """The restore declined to run. Raised before anything is written."""


class TargetWritten(RuntimeError):
    """Raised after the target has been modified. The target is NOT usable."""

# Where each item in a restored data directory comes from.
#
# This replaced two tuples (BACKUP_TREES and CARRIED_FROM_TARGET) that could not
# express what was actually needed. scanner_test_set has to come from the backup
# when the backup has it and from the target when it does not, which meant
# putting one name in both tuples -- at which point "these two lists are
# disjoint" stops being true and "CARRIED_FROM_TARGET names everything that gets
# carried" stops being true. One table with an explicit policy per entry says
# the thing directly.
FROM_BACKUP = "from_backup"          # the backup is authoritative; absent is fine
FROM_TARGET = "from_target"          # never in a backup, must survive a replace
BACKUP_THEN_TARGET = "backup_then_target"  # prefer the backup, fall back

RESTORE_SOURCES = {
    # BACKUP_TREES is imported from the writer so the two cannot drift apart.
    # Anything build_backup starts writing is restored without a second edit.
    **{tree: FROM_BACKUP for tree in BACKUP_TREES},

    # rclone.conf holds the credentials for the remote the backup is uploaded
    # to, plus any hand-configured sftp/S3/local remote. It is deliberately NOT
    # in the backup -- the archive travels unencrypted to the very service
    # those credentials unlock. So it is carried from the machine instead, and
    # a restore onto fresh hardware genuinely has to reconfigure it.
    "rclone.conf": FROM_TARGET,
}

# scanner_test_set only started being backed up recently, so every backup made
# before that has no copy of it. Taking it from the backup alone would delete
# the 57 labelled frames off a machine that still has them, while the
# scanner_test_frames rows in the restored database go on pointing at them.
RESTORE_SOURCES["scanner_test_set"] = BACKUP_THEN_TARGET


def _is_occupied(path: str) -> bool:
    return os.path.isdir(path) and any(os.scandir(path))


def _place(src: str, dst: str) -> None:
    """Copy a file or a whole tree into the staging directory.

    dirs_exist_ok is belt and braces, not a live requirement: RESTORE_SOURCES is
    a dict so each name carries exactly one policy, the placement loop is an
    if/elif that writes each name at most once, and `staging` was created
    moments ago. If any of those ever stops holding, merging beats aborting
    mid-assembly with a traceback and an orphan staging directory.
    """
    if os.path.isdir(src):
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)


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
    # Read-only and immutable. Opening read-write would RECOVER a hot WAL and
    # checkpoint it away -- measured: a COUNT(*) alone took the file from 4096
    # to 8192 bytes and deleted the -wal. That file is the copy being kept as
    # the operator's undo; a courtesy message must never write to it.
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    except sqlite3.Error:
        return None
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


def _warn_about_credentials(target: str) -> None:
    """Say plainly that the restored install has no password until one is set.

    The snapshot is stripped of auth_password_hash, so the row is absent. At the
    next start config.init_settings finds no row and seeds bcrypt("admin") --
    the restored system is reachable over the LAN with admin/admin. The secret
    checklist says "auth_password_hash (was set)", which reads like reassurance.
    """
    conn = sqlite3.connect(
        f"file:{os.path.join(target, 'receiptory.db')}?mode=ro", uri=True
    )
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'auth_password_hash'"
        ).fetchone()
    except sqlite3.Error:
        row = None
    finally:
        conn.close()

    if row and row[0] and row[0] not in ('""', "''"):
        return
    print("\n" + "!" * 68)
    print("!  THE RESTORED SYSTEM HAS NO PASSWORD SET.")
    print("!  On first start it will accept the default login admin / admin.")
    print("!  Set a real password immediately, or pin RECEIPTORY_AUTH_PASSWORD")
    print("!  in .env before starting it.")
    print("!" * 68)


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

    # Resolve first. os.rename does NOT follow symlinks -- measured: renaming a
    # symlinked target moves the LINK, so `kept` would point at the live data
    # and "delete it once you are satisfied" would destroy the real install.
    target = os.path.realpath(target)

    if os.path.exists(target) and not os.path.isdir(target):
        raise RestoreRefused(f"{target} exists and is not a directory")

    if os.path.ismount(target):
        # A mount point cannot be renamed out of its parent (EBUSY), and the
        # documented deployment bind-mounts ./data into the container. Refuse
        # here rather than after copying the whole backup.
        raise RestoreRefused(
            f"{target} is a mount point, which cannot be swapped atomically.\n"
            f"Restore to a sibling path instead and move the contents in by hand:\n"
            f"  scripts/restore_backup.py {backup_dir} {target}.restored"
        )

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
            f"Or pass --force, which moves the current {target} aside and replaces it.\n"
            f"Nothing was written."
        )

    if occupied and _in_use(target):
        raise RestoreRefused(
            f"{target} is in use -- something has receiptory.db open.\n"
            f"Stop Receiptory first (docker compose stop receiptory), then re-run.\n"
            f"Restoring under a running app would let it flush cached pages into "
            f"the freshly restored database and destroy both copies.\n"
            f"Nothing was written.\n"
            f"If Receiptory is already stopped, this is a stale file left by a "
            f"crash -- remove {os.path.join(target, 'receiptory.db-shm')} and re-run."
        )

    # Say what replacing this target costs, before doing it. The old directory
    # is kept, so the peak requirement is the backup plus what is already there.
    # Second resolution alone collides between two runs, and the loser's
    # staging directory would be rmtree'd out from under it.
    stamp = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    staging = f"{target}.restoring-{stamp}"
    kept = f"{target}.pre-restore-{stamp}"

    needed = _dir_bytes(backup_dir)
    free = _free_bytes(os.path.dirname(os.path.abspath(target)) or ".")
    if free < needed:
        raise RestoreRefused(
            f"not enough space: the restored copy needs about "
            f"{needed // (1 << 20)} MB free beside the current directory and "
            f"{free // (1 << 20)} MB is available. (The current directory is kept "
            f"by renaming it, which costs nothing extra.)\nNothing was written."
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
    # Never rmtree a path this run did not create: with a colliding name that
    # would delete a concurrent restore's work in progress.
    os.makedirs(staging)

    db_dst = os.path.join(staging, "receiptory.db")
    print(f"Assembling restore in {staging} ...")
    # No sidecar handling needed: the staging directory was created moments ago,
    # so there is nothing stale for SQLite to replay. Assembling beside the
    # target rather than into it is what removed that hazard.
    shutil.copy2(os.path.join(backup_dir, "receiptory.db"), db_dst)

    # Named entries only, never a blind merge -- merging is what #50 removed.
    # Each entry states where it comes from, and the script says which source it
    # actually used, so a restore that silently fell back is visible rather than
    # discovered later.
    #
    # Note there is no `if occupied` gate here. There used to be one around the
    # carry step, which meant a restore onto a FRESH directory -- the actual
    # disaster-recovery case -- carried nothing at all. The existence checks
    # below are sufficient on their own: if the target is empty or absent, there
    # is nothing there to read.
    # Printed BEFORE each copy, not summarised after. Copying the storage tree
    # is the slowest part of a restore, and a summary printed afterwards means
    # minutes of silence during a disaster recovery. It also means an entry
    # found in NEITHER source prints nothing at all -- a backup whose storage/
    # tree was lost in transit would restore in silence and swap away the only
    # copy of every document.
    from_backup, from_target = [], []
    for name, policy in RESTORE_SOURCES.items():
        backup_src = os.path.join(backup_dir, name)
        target_src = os.path.join(target, name)

        if policy in (FROM_BACKUP, BACKUP_THEN_TARGET) and os.path.exists(backup_src):
            print(f"  {name}  <- the backup")
            _place(backup_src, os.path.join(staging, name))
            from_backup.append(name)
        elif policy in (FROM_TARGET, BACKUP_THEN_TARGET) and os.path.exists(target_src):
            print(f"  {name}  <- the current install")
            _place(target_src, os.path.join(staging, name))
            from_target.append(name)
        else:
            print(f"  {name}  -- NOT PRESENT in either source")

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

    # Re-check immediately before the swap. The first check was minutes ago,
    # before a full copy of the storage tree; docker's restart policy or an
    # impatient owner can start the app in that window. Renaming a directory
    # whose files are open succeeds on Linux, so the app would carry on writing
    # into the directory the operator is about to be told to delete.
    if occupied and _in_use(target):
        raise RestoreRefused(
            f"{target} became busy while the restore was being assembled -- "
            f"something opened receiptory.db.\nStop Receiptory and re-run. The "
            f"assembled restore is at {staging} and {target} is untouched."
        )

    # Printed BEFORE the swap, not after: between the two renames the data
    # directory does not exist, and a kill in that window would otherwise leave
    # the owner with no data/ and no idea where either copy went.
    if occupied:
        print(f"\nSwapping. If this is interrupted, recover with ONE of:")
        print(f"  mv {kept} {target}      # keep the old install")
        print(f"  mv {staging} {target}   # take the restored one")

    # Two renames, each atomic. The target is never a mixture of two installs.
    if occupied:
        try:
            os.rename(target, kept)
        except OSError as e:
            raise RestoreRefused(
                f"could not move {target} aside: {e}\n"
                f"Nothing was changed. The assembled restore is at {staging}."
            ) from e
    try:
        os.rename(staging, target)
    except OSError as e:
        if occupied:
            try:
                os.rename(kept, target)
            except OSError as rollback_err:
                raise TargetWritten(
                    f"could not put {target} back: {rollback_err}\n"
                    f"YOUR DATA IS AT {kept}\nThe restore attempt is at {staging}\n"
                    f"Recover with: mv {kept} {target}"
                ) from e
            raise RestoreRefused(
                f"could not move the restore into place: {e}\n"
                f"{target} was put back unchanged. The attempt is at {staging}."
            ) from e
        raise RestoreRefused(
            f"could not move the restore into place: {e}\n"
            f"The assembled restore is at {staging}."
        ) from e

    # The migrations ran against the staging path, which no longer exists.
    # Leave the process-global pointing somewhere real.
    init_db(os.path.join(target, "receiptory.db"))

    _warn_about_credentials(target)

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
        "there. Cloud backup also needs its OAuth remotes reconnected.\n"
        "\nrclone.conf is not part of the backup. The Google Drive and OneDrive\n"
        "remotes rebuild themselves from the stored tokens once you reconnect\n"
        "them, but a remote you added BY HAND (sftp, S3, a local path) lives in\n"
        "that file and nowhere else -- if it was not carried over from the\n"
        "install being replaced, it has to be reconfigured from scratch."
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
        # Deliberately no blanket "nothing was written": a refusal after the
        # restore was assembled leaves a staging directory on disk, and the
        # message itself names it.
        print(f"\n{e}", file=sys.stderr)
        return 2

    _print_secret_checklist(args.backup_dir)
    print("\nRestore complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
