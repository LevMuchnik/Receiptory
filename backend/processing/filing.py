import os
import re
import unicodedata
from urllib.parse import unquote

from backend.atomic import PROCESSABLE_EXTENSIONS, is_contained_name

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
# "attachment; filename=<uuid>.pdf").
_DISPOSITION_RE = re.compile(r"\s*(?:attachment|inline)\s*(;.*)", re.IGNORECASE | re.DOTALL)
# One ;key=value parameter; a quoted value may itself contain ";".
_PARAM_RE = re.compile(r';\s*([^\s=;]+)\s*=\s*("(?:[^"\\]|\\.)*"|[^;]*)')
# The only charsets RFC 5987 lets filename* declare. Any other label is the
# sender's choice of codec: utf-7 or unicode_escape decode to a lone surrogate,
# which Starlette's quote() cannot encode (a 500 on the download).
_RFC5987_CHARSETS = {"utf-8", "iso-8859-1"}
# Extensions whose download keeps a dotted stem. .html is processed but left
# out: a saved "Invoice.pdf.html" opens in the browser as a page, and shows as
# "Invoice.pdf" where the OS hides known extensions.
_KEEP_DOTS_EXTENSIONS = PROCESSABLE_EXTENSIONS - {".html", ".htm"}


def _visible(text: str) -> str:
    """Drop control (Cc: C0, DEL, C1), format (Cf) and surrogate (Cs) characters.

    Cf is the one that matters: original_filename is chosen by whoever sent the
    file, and a right-to-left override can make "Invoice<RLO>fdp.exe" display
    as "Invoiceexe.pdf" while the download keeps the real .exe on disk. Cs is
    a backstop: a lone surrogate cannot be encoded into the response header.
    """
    return "".join(ch for ch in text if unicodedata.category(ch) not in ("Cc", "Cf", "Cs"))


def _disposition_filename(value: str) -> str | None:
    """The filename a Content-Disposition value names, or None if it is not one.

    filename* (RFC 5987, percent-encoded in a declared charset) wins over
    filename, as RFC 6266 says; any other parameter is ignored. A value with
    neither parameter is somebody's literal file name, not a header.
    """
    m = _DISPOSITION_RE.fullmatch(value)
    if not m:
        return None
    plain = star = None
    for key, val in _PARAM_RE.findall(m.group(1)):
        key = key.lower()
        if val.startswith('"') and val.endswith('"') and len(val) >= 2:
            val = re.sub(r"\\(.)", r"\1", val[1:-1])
        else:
            val = val.strip().strip("'")
        if key == "filename*" and star is None:
            if val.count("'") >= 2:
                charset, _, rest = val.partition("'")
                _lang, _, encoded = rest.partition("'")
            else:  # no charset'lang' prefix: the whole value is the encoded name
                charset, encoded = "utf-8", val
            charset = charset.strip().lower()
            if charset not in _RFC5987_CHARSETS:
                charset = "utf-8"
            star = unquote(encoded, encoding=charset, errors="replace")
        elif key == "filename" and plain is None:
            plain = val
    if star is None and plain is None:
        return None
    return star or plain or ""


def _plain_stem(name: str) -> str:
    name = name.strip()
    if name.startswith(".") and "." not in name[1:]:
        return ""  # ".pdf": splitext reads it as a dotfile, but it is all extension
    stem = os.path.splitext(name)[0]
    # A download must not land as a hidden file: strip dots and whitespace in
    # turn until neither leads (" .x", ". .x").
    while True:
        cleaned = stem.strip().lstrip(".")
        if cleaned == stem:
            return stem
        stem = cleaned


def _clean_original_stem(original_filename: str | None) -> str:
    if not isinstance(original_filename, str):
        return ""
    name = _visible(original_filename)
    from_header = _disposition_filename(name)
    if from_header is not None:
        name = _visible(from_header)
    # Path parts from either convention: a Windows client can send C:\x\y.pdf.
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    return _plain_stem(name)


def download_name(stored_filename: str | None, original_filename: str | None, doc_id: int, ext: str) -> str:
    """The name a downloaded original is saved under.

    `ext` is the extension of the file actually on disk (".jpg", ".bin", or ""
    for a dotless upload), never the one in either name: the bytes are the
    original's, so a .jpg scan must not download as .pdf because its filed
    copy is a PDF. For an extension the pipeline does not process, and for
    .html, dots in the stem become underscores, so no double extension
    survives.

    1. The filed/ name's stem (2026-10-01-30009-ab12cd34), so a download
       matches what the owner sees in storage/filed/ and sorts by date.
    2. No filed name yet (pending, failed): the original name, with a
       Content-Disposition header value or a path stripped from it.
    3. Neither usable: document-<id>.
    """
    if isinstance(stored_filename, str) and is_contained_name(stored_filename):
        stem = _plain_stem(_visible(stored_filename))
    else:
        stem = ""
    stem = stem or _clean_original_stem(original_filename) or f"document-{doc_id}"
    # The extension is the one on disk, so the sender chose it too: a non-UTF-8
    # byte in a watched-folder name arrives as a lone surrogate, which the
    # response header cannot encode.
    ext = _visible(ext)
    if ext.lower() not in _KEEP_DOTS_EXTENSIONS:
        # Gmail and Telegram store any extension, and a file the pipeline cannot
        # process never gets a filed name. A dotted stem would let
        # "Invoice.pdf.exe" pose as a PDF where the OS hides known extensions.
        stem = stem.replace(".", "_")
    return f"{stem}{ext}"
