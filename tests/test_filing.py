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
