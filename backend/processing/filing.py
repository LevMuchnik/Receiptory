import re

# ASCII-only + fullmatch: \d would accept Unicode digits, and match() with $
# would accept a trailing newline into the filename.
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def generate_stored_filename(receipt_date: str | None, vendor_receipt_id: str | None, file_hash: str) -> str:
    # Both values come verbatim from the LLM — validate type AND shape so a
    # malformed value (e.g. "2026/01/15", or the date emitted as a JSON
    # number) can't inject path separators/control characters or crash the
    # regex with a TypeError.
    date_part = receipt_date if isinstance(receipt_date, str) and _DATE_RE.fullmatch(receipt_date) else "0000-00-00"
    id_part = vendor_receipt_id if vendor_receipt_id and isinstance(vendor_receipt_id, str) else "000000"
    hash_part = file_hash[:8]
    id_part = re.sub(r"[^a-zA-Z0-9\-_.]", "_", id_part)
    return f"{date_part}-{id_part}-{hash_part}.pdf"
