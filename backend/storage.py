import hashlib
import os
import shutil
import logging
from pathlib import Path

import fitz  # PyMuPDF

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
    if not os.path.exists(dest):
        shutil.copy2(src_path, dest)
    return dest


def save_converted(src_path: str, file_hash: str, data_dir: str) -> str:
    dest_dir = os.path.join(data_dir, "storage", "converted")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"{file_hash}.pdf")
    if not os.path.exists(dest):
        shutil.copy2(src_path, dest)
    return dest


def save_filed(src_path: str, stored_filename: str, data_dir: str) -> str:
    dest_dir = os.path.join(data_dir, "storage", "filed")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, stored_filename)
    shutil.copy2(src_path, dest)
    return dest


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
