"""URL fetcher module — resolves a URL into a downloadable document file.

Fetch pipeline (stops at first success):
1. HTTP GET with redirect following
2. Content-type routing (PDF/image direct save, HTML link scan, other raw save)
3. HTML link scan for document links
4. Playwright render with auth wall detection and page capture fallback
"""

import asyncio
import logging
import re
import socket
import uuid
from dataclasses import dataclass, field
from ipaddress import ip_address, ip_network
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Mobile User-Agent — many receipt URLs (from Telegram) expect a mobile browser
_MOBILE_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Mobile Safari/537.36"
)

_DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# Private/loopback networks to block (SSRF protection)
_BLOCKED_NETWORKS = [
    ip_network("127.0.0.0/8"),
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("169.254.0.0/16"),  # AWS metadata endpoint
    ip_network("::1/128"),
    ip_network("fc00::/7"),
]


def _is_safe_url(url: str) -> bool:
    """Check that a URL doesn't point to private/internal networks."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    hostname = parsed.hostname
    if not hostname:
        return False
    try:
        resolved = socket.gethostbyname(hostname)
        ip = ip_address(resolved)
        return not any(ip in net for net in _BLOCKED_NETWORKS)
    except (socket.gaierror, ValueError):
        return True  # DNS failure will be caught by httpx


# File extensions considered downloadable documents
DOCUMENT_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".tiff")

# Keywords in URL or link text that indicate a download link. Kept narrow and
# English-only ON PURPOSE: this drives _find_document_links, which follows <a>
# links in the static httpx scan; broadening it there causes false-positive
# link-follows (issue #32 eng-review, finding 3). The button-click path (issue
# #32) uses the separate, broader _CLICK_DOWNLOAD_KEYWORDS below.
DOWNLOAD_KEYWORDS = re.compile(r"download|invoice|receipt", re.IGNORECASE)

# Broader multilingual net for the Playwright button-click download path only
# (issue #32). Matched against a control's visible text, aria-label, title,
# value, and download attribute. Hebrew is first-class: receipt portals like
# mast.co.il label the download control "הורדה".
_CLICK_DOWNLOAD_KEYWORDS = re.compile(
    r"download|\bpdf\b|invoice|receipt|save|"
    r"הורדה|הורד|להורדה|הורדת|חשבונית|קבלה",
    re.IGNORECASE,
)

# Controls we must NEVER click, even if they also match a download keyword —
# these are destructive or money-moving actions on the user's billing account
# (issue #32, finding 1: guarded single-click). Checked against the same
# text/attrs as the keyword net; a match here disqualifies the candidate.
_CLICK_DENYLIST = re.compile(
    r"\bpay\b|payment|checkout|\bdelete\b|remove|submit|"
    r"שלם|תשלום|לתשלום|מחק|הסר|שלח",
    re.IGNORECASE,
)

# Map content-type prefixes to file extensions
CONTENT_TYPE_EXT = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/tiff": ".tiff",
    "image/jpg": ".jpg",
}


@dataclass
class FetchResult:
    file_path: str  # Temp file with fetched content
    content_type: str  # MIME type
    original_url: str  # Source URL
    auth_wall: bool = False  # True if login page detected
    # True when the file is a page.pdf() render of the viewer shell, not a real
    # downloaded document — callers route these to needs_review (issue #32).
    page_capture: bool = False
    method: str = ""  # "direct", "link_follow", "mast_api", "playwright_download", "playwright_download_click", "playwright_network", "playwright_capture"


def _ext_for_content_type(content_type: str) -> str:
    """Return file extension for a content-type string."""
    ct = content_type.split(";")[0].strip().lower()
    if ct in CONTENT_TYPE_EXT:
        return CONTENT_TYPE_EXT[ct]
    if ct.startswith("image/"):
        return "." + ct.split("/")[1]
    return ".bin"


def _is_document_content_type(content_type: str) -> bool:
    """Check if content-type indicates a document (PDF or image)."""
    ct = content_type.split(";")[0].strip().lower()
    return ct == "application/pdf" or ct.startswith("image/")


def _is_html_content_type(content_type: str) -> bool:
    ct = content_type.split(";")[0].strip().lower()
    return "html" in ct


def _content_type_for_ext(ext: str) -> str:
    """Map a file extension back to a content-type (reverse of CONTENT_TYPE_EXT),
    single-sourced so it can't drift from CONTENT_TYPE_EXT."""
    for ct, e in CONTENT_TYPE_EXT.items():
        if e == ext:
            return ct
    return "application/pdf"


# Shared needs_review note for a viewer-shell page.pdf() capture (issue #32),
# used by both the gmail and telegram callers so the wording can't drift.
PAGE_CAPTURE_NOTE = "Captured the viewer page render, not a downloaded file — verify the PDF: {url}"


def _save_response(content: bytes, content_type: str, download_dir: str) -> str:
    """Save response body to a temp file and return the path."""
    ext = _ext_for_content_type(content_type)
    filename = f"{uuid.uuid4().hex}{ext}"
    file_path = str(Path(download_dir) / filename)
    Path(file_path).write_bytes(content)
    return file_path


def _find_document_links(html: str, base_url: str) -> list[str]:
    """Parse HTML and return candidate document URLs."""
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        full_url = urljoin(base_url, href)
        text = a_tag.get_text(strip=True).lower()
        href_lower = href.lower()

        # Check if href ends with a document extension
        parsed_path = urlparse(full_url).path.lower()
        if any(parsed_path.endswith(ext) for ext in DOCUMENT_EXTENSIONS):
            candidates.append(full_url)
            continue

        # Check for download keywords in URL or link text
        if DOWNLOAD_KEYWORDS.search(href_lower) or DOWNLOAD_KEYWORDS.search(text):
            candidates.append(full_url)

    return candidates


async def _follow_link(
    client: httpx.AsyncClient, url: str, download_dir: str, timeout: int
) -> FetchResult | None:
    """Fetch a candidate link and return FetchResult if it yields a document."""
    try:
        resp = await client.get(url, timeout=timeout, follow_redirects=True)
        if resp.status_code >= 400:
            return None
        ct = resp.headers.get("content-type", "application/octet-stream")
        if _is_document_content_type(ct):
            file_path = _save_response(resp.content, ct, download_dir)
            return FetchResult(
                file_path=file_path,
                content_type=ct.split(";")[0].strip(),
                original_url=url,
                method="link_follow",
            )
    except (httpx.TimeoutException, httpx.HTTPError) as exc:
        logger.debug("Failed to follow link %s: %s", url, exc)
    return None


# Minimum seconds to allow for browser rendering. A full SPA (e.g. the
# Adobe Acrobat viewer) needs far longer than a direct HTTP fetch, so the
# short httpx timeout must not starve page.goto.
_MIN_RENDER_TIMEOUT = 30

# Seconds to wait after load for a canvas-based viewer to stream its document.
_DOC_STREAM_SETTLE = 10

# Seconds to wait for any PDF stream to *begin* before treating the page as an
# ordinary (non-viewer) page and bailing out of the settle loop early.
_DOC_STREAM_DETECT = 5

# Hard cap on a captured PDF response body — a malicious page could stream an
# arbitrarily large application/pdf and OOM the single-process NAS deployment.
_MAX_CAPTURE_BYTES = 50 * 1024 * 1024

# Cap on retained application/pdf responses (the listener lives for the whole
# page lifetime on the issue #32 dual-capture path).
_MAX_PDF_RESPONSES = 20


async def _read_streamed_pdf(page, pdf_responses: list) -> bytes | None:
    """Wait (bounded) for a canvas viewer to stream a PDF, return its bytes.

    Polls the responses collected by the page's response listener. Canvas
    viewers fire the document request right after "load", so we first wait a
    short window for any application/pdf response to appear; if none does, this
    is an ordinary page and we return fast rather than stalling the fallback
    path. Once a response exists we keep polling (its body resolves quickly).

    Each body read is timeout-bounded and attempted at most once per response:
    the processing queue is single-process and sequential, so an unbounded (or
    repeated) await on a stalled stream would hang all document processing, not
    just this fetch. response.body() already waits for the body to complete, so
    a response that times out or errors once will not succeed on a retry.
    """
    tried: set = set()
    for i in range(_DOC_STREAM_SETTLE):
        for resp in list(pdf_responses):
            if id(resp) in tried:
                continue
            tried.add(id(resp))
            try:
                body = await asyncio.wait_for(
                    resp.body(), timeout=_DOC_STREAM_SETTLE
                )
            except Exception:
                continue
            if body:
                if len(body) > _MAX_CAPTURE_BYTES:
                    logger.warning(
                        "Captured PDF exceeds %d bytes (%d); discarding: %s",
                        _MAX_CAPTURE_BYTES, len(body), getattr(resp, "url", "?"),
                    )
                    continue
                return body
        # No PDF has even started streaming within the detect window — this is
        # a normal page, so don't wait out the full settle window.
        if not pdf_responses and i >= _DOC_STREAM_DETECT:
            return None
        await page.wait_for_timeout(1000)
    return None


# Seconds to wait for a button-triggered download event (or an inline PDF
# response) after clicking a download control. Longer than the load-time detect
# window (a build-on-click PDF can take a few seconds) but bounded so the
# single-process queue can't hang. Applies ONLY after a matched control is
# clicked, so button-less pages never pay this wait.
_DOC_CLICK_TIMEOUT = 15


def _matches_click_download(text: str) -> bool:
    """True if a control's text/attrs look like a download control AND are not
    a denylisted (money-moving / destructive) action."""
    if not text:
        return False
    if _CLICK_DENYLIST.search(text):
        return False
    return bool(_CLICK_DOWNLOAD_KEYWORDS.search(text))


async def _candidate_label(el) -> str:
    """Concatenate a control's visible text and download-ish attributes."""
    parts = []
    try:
        parts.append(await el.inner_text())
    except Exception:
        pass
    for attr in ("aria-label", "title", "value", "download"):
        try:
            v = await el.get_attribute(attr)
            if v:
                parts.append(v)
        except Exception:
            pass
    return " ".join(p for p in parts if p)


def _save_capture_bytes(content: bytes, download_dir: str, suggested: str | None) -> str | None:
    """Save downloaded bytes to a temp file, enforcing the size cap. Returns the
    path, or None if empty / over the cap."""
    if not content or len(content) > _MAX_CAPTURE_BYTES:
        return None
    ext = Path(suggested or "").suffix.lower()
    if ext not in DOCUMENT_EXTENSIONS:
        ext = ".pdf"
    file_path = str(Path(download_dir) / f"{uuid.uuid4().hex}{ext}")
    Path(file_path).write_bytes(content)
    return file_path


# Controls whose label most strongly signals a document download (ranked first).
_STRONG_CLICK_KEYWORDS = re.compile(r"download|\bpdf\b|הורדה|הורד|להורדה|הורדת", re.IGNORECASE)


async def _try_click_download(page, download_dir: str, pdf_responses: list) -> FetchResult | None:
    """Issue #32: click the single best download control and capture the PDF.

    Dual capture — a client-side SPA may deliver the file either as a browser
    download event (attachment) OR as an inline application/pdf response. The
    page's response listener stays alive (pdf_responses), and the click is
    wrapped in expect_download; whichever fires wins. A mis-click that produces
    neither returns None, so the caller falls back to page.pdf() (needs_review)
    — expect_download is the safety property: no fake success can be filed.
    """
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    try:
        elements = await page.query_selector_all("button, [role=button], a")
    except Exception:
        return None

    ranked: list = []
    for el in elements:
        try:
            if not await el.is_visible() or not await el.is_enabled():
                continue
        except Exception:
            continue
        label = await _candidate_label(el)
        if not _matches_click_download(label):
            continue
        rank = 0 if _STRONG_CLICK_KEYWORDS.search(label) else 1
        ranked.append((rank, el))

    if not ranked:
        return None
    ranked.sort(key=lambda t: t[0])
    best = ranked[0][1]
    page_url = getattr(page, "url", "")

    # Click the single best candidate; capture a download event if one fires.
    download = None
    try:
        async with page.expect_download(timeout=_DOC_CLICK_TIMEOUT * 1000) as dl_info:
            await best.click()
        download = await dl_info.value
    except PlaywrightTimeoutError:
        download = None  # no download event — try the inline-response path below
    except Exception as exc:
        logger.debug("Click-download attempt failed for %s: %s", page_url, exc)
        download = None

    if download is not None:
        try:
            dl_url = download.url or ""
            scheme = urlparse(dl_url).scheme.lower()
            # Re-gate real http(s) download URLs (SSRF). blob:/data: are
            # browser-created — gate them on the page's own origin instead (a
            # safe page could still have fetched internal bytes into a blob).
            if scheme in ("http", "https"):
                if not _is_safe_url(dl_url):
                    logger.warning("Skipping click-download from unsafe host: %s", urlparse(dl_url).hostname)
                    return None
            elif page_url.startswith(("http://", "https://")) and not _is_safe_url(page_url):
                logger.warning("Skipping blob/data click-download from unsafe page origin")
                return None
            # expect_download resolves when the download STARTS; download.path()
            # blocks until it COMPLETES. Bound it — a stalled body would otherwise
            # hang the single-process ingestion queue indefinitely.
            path_str = await asyncio.wait_for(download.path(), timeout=_DOC_CLICK_TIMEOUT)
            # Size-cap on disk BEFORE loading into memory: Playwright streams the
            # download with no limit, so read_bytes() on a huge file would OOM.
            if path_str and Path(path_str).stat().st_size <= _MAX_CAPTURE_BYTES:
                data = Path(path_str).read_bytes()
                saved = _save_capture_bytes(data, download_dir, download.suggested_filename)
                if saved:
                    return FetchResult(
                        file_path=saved,
                        content_type=_content_type_for_ext(Path(saved).suffix.lower()),
                        original_url=page_url,
                        method="playwright_download_click",
                    )
        except Exception as exc:
            logger.debug("Failed saving click-download for %s: %s", page_url, exc)

    # No usable download event — the click may have streamed an inline PDF.
    inline = await _read_streamed_pdf(page, pdf_responses)
    if inline:
        saved = _save_capture_bytes(inline, download_dir, "document.pdf")
        if saved:
            return FetchResult(
                file_path=saved,
                content_type="application/pdf",
                original_url=page_url,
                method="playwright_download_click",
            )
    return None


async def _playwright_fetch(
    url: str, download_dir: str, timeout: int, user_agent: str | None = None,
) -> FetchResult | None:
    """Use Playwright headless Chromium to render a page and extract content."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning(
            "playwright is not installed; skipping browser-based fetch for %s", url
        )
        return None

    # Browser rendering needs more time than a direct fetch; floor the timeout.
    nav_timeout = max(int(timeout), _MIN_RENDER_TIMEOUT)

    # Canvas-based PDF viewers (Adobe Acrobat share links, etc.) never expose
    # the document as an <a> link and render nothing capturable via page.pdf();
    # they stream the real bytes as an application/pdf network response. Collect
    # those responses so we can save the actual document.
    pdf_responses: list = []

    def _on_response(resp) -> None:
        try:
            ct = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if ct != "application/pdf":
                return
            # The browser fetches sub-resources from any host during render;
            # apply the same SSRF gate as the top-level URL so a rendered page
            # can't cause us to persist an internal endpoint's bytes.
            if not _is_safe_url(resp.url):
                logger.warning("Skipping captured PDF from unsafe host: %s", resp.url)
                return
            # Cheap size guard when the server declares a length (the read in
            # _read_streamed_pdf enforces the cap for chunked responses too).
            clen = resp.headers.get("content-length", "")
            if clen.isdigit() and int(clen) > _MAX_CAPTURE_BYTES:
                logger.warning("Skipping oversized captured PDF (%s bytes): %s", clen, resp.url)
                return
            # Bound growth: the listener stays attached for the whole page
            # lifetime (issue #32 dual capture), so cap retained responses so a
            # page emitting many PDF responses can't grow the list unboundedly.
            if len(pdf_responses) < _MAX_PDF_RESPONSES:
                pdf_responses.append(resp)
        except Exception:
            pass

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                ua = user_agent or _MOBILE_USER_AGENT
                is_mobile = ua == _MOBILE_USER_AGENT
                viewport = {"width": 412, "height": 915} if is_mobile else {"width": 1280, "height": 900}
                page = await browser.new_page(
                    user_agent=ua,
                    viewport=viewport,
                    is_mobile=is_mobile,
                )
                page.on("response", _on_response)
                # "networkidle" never settles on SPAs that poll or stream
                # (the Adobe viewer keeps connections open indefinitely), so
                # goto would always time out. Wait for "load" instead.
                await page.goto(url, timeout=nav_timeout * 1000, wait_until="load")

                # Give a canvas-based viewer a bounded window to stream its
                # document, then save the first readable PDF response.
                streamed_pdf = await _read_streamed_pdf(page, pdf_responses)
                # NOTE: the response listener stays attached past here so the
                # button-click path (issue #32) can still catch an inline PDF
                # response the click triggers. It is torn down with the page.
                if streamed_pdf:
                    file_path = _save_response(
                        streamed_pdf, "application/pdf", download_dir
                    )
                    return FetchResult(
                        file_path=file_path,
                        content_type="application/pdf",
                        original_url=url,
                        method="playwright_network",
                    )

                # A PDF stream was detected but its bytes were unreadable
                # (stalled, evicted, or over the size cap). This is a canvas
                # viewer whose document we couldn't capture — treat it as a
                # fetch failure rather than filing a blank page.pdf() capture
                # of the viewer shell as if it were the real document.
                if pdf_responses:
                    logger.warning(
                        "PDF stream detected for %s but no readable body; not capturing blank page", url
                    )
                    return None

                # Auth wall detection
                password_fields = await page.query_selector_all(
                    'input[type="password"]'
                )
                if password_fields:
                    pdf_bytes = await page.pdf()
                    file_path = _save_response(
                        pdf_bytes, "application/pdf", download_dir
                    )
                    return FetchResult(
                        file_path=file_path,
                        content_type="application/pdf",
                        original_url=url,
                        auth_wall=True,
                        method="playwright_capture",
                    )

                # Scan rendered DOM for download links
                html = await page.content()
                candidates = _find_document_links(html, url)
                async with httpx.AsyncClient(headers={"User-Agent": ua}) as client:
                    for link_url in candidates:
                        result = await _follow_link(
                            client, link_url, download_dir, timeout
                        )
                        if result:
                            result.method = "playwright_download"
                            return result

                # Issue #32: the real PDF may sit behind a JS download button
                # (no <a href>, no load-time stream) — e.g. mast.co.il/bill-viewer.
                # Click the best download control and capture what it produces.
                click_result = await _try_click_download(page, download_dir, pdf_responses)
                if click_result is not None:
                    return click_result

                # Fallback: capture the page as PDF. This is a render of the
                # viewer SHELL, not a real document — flag page_capture so the
                # caller routes it to needs_review instead of filing it as the
                # real bill (issue #32).
                pdf_bytes = await page.pdf()
                file_path = _save_response(
                    pdf_bytes, "application/pdf", download_dir
                )
                return FetchResult(
                    file_path=file_path,
                    content_type="application/pdf",
                    original_url=url,
                    method="playwright_capture",
                    page_capture=True,
                )
            finally:
                await browser.close()
    except Exception as exc:
        logger.error("Playwright fetch failed for %s: %s", url, exc)
        return None


# --- Site handler: mast.co.il municipal bill viewer (issue #32) ---------------
# The mast bill-viewer is an Angular SPA (shadow DOM + iframes + invisible
# reCAPTCHA v3) that renders nothing capturable and hides the download behind a
# JS flow — the generic browser path only ever captures the viewer shell. But
# the SPA fetches the bill from a public JSON API keyed by the SAME guid that is
# in the viewer URL, and that JSON carries a direct `pdfLink`. So we skip the
# browser entirely and fetch the real PDF deterministically over httpx.
_MAST_API = "https://api.mast.co.il/mast/api/Stubs/GetStubsByGuidForPresentingStubs"
_MAST_API_TIMEOUT = 15   # floor for the JSON API GET
_MAST_PDF_TIMEOUT = 30   # floor for the Azure-blob PDF GET


def _mast_guid(url: str) -> str | None:
    """Return the decoded bill guid if `url` is a mast.co.il bill-viewer link."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in ("mast.co.il", "www.mast.co.il"):
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "bill-viewer":
        return unquote(parts[1])  # path segment is percent-encoded
    return None


async def _fetch_mast_bill(url: str, download_dir: str, timeout: int) -> FetchResult | None:
    """Resolve a mast bill-viewer URL to its real PDF via the mast JSON API.

    Returns None on any failure so the caller falls through to the generic
    fetch path (which ends at page.pdf() -> page_capture -> needs_review).
    """
    guid = _mast_guid(url)
    if not guid:
        return None
    headers = {
        "User-Agent": _DESKTOP_USER_AGENT,
        "Accept": "application/json",
        "Origin": "https://www.mast.co.il",
        "Referer": "https://www.mast.co.il/",
    }
    # follow_redirects=False: _is_safe_url gates only the initial host, and httpx
    # does NOT re-run it on redirect hops, so a 3xx to an internal host would
    # bypass the SSRF check. The mast API + Azure blob are direct URLs; a redirect
    # is unexpected and safer to refuse. The parse loop is INSIDE the try so any
    # unexpected-but-valid JSON shape (array/string) degrades to None instead of
    # raising AttributeError up through the unguarded gmail caller (issue #32).
    try:
        async with httpx.AsyncClient(headers=headers) as client:
            resp = await client.get(
                _MAST_API, params={"guid": guid},
                timeout=max(int(timeout), _MAST_API_TIMEOUT), follow_redirects=False,
            )
        if resp.status_code >= 400:
            logger.warning("mast API HTTP %d", resp.status_code)
            return None
        if len(resp.content) > _MAX_CAPTURE_BYTES:
            logger.warning("mast API body too large (%d bytes)", len(resp.content))
            return None
        data = resp.json()
        if not isinstance(data, dict):
            logger.warning("mast API returned unexpected JSON shape")
            return None
        # Collect candidate PDF links across all stubs for this period. A period
        # can carry more than one stub; we return one file per URL, so take the
        # first usable link and log if others were dropped.
        links: list[str] = []
        for stub in (data.get("stubExtentionList") or []):
            if not isinstance(stub, dict):
                continue
            for key in ("pdfLink", "fileUrlAttachment", "attachmentFileUrl"):
                val = stub.get(key)
                if val and isinstance(val, str) and val.startswith(("http://", "https://")):
                    links.append(val)
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.warning("mast API fetch/parse failed: %s", exc)
        return None

    if not links:
        logger.warning("mast API returned no pdfLink")
        return None
    if len(links) > 1:
        logger.info("mast bill has %d stub PDFs; ingesting the first", len(links))

    pdf_url = links[0]
    if not _is_safe_url(pdf_url):
        # Log host only — the pdfLink carries an Azure SAS token in its query.
        logger.warning("mast pdfLink points to unsafe host: %s", urlparse(pdf_url).hostname)
        return None
    try:
        async with httpx.AsyncClient(headers={"User-Agent": _DESKTOP_USER_AGENT}) as client:
            pr = await client.get(
                pdf_url, timeout=max(int(timeout), _MAST_PDF_TIMEOUT), follow_redirects=False,
            )
        if pr.status_code >= 400 or len(pr.content) > _MAX_CAPTURE_BYTES:
            return None
        ct = pr.headers.get("content-type", "application/pdf")
        file_path = _save_response(pr.content, ct, download_dir)
        return FetchResult(
            file_path=file_path,
            content_type=ct.split(";")[0].strip(),
            original_url=url,
            method="mast_api",
        )
    except httpx.HTTPError as exc:
        logger.warning("mast pdfLink fetch failed: %s", exc)
        return None


async def fetch_url(
    url: str, download_dir: str | Path, timeout: int = 5, user_agent: str | None = None,
) -> FetchResult | None:
    """Fetch a URL and return a FetchResult with the downloaded file.

    Tries direct HTTP fetch, HTML link scanning, then Playwright rendering.
    Returns None on failure (all errors are non-fatal).
    """
    download_dir = str(download_dir)
    Path(download_dir).mkdir(parents=True, exist_ok=True)

    # SSRF protection: block private/internal network URLs
    if not _is_safe_url(url):
        logger.warning("Blocked unsafe URL (private/internal network): %s", url)
        return None

    # Site handler: mast.co.il bill viewer resolves to its real PDF via the mast
    # JSON API (issue #32). Tried first; falls through to the generic path on any
    # failure so a mast API change can't strand ingestion.
    if _mast_guid(url):
        mast_result = await _fetch_mast_bill(url, download_dir, timeout)
        if mast_result is not None:
            return mast_result

    # Step 1: HTTP fetch (mobile UA — many receipt links expect mobile browser)
    ua = user_agent or _MOBILE_USER_AGENT
    _headers = {"User-Agent": ua}
    try:
        async with httpx.AsyncClient(headers=_headers) as client:
            resp = await client.get(url, timeout=timeout, follow_redirects=True)
    except httpx.TimeoutException:
        logger.warning("Timeout fetching URL: %s", url)
        return None
    except httpx.HTTPError as exc:
        logger.warning("HTTP error fetching URL %s: %s", url, exc)
        return None

    if resp.status_code >= 400:
        logger.warning("HTTP %d for URL: %s", resp.status_code, url)
        return None

    content_type = resp.headers.get("content-type", "application/octet-stream")
    ct_base = content_type.split(";")[0].strip().lower()

    # Step 2: Content-type routing
    if _is_document_content_type(content_type):
        # Direct document — save and return
        file_path = _save_response(resp.content, content_type, download_dir)
        return FetchResult(
            file_path=file_path,
            content_type=ct_base,
            original_url=url,
            method="direct",
        )

    if _is_html_content_type(content_type):
        # Step 3: HTML link scan
        html_text = resp.text
        candidates = _find_document_links(html_text, url)
        async with httpx.AsyncClient(headers=_headers) as client:
            for link_url in candidates:
                result = await _follow_link(client, link_url, download_dir, timeout)
                if result:
                    return result

        # Step 4: Playwright render
        return await _playwright_fetch(url, download_dir, timeout, user_agent=ua)

    # Other content type — save raw, let downstream handle it
    file_path = _save_response(resp.content, content_type, download_dir)
    return FetchResult(
        file_path=file_path,
        content_type=ct_base,
        original_url=url,
        method="direct",
    )
