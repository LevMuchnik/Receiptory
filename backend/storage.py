import hashlib
import os
import shutil
import logging
from pathlib import Path

import fitz  # PyMuPDF

from backend.atomic import (
    STORAGE_MUTATION_LOCK,
    atomic_copy,
    atomic_write_bytes,
    is_contained_name,
)

logger = logging.getLogger(__name__)


def compute_file_hash(file_path: str) -> str:
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha.update(chunk)
    return sha.hexdigest()


def save_original(src_path: str, file_hash: str, ext: str, data_dir: str) -> str:
    dest_dir = os.path.join(data_dir, "storage", "originals")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"{file_hash}{ext}")
    # The guard is only trustworthy because the write is atomic. A crashed
    # copy2 used to leave a partial file here that this check then treated as
    # complete forever; atomic_copy leaves a .rcpt-tmp-* instead, so a
    # destination that exists is a destination that finished.
    if not os.path.exists(dest):
        atomic_copy(src_path, dest)
    return dest


def save_converted(src_path: str, file_hash: str, data_dir: str) -> str:
    dest_dir = os.path.join(data_dir, "storage", "converted")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"{file_hash}.pdf")
    # Note the consequence of this guard, which is not obvious: normalize.py
    # rewrites <hash>_converted.pdf on every reprocess while this refuses to
    # overwrite <hash>.pdf, so <hash>.pdf is the OLDEST conversion and the
    # scratch file beside it is the NEWEST. backup/runner.py's exclusion rule
    # depends on knowing that.
    if not os.path.exists(dest):
        atomic_copy(src_path, dest)
    return dest


def get_filed_path(stored_filename: str, data_dir: str) -> str:
    """Resolve a documents.stored_filename, refusing to leave storage/filed.

    Every consumer of that column goes through here, because it is untrusted
    input on more than one path. api/export.py joins it and hands the result to
    ZipFile.write as root, so a restored row naming "/etc/shadow" was an
    arbitrary read straight into the export zip -- os.path.join discards the
    prefix for an absolute path, and os.path.exists then says yes. Since #54
    the same value also reaches os.unlink, which makes it an arbitrary delete.

    verify.py rejects such a row before a restore lands it, but that guard runs
    only on the restore path. This one runs on every use, so the invariant does
    not depend on how the row got into the database -- the same lesson #45 cost
    us on scanner_test_frames.frame_path.
    """
    if not is_contained_name(stored_filename):
        raise ValueError(
            f"stored_filename escapes the filed directory: {stored_filename!r}"
        )
    path = os.path.join(data_dir, "storage", "filed", stored_filename)
    # A contained NAME can still point anywhere. ZipFile.write follows symlinks,
    # so filed/receipt.pdf aimed at /etc/shadow would put that content in the
    # export under a harmless arcname. backup/verify.py already refuses to
    # verify any backup containing a symlink; without this the live tree held
    # the opposite policy to the backup tree. islink is False for a path that
    # does not exist, so save_filed creating a new file is unaffected.
    if os.path.islink(path):
        raise ValueError(f"filed copy is a symlink: {stored_filename!r}")
    return path


def save_filed(src_path: str, stored_filename: str, data_dir: str) -> str:
    dest = get_filed_path(stored_filename, data_dir)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    # The only save_* helper with no existence guard, so this is the one that
    # genuinely overwrites in place -- pipeline.py re-runs it on every
    # reprocess. That makes it the single writer a backup's copytree can catch
    # mid-write, which is why it has to be atomic.
    atomic_copy(src_path, dest)
    return dest


def remove_filed(stored_filename: str, data_dir: str) -> bool:
    """Delete a filed/ copy no row references any more. Returns True if it went.

    Takes STORAGE_MUTATION_LOCK, which EVERY deleter from a BACKUP_TREE must
    hold -- see the lock's comment in atomic.py for what breaks without it.
    (Not "the first deleter": api/scanner.py's delete_test_frame also unlinks
    from scanner_test_set/, which runner.py copies too. It predated the lock and
    now takes it as well.)

    A missing file is success, not an error: the caller's goal is that the name
    is gone, and two reprocesses of the same document race to the same
    conclusion.
    """
    path = get_filed_path(stored_filename, data_dir)
    with STORAGE_MUTATION_LOCK:
        try:
            os.unlink(path)
        except FileNotFoundError:
            return False
    return True


def get_file_path(file_type: str, file_hash: str, ext: str, data_dir: str) -> str:
    if file_type == "original":
        return os.path.join(data_dir, "storage", "originals", f"{file_hash}{ext}")
    elif file_type == "converted":
        return os.path.join(data_dir, "storage", "converted", f"{file_hash}.pdf")
    raise ValueError(f"Unknown file type: {file_type}")


def get_pdf_page_count(pdf_path: str) -> int:
    doc = fitz.open(pdf_path)
    count = len(doc)
    doc.close()
    return count


def render_page(pdf_path: str, page_num: int, dpi: int = 200, cache_dir: str | None = None, doc_id: int | None = None) -> bytes:
    if cache_dir and doc_id is not None:
        cache_path = os.path.join(cache_dir, str(doc_id), f"page_{page_num}.png")
        if os.path.exists(cache_path):
            with open(cache_path, "rb") as f:
                return f.read()

    doc = fitz.open(pdf_path)
    if page_num >= len(doc):
        doc.close()
        raise ValueError(f"Page {page_num} does not exist (document has {len(doc)} pages)")
    page = doc[page_num]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    png_bytes = pix.tobytes("png")
    doc.close()

    if cache_dir and doc_id is not None:
        page_dir = os.path.join(cache_dir, str(doc_id))
        os.makedirs(page_dir, exist_ok=True)
        with open(os.path.join(page_dir, f"page_{page_num}.png"), "wb") as f:
            f.write(png_bytes)

    return png_bytes


def clear_page_cache(cache_dir: str, doc_id: int) -> None:
    page_dir = os.path.join(cache_dir, str(doc_id))
    if os.path.exists(page_dir):
        shutil.rmtree(page_dir)


def render_all_pages_to_memory(pdf_path: str, dpi: int = 200) -> list[bytes]:
    doc = fitz.open(pdf_path)
    pages = []
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    for page in doc:
        pix = page.get_pixmap(matrix=mat)
        pages.append(pix.tobytes("png"))
    doc.close()
    return pages


def save_scanner_test_frame(jpeg_bytes: bytes, data_dir: str) -> str:
    """Save a raw camera frame for the scanner lab test set.

    Returns the path relative to data_dir so the DB row stays portable
    across data_dir relocations.
    """
    from datetime import datetime, timezone
    import secrets

    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    timestamp = now.strftime("%Y%m%dT%H%M%S")
    short_id = secrets.token_hex(4)
    rel_dir = os.path.join("scanner_test_set", day)
    abs_dir = os.path.join(data_dir, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    rel_path = os.path.join(rel_dir, f"{timestamp}-{short_id}.jpg")
    abs_path = os.path.join(data_dir, rel_path)
    # scanner_test_set is in the backup now (see backup/runner.py BACKUP_TREES),
    # so this is no longer a tree only the Lab reads. A frame caught mid-write
    # by the backup copy would ship torn, and nothing hashes these the way
    # verify.py hashes originals, so it would never be reported.
    atomic_write_bytes(abs_path, jpeg_bytes)
    return rel_path.replace(os.sep, "/")


def get_scanner_test_frame_path(rel_path: str, data_dir: str) -> str:
    """Resolve a scanner_test_frames.frame_path, refusing to leave data_dir.

    Defence in depth, and not theoretical. api/scanner.py hands the result of
    this to FileResponse and to os.unlink, as root. frame_path comes out of the
    database, and since scanner_test_set became a backup tree a restored
    database is untrusted input -- os.path.join(data_dir, "/etc/shadow")
    returns "/etc/shadow", which is an arbitrary read and an arbitrary delete.

    backup/verify.py rejects such a row before a restore ever lands it, but that
    guard only runs on the restore path. This one runs on every request, so the
    invariant does not depend on how the row got into the database.
    """
    full = os.path.realpath(os.path.join(data_dir, rel_path))
    root = os.path.realpath(data_dir)
    if full != root and not full.startswith(root + os.sep):
        raise ValueError(f"scanner frame path escapes the data directory: {rel_path!r}")
    return full
