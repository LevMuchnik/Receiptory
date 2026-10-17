import os
import re

from backend.atomic import is_contained_name

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


# A Content-Disposition value saved as the filename by whatever produced the
# file (11 web uploads and 3 Telegram files on the live install arrived named
# "attachment; filename=<uuid>.pdf"). Optional quotes and an RFC 5987
# "UTF-8''" charset prefix are stripped too.
_DISPOSITION_RE = re.compile(
    r"""^\s*(?:attachment|inline)\s*;\s*filename\*?\s*=\s*(?:utf-8'[^']*')?["']?(.*?)["']?\s*$""",
    re.IGNORECASE,
)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _clean_original_stem(original_filename: str | None) -> str:
    if not isinstance(original_filename, str):
        return ""
    name = _CONTROL_RE.sub("", original_filename)
    m = _DISPOSITION_RE.match(name)
    if m:
        name = m.group(1)
    # Path parts from either convention: a Windows client can send C:\x\y.pdf.
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    if name.startswith(".") and "." not in name[1:]:
        return ""  # ".pdf": splitext reads it as a dotfile, but it is all extension
    # Leading dots stripped: a download must not land as a hidden file.
    return os.path.splitext(name)[0].lstrip(".").strip()


def download_name(stored_filename: str | None, original_filename: str | None, doc_id: int, ext: str) -> str:
    """The name a downloaded original is saved under.

    `ext` is the extension of the file actually on disk (".jpg", ".bin", or ""
    for a dotless upload), never the one in either name: the bytes are the
    original's, so a .jpg scan must not download as .pdf because its filed
    copy is a PDF.

    1. The filed/ name's stem (2026-10-01-30009-ab12cd34), so a download
       matches what the owner sees in storage/filed/ and sorts by date.
    2. No filed name yet (pending, failed): the original name, with a
       Content-Disposition header value or a path stripped from it.
    3. Neither usable: document-<id>.
    """
    if isinstance(stored_filename, str) and is_contained_name(stored_filename):
        stem = os.path.splitext(stored_filename)[0]
    else:
        stem = ""
    stem = stem or _clean_original_stem(original_filename) or f"document-{doc_id}"
    return f"{stem}{ext}"
