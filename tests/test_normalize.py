import os
import pytest
from pathlib import Path
from PIL import Image
from backend.processing.normalize import normalize_file, NormalizeResult

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

def test_unsupported_format(tmp_path, tmp_data_dir):
    path = str(tmp_path / "file.xyz")
    with open(path, "w") as f:
        f.write("not a document")
    with pytest.raises(ValueError, match="Unsupported"):
        normalize_file(path, str(tmp_data_dir))
