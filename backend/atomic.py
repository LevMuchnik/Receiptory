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
"""

import logging
import os
import shutil
import tempfile

logger = logging.getLogger(__name__)

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
