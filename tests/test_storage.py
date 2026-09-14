import os
import pytest
from pathlib import Path
from backend.storage import (save_original, save_converted, save_filed, get_file_path, render_page, clear_page_cache, compute_file_hash)

def test_compute_file_hash(sample_pdf_path):
    h = compute_file_hash(sample_pdf_path)
    assert len(h) == 64
    assert compute_file_hash(sample_pdf_path) == h

def test_save_and_get_original(tmp_data_dir, sample_pdf_path):
    file_hash = compute_file_hash(sample_pdf_path)
    saved = save_original(sample_pdf_path, file_hash, ".pdf", str(tmp_data_dir))
    assert os.path.exists(saved)
    assert file_hash in saved
    retrieved = get_file_path("original", file_hash, ".pdf", str(tmp_data_dir))
    assert retrieved == saved

def test_save_filed(tmp_data_dir, sample_pdf_path):
    stored_name = "2026-01-15-INV001-abc123.pdf"
    filed_path = save_filed(sample_pdf_path, stored_name, str(tmp_data_dir))
    assert os.path.exists(filed_path)
    assert stored_name in filed_path

def test_render_page(tmp_data_dir, sample_pdf_path):
    png_bytes = render_page(sample_pdf_path, page_num=0, dpi=150)
    assert len(png_bytes) > 0
    assert png_bytes[:4] == b'\x89PNG'

def test_render_page_cached(tmp_data_dir, sample_pdf_path):
    cache_dir = str(tmp_data_dir / "storage" / "page_cache")
    png1 = render_page(sample_pdf_path, page_num=0, dpi=150, cache_dir=cache_dir, doc_id=1)
    png2 = render_page(sample_pdf_path, page_num=0, dpi=150, cache_dir=cache_dir, doc_id=1)
    assert png1 == png2
    assert os.path.exists(os.path.join(cache_dir, "1", "page_0.png"))

def test_clear_page_cache(tmp_data_dir, sample_pdf_path):
    cache_dir = str(tmp_data_dir / "storage" / "page_cache")
    render_page(sample_pdf_path, page_num=0, dpi=150, cache_dir=cache_dir, doc_id=1)
    clear_page_cache(cache_dir, doc_id=1)
    assert not os.path.exists(os.path.join(cache_dir, "1"))


# --- issue #45: the save_* helpers must route through backend.atomic ---------
#
# These are CALL-SITE tests and they exist because the first version of this
# work had none. Every save_* helper could be reverted to shutil.copy2 /
# open(...,"wb") and the whole suite stayed green (464 passed) -- the atomic
# tests covered the helper, and nothing covered the four places that use it.
# The repo had already learned this once ("mutate the call site, not just the
# helper") and learned it again here.
#
# They spy on os.replace rather than on backend.atomic, so inlining a
# shutil.copy2 at the call site also fails, not just swapping the import.

import os
import stat
import pytest
from backend.atomic import TMP_PREFIX
from backend.storage import save_converted, save_scanner_test_frame


def _watch_replace(monkeypatch):
    seen = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (seen.append((a, b)), real(a, b))[1])
    return seen


@pytest.mark.parametrize("name,call", [
    ("save_original", lambda src, d: save_original(src, "a" * 64, ".pdf", d)),
    ("save_converted", lambda src, d: save_converted(src, "b" * 64, d)),
    ("save_filed", lambda src, d: save_filed(src, "2026-01-01-x-c0ffee12.pdf", d)),
])
def test_every_save_helper_writes_through_a_rename(
    name, call, tmp_data_dir, sample_pdf_path, monkeypatch
):
    seen = _watch_replace(monkeypatch)

    dest = call(sample_pdf_path, str(tmp_data_dir))

    assert len(seen) == 1, f"{name} opened the destination directly instead of renaming onto it"
    tmp, final = seen[0]
    assert final == dest
    assert os.path.basename(tmp).startswith(TMP_PREFIX)
    assert os.path.dirname(tmp) == os.path.dirname(dest), (
        "the scratch file must be in the destination directory or the rename "
        "crosses a filesystem and stops being atomic"
    )
    assert os.path.exists(dest)


def test_the_scanner_frame_writer_also_renames(tmp_data_dir, monkeypatch):
    """The one tree issue #45 newly ADDS to the backup, so the one whose torn
    files would newly ship. Nothing hashes frames the way verify.py hashes
    originals, so a torn frame would never be reported."""
    seen = _watch_replace(monkeypatch)

    rel = save_scanner_test_frame(b"\xff\xd8 labelled frame", str(tmp_data_dir))

    assert len(seen) == 1, "the frame was written directly to its final name"
    assert os.path.basename(seen[0][0]).startswith(TMP_PREFIX)
    assert (tmp_data_dir / rel).read_bytes() == b"\xff\xd8 labelled frame"


def test_a_scanner_frame_path_cannot_leave_the_data_directory(tmp_data_dir):
    """frame_path comes from the database, and since scanner_test_set became a
    backup tree a restored database is untrusted input. api/scanner.py hands
    this result to FileResponse and to os.unlink, as root."""
    from backend.storage import get_scanner_test_frame_path

    ok = get_scanner_test_frame_path("scanner_test_set/2026-01-01/a.jpg", str(tmp_data_dir))
    assert ok.startswith(str(tmp_data_dir))

    for evil in ("/etc/shadow", "../../../../etc/shadow", "scanner_test_set/../../../etc/passwd"):
        with pytest.raises(ValueError, match="escapes"):
            get_scanner_test_frame_path(evil, str(tmp_data_dir))


# ---------------------------------------------------------------------------
# get_filed_path / remove_filed -- issue #54
# ---------------------------------------------------------------------------


def test_a_stored_filename_cannot_leave_the_filed_directory(tmp_data_dir):
    """documents.stored_filename reaches os.unlink and ZipFile.write, as root.

    os.path.join(filed_dir, "/etc/shadow") returns "/etc/shadow" -- the prefix
    is silently discarded -- and os.path.exists then says yes. Before this
    guard, api/export.py copied whatever the row named into the export zip.
    """
    from backend.storage import get_filed_path

    ok = get_filed_path("2026-01-01-x-c0ffee12.pdf", str(tmp_data_dir))
    assert ok == os.path.join(str(tmp_data_dir), "storage", "filed", "2026-01-01-x-c0ffee12.pdf")

    for evil in (
        "/etc/shadow",
        "../../../../etc/shadow",
        "sub/dir/receipt.pdf",   # rejected on purpose: see the strictness test
        "",
    ):
        with pytest.raises(ValueError):
            get_filed_path(evil, str(tmp_data_dir))


def test_the_filed_guard_agrees_with_the_backup_guard(tmp_data_dir):
    """Two guards on one column that disagree are worse than one guard.

    verify.py rejects a multi-segment name as FATAL. If storage accepted one,
    the app could write a row that later makes every nightly backup refuse to
    verify -- a failure that surfaces at 02:00 and nowhere else. Both sides must
    resolve to the same predicate object, not merely to equivalent logic.
    """
    import backend.atomic as atomic_mod
    import backend.backup.verify as verify_mod
    from backend.backup.verify import _is_contained_name
    from backend.storage import get_filed_path
    from backend.atomic import is_contained_name

    # The identity, first. A behavioural table over hand-picked names passes
    # just as happily against a FORKED copy in verify.py, which is the drift
    # this test exists to prevent -- so assert they are literally one object,
    # and keep the table below as the secondary check on what that object does.
    assert verify_mod.is_contained_name is atomic_mod.is_contained_name, (
        "verify.py no longer shares storage's predicate -- the two guards on "
        "documents.stored_filename can now drift apart"
    )

    for name in ("a.pdf", "sub/dir.pdf", "/etc/shadow", "..", "", "a/../b.pdf",
                 "a\x00b.pdf", "sub\\dir.pdf"):
        accepted_by_storage = True
        try:
            get_filed_path(name, str(tmp_data_dir))
        except ValueError:
            accepted_by_storage = False
        assert accepted_by_storage == _is_contained_name(name) == is_contained_name(name), (
            f"storage and verify disagree about {name!r}"
        )


def test_remove_filed_deletes_the_file_and_reports_it(tmp_data_dir, sample_pdf_path):
    from backend.storage import remove_filed

    dest = save_filed(sample_pdf_path, "2026-01-01-x-c0ffee12.pdf", str(tmp_data_dir))
    assert os.path.exists(dest)

    assert remove_filed("2026-01-01-x-c0ffee12.pdf", str(tmp_data_dir)) is True
    assert not os.path.exists(dest)


def test_remove_filed_is_happy_when_the_file_is_already_gone(tmp_data_dir):
    """The caller's goal is that the name is gone. Two reprocesses of the same
    document race to the same conclusion, and neither should raise."""
    from backend.storage import remove_filed

    assert remove_filed("never-existed-00000000.pdf", str(tmp_data_dir)) is False


def test_remove_filed_refuses_a_name_that_escapes(tmp_data_dir, tmp_path):
    """The whole reason the resolver exists: this is an os.unlink running as
    root against a value that a restored database supplied."""
    from backend.storage import remove_filed

    victim = tmp_path / "precious"
    victim.write_text("do not delete me")

    with pytest.raises(ValueError):
        remove_filed(str(victim), str(tmp_data_dir))
    assert victim.exists(), "the guard did not stop an absolute path"

    with pytest.raises(ValueError):
        remove_filed("../../precious", str(tmp_data_dir))
    assert victim.exists()


def test_remove_filed_holds_the_storage_mutation_lock(tmp_data_dir, sample_pdf_path):
    """Without the lock a delete can land inside build_backup's copytree, whose
    single retry is built for a rename and cannot succeed against a delete.
    Asserting the lock is HELD during the unlink, not merely that it exists."""
    import backend.storage as storage_mod
    from backend.atomic import STORAGE_MUTATION_LOCK

    save_filed(sample_pdf_path, "2026-01-01-x-c0ffee12.pdf", str(tmp_data_dir))

    held = []
    real_unlink = storage_mod.os.unlink

    def spy(path):
        held.append(STORAGE_MUTATION_LOCK.locked())
        return real_unlink(path)

    storage_mod.os.unlink = spy
    try:
        storage_mod.remove_filed("2026-01-01-x-c0ffee12.pdf", str(tmp_data_dir))
    finally:
        storage_mod.os.unlink = real_unlink

    assert held == [True], "remove_filed unlinked without holding STORAGE_MUTATION_LOCK"
    assert not STORAGE_MUTATION_LOCK.locked(), "the lock was not released"


def test_save_filed_refuses_a_name_that_escapes(tmp_data_dir, sample_pdf_path, tmp_path):
    """The write side of the resolver, not just the read and delete sides.

    Caught by mutation: reverting save_filed to a bare os.path.join left the
    whole storage suite green, because every other test here checks
    get_filed_path directly or passes a name that is already valid. The PR
    claims every filed/ call site goes through the resolver -- this is what
    makes that claim true of the one that writes.
    """
    victim = tmp_path / "outside.pdf"

    with pytest.raises(ValueError):
        save_filed(sample_pdf_path, str(victim), str(tmp_data_dir))
    assert not victim.exists(), "save_filed wrote outside the data directory"

    with pytest.raises(ValueError):
        save_filed(sample_pdf_path, "../escaped.pdf", str(tmp_data_dir))
    assert not (tmp_data_dir / "storage" / "escaped.pdf").exists()


def test_get_filed_path_refuses_a_symlinked_filed_copy(tmp_path):
    """A contained NAME can still point anywhere, and ZipFile.write follows
    symlinks -- so filed/receipt.pdf aimed at a secret would put that content in
    the export zip under a harmless arcname. backup/verify.py already treats any
    symlink in a backup as fatal; this is the live tree taking the same line."""
    from backend.storage import get_filed_path

    filed = tmp_path / "storage" / "filed"
    filed.mkdir(parents=True)
    secret = tmp_path / "secret"
    secret.write_text("root:x:0:0")
    os.symlink(secret, filed / "evil.pdf")

    with pytest.raises(ValueError, match="symlink"):
        get_filed_path("evil.pdf", str(tmp_path))

    # A path that does not exist yet must still resolve, or save_filed breaks.
    assert get_filed_path("brand-new-00000000.pdf", str(tmp_path)).endswith(
        "brand-new-00000000.pdf"
    )
