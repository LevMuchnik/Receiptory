from backend.processing.filing import generate_stored_filename

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
