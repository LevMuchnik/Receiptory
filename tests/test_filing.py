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

def test_download_name_never_saves_a_hidden_file_from_the_filed_name():
    # generate_stored_filename cannot produce this; a restored row can.
    assert download_name(".x.pdf", None, 7, ".pdf") == "x.pdf"
    assert download_name(".pdf", "invoice.pdf", 7, ".jpg") == "invoice.jpg"

@pytest.mark.parametrize("name", [" .x.pdf", "\u00a0.x.pdf", ". .x.pdf", 'attachment; filename=" .x.pdf"'])
def test_download_name_hidden_file_rule_survives_leading_whitespace(name):
    assert download_name(None, name, 7, ".pdf") == "x.pdf"
    if not name.startswith("attachment"):
        assert download_name(name, None, 7, ".pdf") == "x.pdf"

def test_download_name_keeps_a_literal_name_that_only_looks_like_a_header():
    # no filename parameter: this is somebody's file name, not a header
    assert download_name(None, "attachment; notes.pdf", 7, ".pdf") == "attachment; notes.pdf"

@pytest.mark.parametrize("original", [
    "attachment; filename*=utf-7''%2B2AA-x.pdf",
    "attachment; filename*=unicode_escape''%5Cud800x.pdf",
])
def test_download_name_ignores_a_sender_chosen_codec(original):
    """RFC 5987 allows utf-8 and iso-8859-1. Any other label used to reach the
    codec lookup, and utf-7 / unicode_escape decode to a lone surrogate that
    Starlette's quote() cannot encode: a 500 on the download."""
    name = download_name(None, original, 7, ".pdf")
    name.encode("utf-8")  # raises on a lone surrogate
    assert name.endswith("x.pdf")

@pytest.mark.parametrize("original", [
    "Invoice\u202efdp.exe",       # right-to-left override: displays as "Invoiceexe.pdf"
    "Invoice\u2066fdp\u2069.exe",  # isolate pair
    "Invoice\u200ffdp.exe",       # right-to-left mark
    "Invoice\x85fdp.exe",         # C1 control
])
def test_download_name_strips_invisible_direction_and_control_characters(original):
    """An outside sender picks original_filename, and the download keeps the
    real extension on disk, so a direction override could make an .exe read
    as a PDF in the save dialog."""
    assert download_name(None, original, 7, ".exe") == "Invoicefdp.exe"

@pytest.mark.parametrize("original,expected", [
    # a second parameter must not leak into the name; RFC 6266: filename* wins
    ("attachment; filename=\"a.pdf\"; filename*=UTF-8''b.pdf", "b.pdf"),
    ("attachment; filename=a.pdf; size=1.5", "a.pdf"),
    # a quoted value may contain the separator
    ('attachment; filename="a;b.pdf"', "a;b.pdf"),
    # the RFC 5987 form is percent-encoded, in any charset label
    ("attachment; filename*=UTF-8''r%C3%A9%20x.pdf", "ré x.pdf"),
    ("attachment; filename*=iso-8859-1'en'r%E9.pdf", "ré.pdf"),
    # no charset'lang' prefix at all: the whole value is the encoded name
    ("attachment; filename*=r%C3%A9.pdf", "ré.pdf"),
])
def test_download_name_parses_a_whole_content_disposition(original, expected):
    assert download_name(None, original, 7, ".pdf") == expected

def test_download_name_keeps_non_ascii():
    assert download_name(None, "קבלה מרץ.jpg", 7, ".jpg") == "קבלה מרץ.jpg"


def test_download_name_extension_cannot_carry_an_unencodable_character():
    """The extension is the one on disk, chosen by the sender. A non-UTF-8 byte
    in a watched-folder name arrives as a lone surrogate, which Starlette's
    quote() cannot encode: the download would 500 for that document forever."""
    name = download_name("2026-01-01-x-ab12cd34.pdf", None, 1, ".p\udce9")
    name.encode("utf-8")

@pytest.mark.parametrize("stored,original,ext,expected", [
    # an extension the app does not process: no dotted stem can pose as the type
    (None, "Invoice.pdf.exe", ".exe", "Invoice_pdf.exe"),
    (None, "report.v2.docx", ".docx", "report_v2.docx"),
    (None, "Invoice.pdf", ".bin", "Invoice.bin"),
    # .html is processed, but a saved copy opens in the browser as a page
    (None, "Invoice.pdf.html", ".html", "Invoice_pdf.html"),
    (None, "Invoice.pdf.HTM", ".HTM", "Invoice_pdf.HTM"),
    ("2026-01-01-INV.7-ab12cd34.pdf", None, ".bin", "2026-01-01-INV_7-ab12cd34.bin"),
    # a processable extension keeps the stem as it is
    (None, "report.v2.pdf", ".pdf", "report.v2.pdf"),
    ("2026-01-01-INV.7-ab12cd34.pdf", None, ".jpg", "2026-01-01-INV.7-ab12cd34.jpg"),
])
def test_download_name_flattens_a_double_extension_for_unprocessable_files(stored, original, ext, expected):
    """Gmail and Telegram store any extension. A failed "Invoice.pdf.exe" has
    no filed name, and Windows hides the known .exe."""
    assert download_name(stored, original, 7, ext) == expected

@pytest.mark.parametrize("original", [
    "attachment; filename*=UTF-8''Invoice%E2%80%AEfdp.exe",
    "attachment; filename*=UTF-8''Invoice%E2%81%A6fdp%E2%81%A9.exe",
])
def test_download_name_strips_direction_characters_that_only_appear_after_percent_decoding(original):
    # The raw value is plain ASCII; the override exists only after unquote().
    assert download_name(None, original, 7, ".exe") == "Invoicefdp.exe"

def test_download_name_strips_direction_characters_from_the_filed_name():
    # generate_stored_filename cannot produce this; a restored row can.
    assert download_name("2026-01-01-\u202efdp.exe", None, 7, ".pdf") == "2026-01-01-fdp.pdf"
