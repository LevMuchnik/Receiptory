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
