"""Write a file so that no reader can ever observe it half-written.

Every writer in this project used to write straight to the final name --
`shutil.copy2(src, dest)` in storage.py, `open(conf, "w")` in
backup/cloud_auth.py -- which means a concurrent reader can open the
destination and get whatever bytes have landed so far. That reader is not
hypothetical: `backup/runner.py` walks the whole storage tree with
`shutil.copytree` while the processing queue is still writing into it, and
since PR #48 `backup/verify.py` hashes every original and compares it to its
own filename, so a file copied mid-write is reported as damage against a
document that is actually fine.

The fix is the standard one: write to a scratch name in the SAME directory,
flush it, then rename it onto the target. The rename is a single operation, so
a reader sees the whole old file or the whole new one.

Two things about this that are specific to where Receiptory runs.

**The scratch name comes from mkstemp, not from the pid.** Backups run in an
executor thread while the processing queue runs on the event loop, and
POST /api/documents/{id}/reprocess can fire while a queue retry is already
running. Same process, same pid, two threads, one destination -- a
`<dest>.part-<pid>` name would have both threads writing the same scratch file.
`mkstemp` takes uniqueness from the OS instead.

**The rename is atomic for content but not for existence, on this filesystem.**
Measured on the Unraid shfs (fuse) mount that holds the data directory: over
46,337 reads racing 3,174 replaces, a concurrent opener hit ENOENT 118 times
and saw a torn file zero times. The same probe on btrfs: 0 ENOENT in 172,022
reads. So `os.replace` passes rename(2) through to the array, but fuse still
lets a racing lookup miss the entry. A reader therefore gets complete-old,
complete-new, or nothing -- never garbage. Callers that walk a live tree have
to tolerate that ENOENT; `backup/runner.py` does it with a retrying
copy_function.

The directory fsync after the rename is deliberate and not decoration. The
file fsync makes the CONTENT durable; only the directory fsync makes the
RENAME durable, which is what the crash-safety argument for rclone.conf
actually rests on.

This module is also where two things live that are not about writing at all:
`is_contained_name` and `STORAGE_MUTATION_LOCK`. Both are here for the same
reason -- they are needed on both sides of a boundary whose two halves must not
import each other, and `backup/verify.py` has to keep working without PyMuPDF.
This file imports nothing but the standard library, which is what makes it the
only place both sides can already reach. See each one's own comment.
"""

import logging
import os
import shutil
import tempfile
import threading

logger = logging.getLogger(__name__)

# Held by anything that REMOVES a file from the storage tree, and by
# backup/runner.py across its snapshot and its copytree.
#
# build_backup snapshots the database first and then walks storage/ while the
# app keeps running. Its safety argument (runner.py, "Snapshot the database
# FIRST") has two clauses: every row in the snapshot already had its bytes on
# disk, AND those bytes are still there when the copy runs. The second clause
# was free for as long as nothing in this codebase ever deleted from storage/ --
# there was no such code until issue #54 added one.
#
# Without this lock a reprocess that drops a stale filed/ copy breaks the backup
# two different ways:
#   * between the snapshot and the walk -> the snapshot row names a file that is
#     gone, and verify_backup reports damage on a completely healthy install
#   * during the walk -> _copy_tolerating_rename retries once, which is built
#     for a rename window where the retry finds the settled file. For a genuine
#     delete the retry raises too, copytree collects it into shutil.Error at the
#     END of the walk, and build_backup's `except BaseException` throws the
#     half-built artifact away. One reprocess at 02:00 = no backup that night.
#
# It lives here rather than in storage.py because backup/runner.py needs it and
# must not import storage.py, which pulls fitz (PyMuPDF) onto the backup path,
# while storage.remove_filed needs the same object. (The sibling predicate below
# is here for a related but different reason -- see its docstring.)
#
# NOT REENTRANT. threading.Lock, not RLock: a second acquire on the same thread
# deadlocks the process. So nothing that holds it may call storage.remove_filed,
# which takes it itself -- build_backup unlinks nothing, and every other holder
# unlinks directly.
#
# THE RULE, not a list: anything that DELETES a path under a BACKUP_TREES
# directory (runner.py: storage, logs, scanner_test_set) takes this lock,
# unless that path is under an EXCLUDED_STORAGE_DIRS subtree (page_cache,
# tmp), which the backup never copies. Stated as a rule because the
# enumeration has been wrong twice: it first missed api/scanner.py's
# delete_test_frame, then missed ingestion/gmail.py, which unlinks the
# storage/converted/<stem>_converted.pdf that normalize writes -- a scratch
# _paired_scratch does NOT exclude, because excluding it requires a <stem>.pdf
# sibling that a temp-file stem never has. To check a candidate: resolve the
# path, not the variable name.
#
# Holders at the time of writing: backup/runner.py build_backup,
# storage.remove_filed, api/scanner.py delete_test_frame, and the two
# ingestion/gmail.py _render_first_page* paths.
#
# scripts/cleanup_filed_orphans.py deliberately does NOT take it: it is a
# separate OS process, so acquiring here would exclude nobody while reading as
# a guarantee. It enforces a stop-the-app precondition instead.
#
# Hold time is the whole backup assembly: snapshot_database (its own
# SNAPSHOT_TIMEOUT_S is 120s) plus a copytree of every backup tree. 2.7s was
# measured on a warm 314-document install; the worst case is minutes, and a
# reprocess or a frame delete waiting on it waits that long.
#
# A plain threading.Lock is the right primitive: both sides already run in
# executor threads (scheduler.py's run_in_executor(None, build_backup, ...) and
# queue.py's run_in_executor(None, process_document, ...)), so neither blocks an
# event loop. Contention is one backup assembly, measured at 2.7s on a
# 314-document install, against a reprocess that already waits seconds on a
# model call.
STORAGE_MUTATION_LOCK = threading.Lock()


def is_contained_name(name: str) -> bool:
    """True if `name` is a plain filename that cannot escape its directory.

    The single definition of what documents.stored_filename may hold. Both
    storage.get_filed_path (which guards a live os.unlink and the export zip)
    and backup/verify.py (which guards the restore path) call this, so the two
    cannot drift apart -- and two guards on one column that disagree are worse
    than one guard, because the disagreement only surfaces the night a backup
    refuses to verify.

    Deliberately stricter than the realpath containment used by
    get_scanner_test_frame_path. That one must accept a multi-segment relative
    path; this one must not, because generate_stored_filename cannot produce a
    separator (it fullmatches the date and regex-substitutes the id), so a name
    carrying one did not come from this application.
    """
    # "." and ".." pass the basename test -- os.path.basename("..") is ".." --
    # so they need naming explicitly. They were accepted by the original
    # verify.py predicate this replaces: os.path.join(filed_dir, "..") resolves
    # to storage/, which os.path.exists then confirms, so the export would try
    # to add a directory to the zip and the unlink would hit a directory. Not
    # exploitable, but not a filename either.
    # NUL and backslash are rejected for the same reason as "." and "..": this
    # docstring calls itself the single definition of what the column may hold,
    # and generate_stored_filename can emit neither. No exploit follows on Linux
    # (os.unlink raises ValueError on a NUL, which the caller already catches),
    # but rejecting them here saves every future caller from re-deriving that.
    return (
        bool(name)
        and name not in (os.curdir, os.pardir)
        and "\x00" not in name
        and "\\" not in name
        and not os.path.isabs(name)
        and os.path.basename(name) == name
    )

# Explicit, greppable, and distinct from the `tmp*_converted.pdf` scratch that
# normalize.py leaks into storage/converted. The backup's ignore rule drops
# these at any depth, so an in-flight write never rides into an archive, and a
# leftover from a SIGKILL can be found by name.
TMP_PREFIX = ".rcpt-tmp-"

# What a plain open(path, "wb") would have produced. Read once at import, while
# the process is still single-threaded: os.umask is a get-and-set, so calling it
# later would briefly clear the umask for every other thread.
_UMASK = os.umask(0)
os.umask(_UMASK)
_DEFAULT_MODE = 0o666 & ~_UMASK


def _fsync_dir(path: str) -> None:
    """Make a rename durable, best effort.

    Not every platform lets you open a directory for reading (Windows) and not
    every filesystem implements fsync on a directory fd. A failure here costs
    durability across a power cut, never correctness, so it is logged rather
    than raised -- the rename has already happened and the caller's file is in
    place either way.
    """
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError as e:
        logger.debug("Directory fsync unsupported on %s: %s", path, e)
    finally:
        os.close(fd)


def atomic_copy(src: str, dest: str) -> str:
    """Copy `src` onto `dest` without `dest` ever holding a partial file.

    A drop-in replacement for `shutil.copy2`, including its metadata contract:
    `copystat` runs against the scratch file before the rename, so the mode and
    mtime that copy2 used to carry across still land on the destination. That
    matters -- `mkstemp` creates 0600, and the live storage tree is a mix (233
    files at 0600 and 82 at 0644 in filed/ alone), so skipping copystat would
    silently re-permission every file the app rewrites from here on.
    """
    dest_dir = os.path.dirname(dest) or "."
    fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=TMP_PREFIX)
    try:
        with open(src, "rb") as fsrc, os.fdopen(fd, "wb") as fdst:
            shutil.copyfileobj(fsrc, fdst)
            fdst.flush()
            os.fsync(fdst.fileno())
        shutil.copystat(src, tmp)
        os.replace(tmp, dest)
    except BaseException:
        # BaseException, not Exception: a KeyboardInterrupt or a cancellation
        # between mkstemp and replace would otherwise leave the scratch file
        # behind under a name nothing ever cleans up.
        _unlink_quietly(tmp)
        raise
    _fsync_dir(dest_dir)
    return dest


def atomic_write_bytes(path: str, data: bytes, mode: int | None = None) -> str:
    """Write `data` to `path` without `path` ever holding a partial file.

    `mode` defaults to whatever the destination already has (a rewrite should
    not re-permission a file) and to the umask default when it is new, so
    replacing a plain `open(path, "wb")` changes nothing about permissions.
    """
    if mode is None:
        try:
            mode = os.stat(path).st_mode & 0o7777
        except OSError:
            mode = _DEFAULT_MODE

    dest_dir = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=TMP_PREFIX)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        _unlink_quietly(tmp)
        raise
    _fsync_dir(dest_dir)
    return path


def atomic_write_text(path: str, text: str, mode: int | None = None) -> str:
    """UTF-8 sibling of atomic_write_bytes, for config files."""
    return atomic_write_bytes(path, text.encode("utf-8"), mode=mode)


def _unlink_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
