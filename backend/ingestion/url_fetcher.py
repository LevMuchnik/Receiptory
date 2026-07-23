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
from urllib.parse import urljoin, urlparse

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

# Keywords in URL or link text that indicate a download link
DOWNLOAD_KEYWORDS = re.compile(r"download|invoice|receipt", re.IGNORECASE)

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
    method: str = ""  # "direct", "link_follow", "playwright_download", "playwright_network", "playwright_capture"


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
                page.remove_listener("response", _on_response)
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

                # Fallback: capture the page as PDF
                pdf_bytes = await page.pdf()
                file_path = _save_response(
                    pdf_bytes, "application/pdf", download_dir
                )
                return FetchResult(
                    file_path=file_path,
                    content_type="application/pdf",
                    original_url=url,
                    method="playwright_capture",
                )
            finally:
                await browser.close()
    except Exception as exc:
        logger.error("Playwright fetch failed for %s: %s", url, exc)
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
