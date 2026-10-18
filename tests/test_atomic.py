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
    """0o640, not 0o600 -- mkstemp CREATES the file at 0600, so asserting 0600
    here is true by construction: the chmod could be skipped entirely and the
    assertion would still hold. Same trap as the umask default two tests up,
    and it survived the first mutation battery for exactly that reason."""
    import backend.atomic as atomic_mod

    assert 0o640 not in (0o600, atomic_mod._DEFAULT_MODE), "pick a mode nothing else produces"
    path = tmp_path / "secret.conf"
    atomic_write_text(str(path), "token = hunter2\n", mode=0o640)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o640


def test_an_explicit_mode_beats_the_existing_file_mode(tmp_path):
    """The mode argument must win over the inherit-from-destination branch."""
    path = tmp_path / "secret.conf"
    path.write_bytes(b"old")
    os.chmod(path, 0o666)

    atomic_write_text(str(path), "token\n", mode=0o640)

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o640


def test_both_the_file_and_its_directory_are_fsynced(tmp_path, monkeypatch):
    """Asserts the syscalls, not their absence.

    test_a_directory_fsync_failure_does_not_fail_the_write only proves a FAILING
    fsync does not raise -- it passes just as happily when nothing is fsynced at
    all. Both fsyncs could be deleted and every other test stayed green. The
    file fsync makes the CONTENT durable; only the directory fsync makes the
    RENAME durable, which is what the crash-safety case for rclone.conf rests on.
    """
    import backend.atomic as atomic_mod

    kinds = []
    real = atomic_mod.os.fsync

    def spy(fd):
        kinds.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        return real(fd)

    monkeypatch.setattr(atomic_mod.os, "fsync", spy)
    atomic_write_bytes(str(tmp_path / "f.bin"), b"payload")

    assert "file" in kinds, "the content was never made durable"
    assert "dir" in kinds, "the rename was never made durable"


def test_atomic_copy_fsyncs_both_as_well(tmp_path, monkeypatch):
    import backend.atomic as atomic_mod

    src = tmp_path / "src.bin"
    src.write_bytes(b"payload")
    kinds = []
    real = atomic_mod.os.fsync
    monkeypatch.setattr(
        atomic_mod.os, "fsync",
        lambda fd: (kinds.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"), real(fd))[1],
    )

    atomic_copy(str(src), str(tmp_path / "dest.bin"))

    assert "file" in kinds and "dir" in kinds


@pytest.mark.parametrize("boom", [KeyboardInterrupt, __import__("asyncio").CancelledError])
def test_an_interrupt_does_not_orphan_the_scratch_file(boom, tmp_path, monkeypatch):
    """`except BaseException` is documented as deliberate but was untested:
    narrowing both handlers to `except Exception` left every test green, because
    both cleanup tests raise OSError. The orphan this creates is a .rcpt-tmp-*
    that the backup ignore rule then hides forever."""
    src = tmp_path / "src.bin"
    src.write_bytes(b"payload")

    def die(fsrc, fdst, length=0):
        raise boom()

    monkeypatch.setattr(shutil, "copyfileobj", die)
    with pytest.raises(boom):
        atomic_copy(str(src), str(tmp_path / "dest.bin"))
    assert _leftovers(tmp_path) == [], "an interrupt left a scratch file behind"

    def die_open(fd, mode="r", *a, **k):
        os.close(fd)
        raise boom()

    monkeypatch.setattr(os, "fdopen", die_open)
    with pytest.raises(boom):
        atomic_write_bytes(str(tmp_path / "other.bin"), b"x")
    assert _leftovers(tmp_path) == []


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


# ---------------------------------------------------------------------------
# is_contained_name / STORAGE_MUTATION_LOCK -- issue #54
#
# Both live here rather than in storage.py because backup/verify.py must import
# them and storage.py imports fitz (PyMuPDF) at module level, which would put an
# image library on the restore path.
# ---------------------------------------------------------------------------


def test_is_contained_name_accepts_only_plain_filenames():
    from backend.atomic import is_contained_name

    assert is_contained_name("2026-01-01-INV1-c0ffee12.pdf")
    assert is_contained_name("no-extension")

    # NUL and backslash are mutation-proven gaps: deleting both clauses from
    # is_contained_name left this file and test_storage.py at 36/36 green.
    for bad in ("", "/etc/shadow", "../x.pdf", "sub/dir.pdf", "a/../b.pdf", ".", "..",
                "a\x00b.pdf", "sub\\dir.pdf", "..\\..\\etc\\shadow"):
        assert not is_contained_name(bad), f"{bad!r} was accepted"


def test_this_module_stays_importable_without_pymupdf():
    """The reason the predicate is here and not in storage.py.

    backup/verify.py imports it, and verify.py is what runs on the restore path
    -- the one place you least want an optional native dependency. storage.py
    imports fitz at module level, so importing the predicate from there would
    make verifying a backup require PyMuPDF.

    A SUBPROCESS, deliberately. The in-process version of this test was inert:
    it blocked fitz and evicted backend.atomic and backend.backup.verify, but
    left backend.storage cached -- and test collection imports backend.storage
    long before this file runs. Measured: with verify.py changed to
    `from backend.storage import is_contained_name` (the exact inversion this
    test exists to catch), `pytest tests/test_atomic.py` failed while
    `pytest tests/test_export.py tests/test_atomic.py` passed 31/31. Any earlier
    test, any -k selection, any new file sorting first would disarm it, which is
    the same true-by-construction trap this suite keeps finding.

    A fresh interpreter has no warm cache to hide behind.
    """
    import subprocess
    import sys

    code = (
        "import sys; sys.modules['fitz'] = None; "
        "import backend.backup.verify as v; "
        "assert v._is_contained_name('a.pdf'); "
        "assert v.find_original('/nonexistent', '0' * 64) is None"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert r.returncode == 0, (
        "backup/verify.py no longer imports without PyMuPDF -- the restore path "
        f"just grew a dependency on an image library:\n{r.stderr}"
    )


def test_the_storage_mutation_lock_is_a_real_lock():
    """Guards the first code in the project that deletes from storage/, against
    build_backup's copytree. A stand-in that does not actually exclude would
    make every test that asserts `.locked()` pass while protecting nothing."""
    import threading

    from backend.atomic import STORAGE_MUTATION_LOCK

    assert isinstance(STORAGE_MUTATION_LOCK, type(threading.Lock()))
    assert not STORAGE_MUTATION_LOCK.locked()
    with STORAGE_MUTATION_LOCK:
        assert STORAGE_MUTATION_LOCK.locked()
        assert not STORAGE_MUTATION_LOCK.acquire(blocking=False), "the lock does not exclude"
    assert not STORAGE_MUTATION_LOCK.locked()


_HASH = "ab" * 32


@pytest.fixture
def originals(tmp_path):
    storage = tmp_path / "storage"
    (storage / "originals").mkdir(parents=True)
    return storage


@pytest.mark.parametrize("name", [f"{_HASH}.pdf", f"{_HASH}.bin", f"{_HASH}.JPG", _HASH])
def test_find_original_finds_every_extension_ingestion_writes(originals, name):
    """upload.py stores no extension for a dotless name, telegram/gmail fall
    back to .bin, one gmail path keeps the sender's case."""
    from backend.atomic import find_original

    (originals / "originals" / name).write_bytes(b"x")
    assert find_original(str(originals), _HASH) == str(originals / "originals" / name)


def test_find_original_returns_none_when_nothing_is_on_disk(originals):
    from backend.atomic import find_original

    (originals / "originals" / f"{'cd' * 32}.pdf").write_bytes(b"other document")
    assert find_original(str(originals), _HASH) is None


@pytest.mark.parametrize(
    "bad",
    ["../../etc/hostname", "/etc/hostname", _HASH.upper(), _HASH[:-1], _HASH + "0", "", None, "*"],
)
def test_find_original_refuses_a_hash_the_app_could_not_have_written(originals, tmp_path, bad):
    """file_hash comes out of the database, which a restore can replace. The
    download serves whatever this returns, so a traversal must find nothing
    even when the file it names exists."""
    from backend.atomic import find_original

    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "hostname").write_text("nas")
    (originals / "originals" / f"{_HASH}.pdf").write_bytes(b"x")
    assert find_original(str(originals), bad) is None


def test_find_original_refuses_a_hash_with_a_valid_prefix(originals):
    """fullmatch, not match: a value that STARTS with a valid hash must not
    reach the filesystem. Both files exist, so only the shape check stops it."""
    from backend.atomic import find_original

    (originals / "originals" / f"{_HASH}.pdf").write_bytes(b"x")
    (originals / "originals" / f"{_HASH}.pdf.pdf").write_bytes(b"x")
    assert find_original(str(originals), _HASH + ".pdf") is None
    assert find_original(str(originals), _HASH + "\n") is None


# Value: protects=find_original refuses a symlink at the bare (dotless) path as well as the globbed one; fails_when=the bare check becomes os.path.isfile, which follows links; why_new=only the .pdf (glob) link was tested; seam=none; no_claim=symlinked parent dirs
@pytest.mark.parametrize("link_name", [f"{_HASH}.pdf", _HASH])
def test_find_original_does_not_follow_a_symlink(originals, tmp_path, link_name):
    """Same rule as get_filed_path and verify_backup: the storage tree holds
    regular files. A planted link would make the download serve its target."""
    from backend.atomic import find_original

    target = tmp_path / "elsewhere.pdf"
    target.write_bytes(b"not an original")
    os.symlink(target, originals / "originals" / link_name)
    assert find_original(str(originals), _HASH) is None


def test_find_original_skips_a_directory(originals):
    """A directory named like an original made FileResponse raise (a 500);
    it is not an original, so the lookup reports nothing there."""
    from backend.atomic import find_original

    (originals / "originals" / _HASH).mkdir()
    assert find_original(str(originals), _HASH) is None
    (originals / "originals" / f"{_HASH}.jpg").write_bytes(b"x")
    assert find_original(str(originals), _HASH) == str(originals / "originals" / f"{_HASH}.jpg")


def test_find_original_prefers_an_extension_the_app_can_process(originals):
    """A failed INSERT after save_original (two arrivals of the same bytes, or a
    crash) can leave a second file for one hash. Alphabetical order would pick
    <hash>.bin, or the dotless file, over the PDF, and processing would fail."""
    from backend.atomic import find_original

    for name in (_HASH, f"{_HASH}.bin", f"{_HASH}.jpg"):
        (originals / "originals" / name).write_bytes(b"x")
    # dotless and .bin sort ahead of .jpg by name; the processable one wins
    assert find_original(str(originals), _HASH) == str(originals / "originals" / f"{_HASH}.jpg")
    (originals / "originals" / f"{_HASH}.PDF").write_bytes(b"x")
    # two processable candidates: the stable name order decides
    assert find_original(str(originals), _HASH) == str(originals / "originals" / f"{_HASH}.PDF")
    for name in (f"{_HASH}.jpg", f"{_HASH}.PDF"):
        (originals / "originals" / name).unlink()
    # only unprocessable ones left: still found, never dropped
    assert find_original(str(originals), _HASH) == str(originals / "originals" / _HASH)


def test_find_original_does_not_list_the_directory_for_a_plain_pdf(originals, monkeypatch):
    """<hash>.pdf is 314 of 384 originals on the live install. Listing
    originals/ for it on every page view grows with the archive on shfs."""
    import backend.atomic as atomic

    (originals / "originals" / f"{_HASH}.pdf").write_bytes(b"x")

    def no_listing(*a, **k):
        raise AssertionError("find_original listed originals/ for a plain .pdf")

    monkeypatch.setattr(atomic.glob, "glob", no_listing)
    assert atomic.find_original(str(originals), _HASH) == str(originals / "originals" / f"{_HASH}.pdf")


def test_processable_extensions_match_what_normalize_accepts():
    """atomic.py must stay stdlib-only, so it keeps its own copy of the list
    normalize_file dispatches on; this pins the two together."""
    from backend.atomic import PROCESSABLE_EXTENSIONS
    from backend.processing.normalize import SUPPORTED_EXTENSIONS

    assert PROCESSABLE_EXTENSIONS == SUPPORTED_EXTENSIONS
