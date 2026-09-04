import io
import os
import pytest
from pathlib import Path

import fitz
from PIL import Image
from backend.config import set_setting
from backend.storage import render_all_pages_to_memory
from backend.processing.normalize import (
    normalize_file, NormalizeResult, _resolve_render_dpi, DEFAULT_RENDER_DPI,
    MIN_RENDER_DPI, MAX_RENDER_DPI,
)

@pytest.fixture
def sample_image(tmp_path):
    img = Image.new("RGB", (200, 300), color="white")
    path = str(tmp_path / "test_receipt.jpg")
    img.save(path, "JPEG")
    return path

@pytest.fixture
def sample_html(tmp_path):
    path = str(tmp_path / "receipt.html")
    with open(path, "w") as f:
        f.write("<html><body><h1>Receipt</h1><p>Total: $50.00</p></body></html>")
    return path

def test_pdf_passthrough(sample_pdf_path, tmp_data_dir):
    result = normalize_file(sample_pdf_path, str(tmp_data_dir))
    assert result.converted is False
    assert result.pdf_path == sample_pdf_path
    assert result.page_count > 0

def test_image_to_pdf(sample_image, tmp_data_dir):
    result = normalize_file(sample_image, str(tmp_data_dir))
    assert result.converted is True
    assert result.pdf_path.endswith(".pdf")
    assert os.path.exists(result.pdf_path)
    assert result.page_count == 1

def _weasyprint_available():
    try:
        import weasyprint  # noqa: F401
        return True
    except Exception:
        return False

weasyprint_available = pytest.mark.skipif(
    not _weasyprint_available(),
    reason="weasyprint native libraries not available on this system",
)

@weasyprint_available
def test_html_to_pdf(sample_html, tmp_data_dir):
    result = normalize_file(sample_html, str(tmp_data_dir))
    assert result.converted is True
    assert result.pdf_path.endswith(".pdf")
    assert os.path.exists(result.pdf_path)
    assert result.page_count >= 1

# --- image -> PDF page sizing (must track the page_render_dpi setting) ---
#
# PIL sizes a PDF page as pixels/resolution inches and fitz reports it in points
# (72 per inch), so a WxH image at D dpi must land on a (W/D*72) x (H/D*72) page.
# The renderer that feeds the LLM rasterizes that page at page_render_dpi, so the
# two DPIs agreeing is exactly what makes the scan round trip 1:1.

def _make_image(tmp_path, name, size):
    img = Image.new("RGB", size, color="white")
    path = str(tmp_path / name)
    img.save(path, "JPEG")
    return path

def _page_size_points(pdf_path):
    doc = fitz.open(pdf_path)
    rect = doc[0].rect
    doc.close()
    return rect.width, rect.height

def _rendered_size(pdf_path, dpi):
    pages = render_all_pages_to_memory(pdf_path, dpi=dpi)
    with Image.open(io.BytesIO(pages[0])) as img:
        return img.size

def test_image_to_pdf_page_size_at_default_dpi(tmp_path, tmp_data_dir, db_path):
    # No settings row written -> get_setting falls through to the 200 default.
    image = _make_image(tmp_path, "scan.jpg", (2000, 3000))
    result = normalize_file(image, str(tmp_data_dir))
    width, height = _page_size_points(result.pdf_path)
    assert width == pytest.approx(2000 / 200 * 72, abs=0.5)   # 10in -> 720pt
    assert height == pytest.approx(3000 / 200 * 72, abs=0.5)  # 15in -> 1080pt

def test_image_to_pdf_round_trip_is_lossless_at_default_dpi(tmp_path, tmp_data_dir, db_path):
    image = _make_image(tmp_path, "scan.jpg", (2000, 3000))
    result = normalize_file(image, str(tmp_data_dir))
    rendered = _rendered_size(result.pdf_path, 200)
    assert rendered[0] == pytest.approx(2000, abs=2)
    assert rendered[1] == pytest.approx(3000, abs=2)

def test_image_to_pdf_tracks_non_default_dpi(tmp_path, tmp_data_dir, db_path):
    set_setting("page_render_dpi", 300)
    image = _make_image(tmp_path, "scan.jpg", (2000, 3000))
    result = normalize_file(image, str(tmp_data_dir))
    width, height = _page_size_points(result.pdf_path)
    # Smaller page at higher dpi: 2000/300in -> 480pt, 3000/300in -> 720pt.
    assert width == pytest.approx(2000 / 300 * 72, abs=0.5)
    assert height == pytest.approx(3000 / 300 * 72, abs=0.5)
    # ...and the round trip is still 1:1 at that dpi, no upscaling.
    rendered = _rendered_size(result.pdf_path, 300)
    assert rendered[0] == pytest.approx(2000, abs=2)
    assert rendered[1] == pytest.approx(3000, abs=2)

def test_image_to_pdf_round_trip_with_odd_dimensions(tmp_path, tmp_data_dir, db_path):
    set_setting("page_render_dpi", 150)
    image = _make_image(tmp_path, "scan.jpg", (1234, 789))
    result = normalize_file(image, str(tmp_data_dir))
    rendered = _rendered_size(result.pdf_path, 150)
    assert rendered[0] == pytest.approx(1234, abs=2)
    assert rendered[1] == pytest.approx(789, abs=2)

def test_render_dpi_accepts_numeric_string_from_db(db_path):
    # DB values are stored as raw JSON and never coerced (only env overrides are),
    # so a UI edit can arrive as a string.
    set_setting("page_render_dpi", "300")
    assert _resolve_render_dpi() == 300.0

@pytest.mark.parametrize("value", ["not-a-number", "", 0, -50, None, True])
def test_render_dpi_falls_back_on_garbage(value, db_path):
    set_setting("page_render_dpi", value)
    assert _resolve_render_dpi() == DEFAULT_RENDER_DPI

@pytest.mark.parametrize("value,expected", [(1, MIN_RENDER_DPI), (10, MIN_RENDER_DPI), (5000, MAX_RENDER_DPI)])
def test_render_dpi_clamps_extremes(value, expected, db_path):
    set_setting("page_render_dpi", value)
    assert _resolve_render_dpi() == expected

def test_image_to_pdf_survives_garbage_dpi(tmp_path, tmp_data_dir, db_path):
    set_setting("page_render_dpi", 0)
    image = _make_image(tmp_path, "scan.jpg", (2000, 3000))
    result = normalize_file(image, str(tmp_data_dir))
    width, height = _page_size_points(result.pdf_path)
    assert width == pytest.approx(2000 / DEFAULT_RENDER_DPI * 72, abs=0.5)
    assert height == pytest.approx(3000 / DEFAULT_RENDER_DPI * 72, abs=0.5)

def test_image_to_pdf_without_db_uses_default_dpi(tmp_path, tmp_data_dir):
    # No init_db in this test: get_setting raises, conversion must still work.
    image = _make_image(tmp_path, "scan.jpg", (2000, 3000))
    result = normalize_file(image, str(tmp_data_dir))
    width, _ = _page_size_points(result.pdf_path)
    assert width == pytest.approx(2000 / DEFAULT_RENDER_DPI * 72, abs=0.5)

def test_unsupported_format(tmp_path, tmp_data_dir):
    path = str(tmp_path / "file.xyz")
    with open(path, "w") as f:
        f.write("not a document")
    with pytest.raises(ValueError, match="Unsupported"):
        normalize_file(path, str(tmp_data_dir))
