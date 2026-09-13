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
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.backup.verify import verify_backup, BackupVerificationError  # noqa: E402
from backend.backup.runner import SNAPSHOT_REDACTED_KEYS  # noqa: E402

# Copied as-is. page_cache is 131MB of regenerable 200-DPI renders and tmp is
# scratch, so neither is required for a working install; they are restored only
# if the backup happens to carry them.
_TREES = ("storage", "logs")


def _is_occupied(path: str) -> bool:
    return os.path.isdir(path) and any(os.scandir(path))


def restore(backup_dir: str, target: str, *, force: bool = False) -> dict:
    """Rebuild `target` from `backup_dir`. Returns the verification report."""
    if not os.path.isdir(backup_dir):
        raise SystemExit(f"no such backup directory: {backup_dir}")

    print(f"Verifying {backup_dir} before touching anything ...")
    report = verify_backup(backup_dir)
    print(
        f"  ok: {report['documents']} documents, "
        f"{report['originals_verified']} originals hash-checked, "
        f"{report['filed_verified']} filed, schema {report['schema_version']}"
    )

    if _is_occupied(target) and not force:
        raise SystemExit(
            f"refusing to restore into non-empty {target}.\n"
            f"Restore beside it and swap, which is reversible:\n"
            f"  {sys.argv[0]} {backup_dir} {target}.restored\n"
            f"  mv {target} {target}.old && mv {target}.restored {target}\n"
            f"Or pass --force to overwrite it in place."
        )

    os.makedirs(target, exist_ok=True)

    db_src = os.path.join(backup_dir, "receiptory.db")
    db_dst = os.path.join(target, "receiptory.db")
    print(f"Restoring database -> {db_dst}")
    shutil.copy2(db_src, db_dst)
    # The snapshot carries no -wal/-shm and must not inherit stale ones from a
    # directory being overwritten, or SQLite would replay another install's tail.
    for sidecar in (db_dst + "-wal", db_dst + "-shm"):
        if os.path.exists(sidecar):
            os.remove(sidecar)

    for tree in _TREES:
        src = os.path.join(backup_dir, tree)
        if not os.path.isdir(src):
            continue
        dst = os.path.join(target, tree)
        print(f"Restoring {tree}/ -> {dst}")
        shutil.copytree(src, dst, dirs_exist_ok=True)

    # Bring an older snapshot forward. A backup taken before a schema change
    # restores at its own version, and the app expects the current one.
    print("Applying migrations ...")
    from backend.database import init_db

    init_db(db_dst)

    print("Verifying the restored directory ...")
    restored = verify_backup(target)
    print(
        f"  ok: {restored['documents']} documents, "
        f"{restored['originals_verified']} originals hash-checked, "
        f"{restored['filed_verified']} filed, schema {restored['schema_version']}"
    )
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
        help="overwrite the target in place even if it already holds data",
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
            print("\nThis backup would restore.")
            return 0

        if not args.target:
            parser.error("target is required unless --verify-only is given")

        restore(args.backup_dir, args.target, force=args.force)
    except BackupVerificationError as e:
        print(f"\nVERIFICATION FAILED: {e}", file=sys.stderr)
        print("Nothing was written." if not args.verify_only else "", file=sys.stderr)
        return 1

    _print_secret_checklist(args.backup_dir)
    print("\nRestore complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
