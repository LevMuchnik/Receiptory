import math
import os
import tempfile
import logging
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

# Module scope is safe: backend.config imports only backend.database (+ stdlib)
# and nothing under backend.processing, so there is no cycle. Imported as a module
# rather than `from ... import get_setting` so the lookup stays late-bound.
from backend import config as _config

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".heic", ".heif", ".webp"}
HTML_EXTENSIONS = {".html", ".htm"}
PDF_EXTENSIONS = {".pdf"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | HTML_EXTENSIONS | PDF_EXTENSIONS

# Page-sizing DPI for image -> PDF conversion. Kept equal to the `page_render_dpi`
# setting at runtime (see _resolve_render_dpi); these are the fallback and the
# sanity bounds, not a fixed value.
DEFAULT_RENDER_DPI = 200.0
MIN_RENDER_DPI = 72.0
MAX_RENDER_DPI = 600.0


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


def _resolve_render_dpi() -> float:
    """The DPI to size a converted image's PDF page at.

    COUPLING: this must match the DPI the page is later rasterized at, or the
    image round trip stops being 1:1. `backend/processing/pipeline.py` feeds the
    LLM via `storage.render_all_pages_to_memory(pdf, dpi=get_setting("page_render_dpi"))`,
    and PIL sizes a PDF page as `pixels / resolution` inches — so writing the page
    at the same DPI the renderer will use means one source pixel in, one rendered
    pixel out. Hardcoding 200 here agreed with the default only by coincidence:
    raising `page_render_dpi` upscaled every uploaded image (more bytes to the LLM,
    no extra detail) and lowering it silently discarded detail. This is the main
    path for mobile-scanner JPEGs, so the resolution loss would be real.

    The value is a runtime setting read from the DB, where (unlike env overrides)
    values are stored as raw JSON and never type-coerced — a UI edit can arrive as
    "200", or as 0/negative/absurd. Coerce to a number, drop anything unusable
    (non-numeric, non-finite, non-positive) back to the 200 default, and clamp the
    merely extreme into MIN..MAX so PIL can't raise or emit a degenerate page.
    """
    try:
        raw = _config.get_setting("page_render_dpi")
    except Exception:
        # No DB yet (standalone/normalize-only callers) or a corrupt read — the
        # conversion must still work at the default.
        return DEFAULT_RENDER_DPI
    if isinstance(raw, bool):
        raw = None
    try:
        dpi = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        dpi = 0.0
    if not math.isfinite(dpi) or dpi <= 0:
        logger.warning("Unusable page_render_dpi=%r; using %s", raw, DEFAULT_RENDER_DPI)
        return DEFAULT_RENDER_DPI
    clamped = min(max(dpi, MIN_RENDER_DPI), MAX_RENDER_DPI)
    if clamped != dpi:
        logger.warning("page_render_dpi=%r out of range; clamped to %s", raw, clamped)
    return clamped


def _image_to_pdf(image_path: str, data_dir: str) -> str:
    img = Image.open(image_path)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGB")
    tmp_dir = os.path.join(data_dir, "storage", "converted")
    os.makedirs(tmp_dir, exist_ok=True)
    stem = Path(image_path).stem
    pdf_path = os.path.join(tmp_dir, f"{stem}_converted.pdf")
    img.save(pdf_path, "PDF", resolution=_resolve_render_dpi())
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
