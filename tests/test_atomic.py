"""Tests for backend/atomic.py.

Deliberately NOT written as a concurrency race. The obvious test -- copy a
large file in a thread while the main thread stats the destination and asserts
it is never partial -- passes against a completely non-atomic implementation
whenever the stat loop happens to miss the window. It can only fail flakily and
can never fail reliably, which makes it worse than no test.

What these assert instead is the mechanism: the destination name is never
opened for writing, a failed write leaves the old bytes exactly as they were,
and nothing is left behind either way.
"""

import os
import shutil
import stat

import pytest

from backend.atomic import (
    TMP_PREFIX,
    atomic_copy,
    atomic_write_bytes,
    atomic_write_text,
)


def _leftovers(directory):
    return [n for n in os.listdir(directory) if n.startswith(TMP_PREFIX)]


def test_atomic_copy_places_the_whole_file(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"payload")
    dest = tmp_path / "dest.bin"

    assert atomic_copy(str(src), str(dest)) == str(dest)
    assert dest.read_bytes() == b"payload"
    assert _leftovers(tmp_path) == []


def test_atomic_copy_never_opens_the_destination_name_for_writing(tmp_path, monkeypatch):
    """The whole point: bytes go to a scratch name, then one rename.

    Asserting on os.replace's arguments is deterministic where a race is not.
    Reverting atomic_copy to a plain shutil.copy2 makes this fail every time.
    """
    src = tmp_path / "src.bin"
    src.write_bytes(b"x" * 4096)
    dest = tmp_path / "dest.bin"

    seen = []
    real_replace = os.replace
    monkeypatch.setattr(
        os, "replace", lambda a, b: (seen.append((a, b)), real_replace(a, b))[1]
    )

    atomic_copy(str(src), str(dest))

    assert len(seen) == 1, "exactly one rename should have happened"
    tmp_name, final = seen[0]
    assert final == str(dest)
    assert os.path.basename(tmp_name).startswith(TMP_PREFIX)
    assert os.path.dirname(tmp_name) == str(tmp_path), (
        "the scratch file must live in the DESTINATION directory, or the rename "
        "crosses a filesystem and stops being atomic"
    )


def test_atomic_copy_leaves_the_destination_untouched_when_the_copy_fails(
    tmp_path, monkeypatch
):
    """A failure mid-copy must not damage what is already there."""
    dest = tmp_path / "dest.bin"
    dest.write_bytes(b"the good old bytes")
    src = tmp_path / "src.bin"
    src.write_bytes(b"new" * 1000)

    def die_partway(fsrc, fdst, length=0):
        fdst.write(b"half a f")
        raise OSError("disk went away")

    monkeypatch.setattr(shutil, "copyfileobj", die_partway)

    with pytest.raises(OSError):
        atomic_copy(str(src), str(dest))

    assert dest.read_bytes() == b"the good old bytes"
    assert _leftovers(tmp_path) == [], "the scratch file must be cleaned up"


def test_atomic_copy_carries_mode_and_mtime_like_copy2(tmp_path):
    """mkstemp creates 0600. copy2 carried the source's mode across, and the
    live storage tree is a mix of 0600 and 0644, so losing this would silently
    re-permission every file the app rewrites."""
    src = tmp_path / "src.bin"
    src.write_bytes(b"payload")
    os.chmod(src, 0o640)
    os.utime(src, (1_000_000, 1_000_000))
    dest = tmp_path / "dest.bin"

    atomic_copy(str(src), str(dest))

    assert stat.S_IMODE(os.stat(dest).st_mode) == 0o640
    assert int(os.stat(dest).st_mtime) == 1_000_000


def test_atomic_copy_repairs_a_destination_that_already_exists(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"replacement")
    dest = tmp_path / "dest.bin"
    dest.write_bytes(b"stale")

    atomic_copy(str(src), str(dest))

    assert dest.read_bytes() == b"replacement"
    assert _leftovers(tmp_path) == []


def test_atomic_write_bytes_leaves_the_original_intact_on_failure(tmp_path, monkeypatch):
    """This is the rclone.conf case that motivated the helper.

    open(path, "w") truncates before it writes, so a crash in that window empties
    the file and takes every configured remote with it.
    """
    path = tmp_path / "rclone.conf"
    path.write_text("[receiptory_onedrive]\ntype = onedrive\n")

    real_fdopen = os.fdopen

    def die_after_open(fd, mode="r", *a, **k):
        f = real_fdopen(fd, mode, *a, **k)
        original_write = f.write

        def exploding_write(data):
            original_write(data[: len(data) // 2])
            raise OSError("power cut")

        f.write = exploding_write
        return f

    monkeypatch.setattr(os, "fdopen", die_after_open)

    with pytest.raises(OSError):
        atomic_write_text(str(path), "[receiptory_gdrive]\ntype = drive\n")

    assert path.read_text() == "[receiptory_onedrive]\ntype = onedrive\n"
    assert _leftovers(tmp_path) == []


def test_atomic_write_bytes_keeps_the_existing_mode_on_a_rewrite(tmp_path):
    """Rewriting a file must not re-permission it. mkstemp would make it 0600.

    The existing mode here is deliberately 0o640 and not 0o644: the umask
    default is 0o666 & ~umask, which on the usual umask 022 IS 0o644, so a test
    written against 0o644 passes even when the code ignores the existing mode
    entirely. Mutation caught exactly that.
    """
    import backend.atomic as atomic_mod

    path = tmp_path / "conf"
    path.write_bytes(b"old")
    os.chmod(path, 0o640)
    assert 0o640 != atomic_mod._DEFAULT_MODE, "pick a mode the default cannot mask"

    atomic_write_bytes(str(path), b"new")

    assert path.read_bytes() == b"new"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o640


def test_atomic_write_bytes_honours_an_explicit_mode(tmp_path):
    path = tmp_path / "secret.conf"
    atomic_write_text(str(path), "token = hunter2\n", mode=0o600)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_atomic_write_bytes_on_a_new_file_matches_a_plain_open(tmp_path):
    """A new file should land with the permissions open(path,"wb") would give,
    not mkstemp's 0600."""
    reference = tmp_path / "reference"
    with open(reference, "wb") as f:
        f.write(b"x")

    written = tmp_path / "written"
    atomic_write_bytes(str(written), b"x")

    assert stat.S_IMODE(os.stat(written).st_mode) == stat.S_IMODE(
        os.stat(reference).st_mode
    )


def test_a_directory_fsync_failure_does_not_fail_the_write(tmp_path, monkeypatch):
    """Not every platform can fsync a directory fd. Losing that costs durability
    across a power cut, never correctness, so it must not raise."""
    import backend.atomic as atomic_mod

    monkeypatch.setattr(
        atomic_mod.os, "fsync", _raise_on_dir_fd(atomic_mod.os.fsync)
    )

    path = tmp_path / "f.bin"
    atomic_write_bytes(str(path), b"payload")
    assert path.read_bytes() == b"payload"


def _raise_on_dir_fd(real_fsync):
    def fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("fsync on a directory is not supported here")
        return real_fsync(fd)

    return fsync
