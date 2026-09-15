"""One-off cleanup for the filed/ orphans issue #54 left behind.

RUN THIS ON THE HOST, NOT IN THE CONTAINER. The Dockerfile copies only
backend/, migrations/ and frontend/dist -- /app/scripts does not exist in the
running image. Run it from a checkout with `uv sync`:

    uv run python scripts/cleanup_filed_orphans.py --data-dir /mnt/user/appdata/Receiptory/data
    uv run python scripts/cleanup_filed_orphans.py --data-dir ... --apply

Default is a dry run. Nothing is touched without --apply.

STOP RECEIPTORY BEFORE --apply. This is a hard precondition, not advice, and
--apply refuses to run while the app is up (it checks for receiptory.db-shm,
the same signal scripts/restore_backup.py uses). Two independent reasons:

  * The app's STORAGE_MUTATION_LOCK cannot help here. It is a threading.Lock,
    so it only excludes threads inside ONE process; this script is a separate
    process on the host. Taking it here would exclude nobody while reading like
    a guarantee. If --apply ran during the 02:00 backup, the deletes would land
    inside build_backup's copytree, whose retry is built for a rename window and
    cannot survive a delete, and the whole run would be discarded.
  * A live app commits through the write-ahead log. This script must see those
    commits to know what is still referenced -- see the connection below.

WHAT IT DOES, AND WHY THE TWO CASES DIFFER
------------------------------------------
An orphan is a file in storage/filed/ that no documents row names. Since
#54 landed, a reprocess that renames a document removes the superseded copy
itself, so this script is for the ones that accumulated before that.

    DUPLICATE  the orphan is byte-identical to a file that is still
               referenced -> deleted. Nothing is lost: the identical bytes stay
               on disk under the current name, and originals/ holds the source.

    UNMATCHED  no referenced file has the same contents -> MOVED to
               <data_dir>/storage/quarantine/, never deleted. On this install
               that is 0000-00-00-000000-e4b519a0.pdf, whose document row does
               not exist in any state -- and the codebase has no path that can
               produce that (there is no DELETE FROM documents anywhere;
               delete_document only sets is_deleted). See issue #64.

               INSIDE storage/, deliberately. It is a BACKUP_TREE and not an
               EXCLUDED_STORAGE_DIRS subtree, so the quarantined file keeps
               riding in every backup -- and this is by definition the one file
               the script could NOT prove is reproducible from anything else.
               An earlier revision put it in <data_dir>/quarantine/, outside
               every backup tree AND every restore source, which gave the least
               reproducible artifact the thinnest durability in the install.
               verify_backup's orphan scan reads storage/filed only, so a
               sibling directory does not put the count back above zero.

Every orphan is hashed against its candidate before anything happens, and the
verdict is printed. The `converted/` lesson of 2026-09-12 is why: a pair that
looks identical is not identical until it has been hashed.
"""

import argparse
import glob
import hashlib
import os
import shutil
import re
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.atomic import is_contained_name
from backend.storage import get_filed_path

# The last field of a filed/ name is file_hash[:8] (processing/filing.py).
_HASH8_RE = re.compile(r"[0-9a-f]{8}")


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_referenced(db_path: str) -> set[str]:
    """Every stored_filename in the database, read from a COPY.

    A COPY, so this script never writes a single byte into data_dir. The obvious
    alternative -- open the live database read-only -- creates
    receiptory.db-shm as a side effect and leaves it behind, and then "clean up
    the -shm we made" is unsafe in its own right: Receiptory opens a connection
    per operation, so an app that was idle when we sampled can wake during our
    read and leave its OWN live -shm, which we would then unlink out from under
    an open connection. Deleting a live WAL index splits SQLite's lock domain
    and is a documented route to corruption.

    The -wal is copied too, and NOT immutable=1: a committed-but-not-yet-
    checkpointed rename lives in the WAL, and missing it would make this script
    think a live file is unreferenced. Measured: immutable=1 sees only
    checkpointed rows.
    """
    with tempfile.TemporaryDirectory(prefix="receiptory_cleanup_") as td:
        tmp_db = os.path.join(td, "receiptory.db")
        shutil.copy2(db_path, tmp_db)
        for suffix in ("-wal", "-shm"):
            if os.path.exists(db_path + suffix):
                shutil.copy2(db_path + suffix, tmp_db + suffix)
        conn = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True)
        try:
            # No is_deleted filter on purpose: a soft-deleted row still
            # references its file and is not an orphan.
            return {
                r[0] for r in conn.execute(
                    "SELECT stored_filename FROM documents "
                    "WHERE stored_filename IS NOT NULL"
                )
            }
        finally:
            conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", required=True, help="the Receiptory data directory")
    ap.add_argument("--apply", action="store_true", help="actually delete and move (default: dry run)")
    args = ap.parse_args()

    data_dir = os.path.abspath(args.data_dir)
    filed_dir = os.path.join(data_dir, "storage", "filed")
    db_path = os.path.join(data_dir, "receiptory.db")

    for p in (filed_dir, db_path):
        if not os.path.exists(p):
            print(f"ERROR: {p} does not exist. Is --data-dir right?", file=sys.stderr)
            return 2

    # Liveness check FIRST, before opening anything.
    #
    # Ordering is load-bearing: a read-only connect to a WAL database CREATES
    # receiptory.db-shm and leaves it behind. Checking after connecting would
    # therefore always find the file this script just made, and --apply could
    # never run. Measured on this install.
    #
    # Honest about what this proves: Receiptory opens a connection per operation
    # and closes it, so -shm is present only while a query is actually in
    # flight, not for the whole time the app is up. This catches the app
    # mid-write; it is NOT a guarantee the app is stopped. That is why the
    # docstring makes stopping the container the precondition rather than
    # relying on this.
    shm = os.path.join(data_dir, "receiptory.db-shm")
    shm_existed = os.path.exists(shm)
    if args.apply and shm_existed:
        print(
            f"\nREFUSING to modify anything: {shm} exists, so Receiptory is "
            f"mid-operation.\nStop the container first:  docker compose stop receiptory",
            file=sys.stderr,
        )
        return 2

    referenced = _read_referenced(db_path)

    # Files only, matching backup/verify.py's orphan scan exactly. A directory
    # is never a filed copy, and letting one through means _sha256 raises
    # IsADirectoryError and aborts the whole run after printing a count.
    on_disk = {e.name for e in os.scandir(filed_dir) if e.is_file()}

    # Compare BASENAMES. `referenced` holds raw stored_filename values, and a
    # row that is not a plain filename ("filed/x.pdf", or an absolute path)
    # would otherwise fail to match its own file on disk -- so that LIVE file
    # would land in the orphan set and be quarantined or deleted. Those rows are
    # collected separately below and block --apply, because this script cannot
    # prove where they point.
    non_contained = sorted(n for n in referenced if not is_contained_name(n))
    referenced_names = {os.path.basename(n) for n in referenced}
    orphans = sorted(on_disk - referenced_names)

    print(f"referenced by a row: {len(referenced)}")
    print(f"files in filed/:     {len(on_disk)}")
    print(f"orphans:             {len(orphans)}")
    if not orphans:
        print("\nNothing to do.")
        return 0

    # Hash the referenced files once, so each orphan can be matched by content
    # rather than by a filename that merely looks similar.
    by_hash: dict[str, str] = {}
    for name in referenced:
        if not is_contained_name(name):
            continue
        # Through the resolver, not a bare join: an absolute stored_filename
        # makes os.path.join discard the prefix, and this script runs as root.
        p = get_filed_path(name, data_dir)
        if os.path.exists(p):
            by_hash.setdefault(_sha256(p), name)
    if non_contained:
        print(f"\nWARNING: {len(non_contained)} row(s) hold a stored_filename "
              f"that is not a plain filename: {non_contained[:5]}")
        print("  --apply is blocked: their files cannot be told apart from orphans.")

    # Inside storage/, which IS a BACKUP_TREE, and not in
    # EXCLUDED_STORAGE_DIRS. The previous location (data_dir/quarantine) was
    # outside every backup tree AND every restore source, so the one file this
    # script cannot prove is reproducible was also the one file with no copy
    # anywhere -- and restore_backup.py tells the operator to delete the
    # directory that would have held it. verify_backup's orphan scan reads
    # storage/filed only, so a sibling directory does not affect the count.
    quarantine = os.path.join(data_dir, "storage", "quarantine")
    to_delete: list[tuple[str, str]] = []
    to_quarantine: list[str] = []

    print()
    for orphan in orphans:
        path = os.path.join(filed_dir, orphan)
        digest = _sha256(path)
        size_kb = os.path.getsize(path) // 1024
        twin = by_hash.get(digest)
        # The filename's last field is the first 8 hex of the document's
        # file_hash, so this answers "is it regenerable?" directly. The
        # docstring justified deleting duplicates by saying originals/ holds the
        # source; asserting that in prose and not checking it before an
        # irreversible unlink is not good enough.
        prefix = orphan.rsplit("-", 1)[-1].split(".")[0]
        # Validated, because an unvalidated parse is worse than none: a name
        # ending "-.pdf" yields "" and glob(originals/ + "*") then matches
        # EVERY original, so "the original is present" passes vacuously and
        # the file is deleted. Weakest exactly on files this app did not write.
        if _HASH8_RE.fullmatch(prefix):
            original = glob.glob(
                os.path.join(data_dir, "storage", "originals", prefix + "*"))
            # Hashed, not just named. The docstring's own rule is that a pair is
            # not identical until it has been hashed -- and originals/<hash> IS
            # its own sha256, so a truncated file left by a crashed copy has the
            # right NAME and the wrong bytes. Globbing alone would let it
            # "justify" an irreversible delete.
            original = [
                o for o in original
                if os.path.basename(o).split(".")[0] == _sha256(o)
            ]
        else:
            original = []
        if twin and original:
            print(f"  DUPLICATE  {orphan}  ({size_kb} KB)")
            print(f"             sha256 {digest[:16]}... identical to {twin}")
            print(f"             original present: {os.path.basename(original[0])}")
            to_delete.append((orphan, twin))
        elif twin:
            print(f"  KEEP       {orphan}  ({size_kb} KB)")
            print(f"             identical to {twin}, but NO originals/{prefix}* on disk")
            print(f"             -> quarantined instead of deleted")
            to_quarantine.append(orphan)
        else:
            print(f"  UNMATCHED  {orphan}  ({size_kb} KB)")
            print(f"             sha256 {digest[:16]}... no referenced file has these bytes")
            print(f"             original: {os.path.basename(original[0]) if original else 'ABSENT'}")
            to_quarantine.append(orphan)

    print()
    print(f"would delete    {len(to_delete)} duplicate(s)")
    print(f"would move      {len(to_quarantine)} file(s) to {quarantine}")

    if args.apply and non_contained:
        print("\nREFUSING: a row holds a stored_filename that is not a plain "
              "filename, so its file cannot be distinguished from an orphan.",
              file=sys.stderr)
        return 2

    if not args.apply:
        print("\nDry run. Re-run with --apply to make these changes.")
        return 0

    # Re-read, immediately before the first delete. The plan above was built
    # from a snapshot taken before hashing every referenced file in filed/
    # (~160MB on a real install), so it is minutes old by now. A save_filed in
    # that window produces a file that is LIVE but absent from the stale
    # snapshot -- which the plan has already classified as an orphan. Deleting
    # it leaves a row pointing at nothing, the one outcome this whole change
    # treats as unacceptable. scripts/restore_backup.py learned this first:
    # "Re-check immediately before the swap. The first check was minutes ago."
    if os.path.exists(shm):
        print(f"\nREFUSING: {shm} appeared during the scan, so Receiptory "
              f"started. Nothing was changed.", file=sys.stderr)
        return 2
    fresh = _read_referenced(db_path)
    now_live = {o for o, _ in to_delete} | set(to_quarantine)
    raced = sorted(n for n in now_live if os.path.basename(n) in
                   {os.path.basename(r) for r in fresh})
    if raced:
        print(f"\nREFUSING: {len(raced)} planned victim(s) became referenced "
              f"during the scan: {raced[:5]}\nRe-run to recompute.",
              file=sys.stderr)
        return 2

    print()
    for orphan, twin in to_delete:
        os.unlink(os.path.join(filed_dir, orphan))
        print(f"  deleted  {orphan}  (identical copy {twin} kept)")

    if to_quarantine:
        os.makedirs(quarantine, exist_ok=True)
        for orphan in to_quarantine:
            dest = os.path.join(quarantine, orphan)
            if os.path.exists(dest):
                # shutil.move is os.rename on one filesystem, which clobbers
                # silently. A quarantined file has no copy in any backup tree,
                # so overwriting one destroys the only surviving artifact --
                # exactly what quarantining instead of deleting exists to avoid.
                print(f"  SKIPPED  {orphan}: {dest} already exists, refusing to "
                      f"overwrite the earlier artifact")
                continue
            shutil.move(os.path.join(filed_dir, orphan), dest)
            print(f"  moved    {orphan}  ->  {dest}")

    remaining = len([e for e in os.scandir(filed_dir) if e.is_file()])
    print(f"\nDone. filed/ now holds {remaining} files.")
    print("The next backup's verification line should read 0 unreferenced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
