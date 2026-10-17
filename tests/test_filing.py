import pytest

from backend.processing.filing import download_name, generate_stored_filename

def test_full_fields():
    name = generate_stored_filename(receipt_date="2026-01-15", vendor_receipt_id="INV-001", file_hash="abcdef1234567890")
    assert name == "2026-01-15-INV-001-abcdef12.pdf"

def test_no_date():
    name = generate_stored_filename(receipt_date=None, vendor_receipt_id="R123", file_hash="abcdef1234567890")
    assert name == "0000-00-00-R123-abcdef12.pdf"

def test_no_receipt_id():
    name = generate_stored_filename(receipt_date="2026-03-01", vendor_receipt_id=None, file_hash="abcdef1234567890")
    assert name == "2026-03-01-000000-abcdef12.pdf"

def test_no_fields():
    name = generate_stored_filename(receipt_date=None, vendor_receipt_id=None, file_hash="abcdef1234567890")
    assert name == "0000-00-00-000000-abcdef12.pdf"

def test_special_chars_sanitized():
    name = generate_stored_filename(receipt_date="2026-01-15", vendor_receipt_id="INV/001:test", file_hash="abcdef1234567890")
    assert "/" not in name
    assert ":" not in name

def test_malformed_date_falls_back():
    # receipt_date is LLM-supplied — a non-YYYY-MM-DD value must not inject
    # path separators into the filed filename.
    name = generate_stored_filename(receipt_date="2026/01/15", vendor_receipt_id="R1", file_hash="abcdef1234567890")
    assert name == "0000-00-00-R1-abcdef12.pdf"
    name = generate_stored_filename(receipt_date="../../etc/passwd", vendor_receipt_id="R1", file_hash="abcdef1234567890")
    assert "/" not in name and ".." not in name

def test_non_string_values_fall_back():
    # The LLM can emit the date or receipt id as a JSON number — must fall
    # back cleanly, not TypeError inside the regex.
    name = generate_stored_filename(receipt_date=20260115, vendor_receipt_id="R1", file_hash="abcdef1234567890")
    assert name == "0000-00-00-R1-abcdef12.pdf"
    name = generate_stored_filename(receipt_date="2026-01-15", vendor_receipt_id=12345, file_hash="abcdef1234567890")
    assert name == "2026-01-15-000000-abcdef12.pdf"

def test_date_regex_boundary_bypasses():
    # $ matches before a trailing newline and \d matches Unicode digits —
    # both must be rejected (fullmatch + ASCII-only pattern).
    name = generate_stored_filename(receipt_date="2026-01-15\n", vendor_receipt_id="R1", file_hash="abcdef1234567890")
    assert name == "0000-00-00-R1-abcdef12.pdf"
    name = generate_stored_filename(receipt_date="٢٠٢٦-٠١-١٥", vendor_receipt_id="R1", file_hash="abcdef1234567890")
    assert name == "0000-00-00-R1-abcdef12.pdf"


# --- download_name (issue #69) ---

def test_download_name_uses_the_filed_stem_with_the_originals_extension():
    assert download_name("2026-10-01-30009-ab12cd34.pdf", "scan_1791552248041.jpg", 7, ".jpg") == "2026-10-01-30009-ab12cd34.jpg"
    assert download_name("2026-10-01-30009-ab12cd34.pdf", "invoice.pdf", 7, ".pdf") == "2026-10-01-30009-ab12cd34.pdf"

def test_download_name_keeps_a_missing_extension_missing():
    # api/upload.py stores a dotless upload as originals/<hash>; inventing an
    # extension would misdescribe the bytes.
    assert download_name("2026-10-01-1-ab12cd34.pdf", "receipt", 7, "") == "2026-10-01-1-ab12cd34"

@pytest.mark.parametrize("stored", [None, "", "../../etc/passwd", "/etc/hostname", "a/b.pdf", ".."])
def test_download_name_falls_back_to_the_original_name_without_a_usable_filed_name(stored):
    assert download_name(stored, "Invoice March.pdf", 7, ".pdf") == "Invoice March.pdf"

@pytest.mark.parametrize("original", [
    "attachment; filename=0a6fef7c-1b2c.pdf",
    'attachment; filename="0a6fef7c-1b2c.pdf"',
    "inline;filename*=UTF-8''0a6fef7c-1b2c.pdf",
    "C:\\Users\\me\\0a6fef7c-1b2c.pdf",
    "../../0a6fef7c-1b2c.pdf",
    "0a6fef7c-1b2c\n.pdf",
])
def test_download_name_cleans_the_original_name(original):
    assert download_name(None, original, 7, ".pdf") == "0a6fef7c-1b2c.pdf"

def test_download_name_takes_the_extension_from_disk_not_from_the_original_name():
    # telegram.py stores an unknown type as .bin; the download must say so.
    assert download_name(None, "photo.jpg", 7, ".bin") == "photo.bin"

@pytest.mark.parametrize("original", [None, "", "attachment; filename=", ".pdf", "..", "dir/", 12])
def test_download_name_last_resort_is_the_document_id(original):
    assert download_name(None, original, 42, ".pdf") == "document-42.pdf"

def test_download_name_never_saves_a_hidden_file():
    assert download_name(None, ".receipt.pdf", 7, ".pdf") == "receipt.pdf"

def test_download_name_keeps_non_ascii():
    assert download_name(None, "קבלה מרץ.jpg", 7, ".jpg") == "קבלה מרץ.jpg"
