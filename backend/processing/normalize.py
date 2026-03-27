import os
import tempfile
import logging
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".heic", ".heif", ".webp"}
HTML_EXTENSIONS = {".html", ".htm"}
PDF_EXTENSIONS = {".pdf"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | HTML_EXTENSIONS | PDF_EXTENSIONS


@dataclass
class NormalizeResult:
    pdf_path: str
    converted: bool
    page_count: int
    original_ext: str


def normalize_file(file_path: str, data_dir: str) -> NormalizeResult:
    ext = Path(file_path).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file format: {ext}")
    if ext in PDF_EXTENSIONS:
        page_count = _count_pages(file_path)
        return NormalizeResult(pdf_path=file_path, converted=False, page_count=page_count, original_ext=ext)
    if ext in IMAGE_EXTENSIONS:
        pdf_path = _image_to_pdf(file_path, data_dir)
        page_count = _count_pages(pdf_path)
        return NormalizeResult(pdf_path=pdf_path, converted=True, page_count=page_count, original_ext=ext)
    if ext in HTML_EXTENSIONS:
        pdf_path = _html_to_pdf(file_path, data_dir)
        page_count = _count_pages(pdf_path)
        return NormalizeResult(pdf_path=pdf_path, converted=True, page_count=page_count, original_ext=ext)
    raise ValueError(f"Unsupported file format: {ext}")


def _image_to_pdf(image_path: str, data_dir: str) -> str:
    img = Image.open(image_path)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")
    tmp_dir = os.path.join(data_dir, "storage", "converted")
    os.makedirs(tmp_dir, exist_ok=True)
    stem = Path(image_path).stem
    pdf_path = os.path.join(tmp_dir, f"{stem}_converted.pdf")
    img.save(pdf_path, "PDF", resolution=200.0)
    return pdf_path


def _html_to_pdf(html_path: str, data_dir: str) -> str:
    from weasyprint import HTML
    tmp_dir = os.path.join(data_dir, "storage", "converted")
    os.makedirs(tmp_dir, exist_ok=True)
    stem = Path(html_path).stem
    pdf_path = os.path.join(tmp_dir, f"{stem}_converted.pdf")
    HTML(filename=html_path).write_pdf(pdf_path)
    return pdf_path


def _count_pages(pdf_path: str) -> int:
    doc = fitz.open(pdf_path)
    count = len(doc)
    doc.close()
    return count
