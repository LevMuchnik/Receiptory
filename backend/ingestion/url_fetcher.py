"""URL fetcher module — resolves a URL into a downloadable document file.

Fetch pipeline (stops at first success):
1. HTTP GET with redirect following
2. Content-type routing (PDF/image direct save, HTML link scan, other raw save)
3. HTML link scan for document links
4. Playwright render with auth wall detection and page capture fallback
"""

import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

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
    method: str = ""  # "direct", "link_follow", "playwright_download", "playwright_capture"


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


async def _playwright_fetch(
    url: str, download_dir: str, timeout: int
) -> FetchResult | None:
    """Use Playwright headless Chromium to render a page and extract content."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning(
            "playwright is not installed; skipping browser-based fetch for %s", url
        )
        return None

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(url, timeout=timeout * 1000, wait_until="networkidle")

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
                for link_url in candidates:
                    # Try to download via httpx (simpler than intercepting Playwright downloads)
                    async with httpx.AsyncClient() as client:
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
    url: str, download_dir: str | Path, timeout: int = 5
) -> FetchResult | None:
    """Fetch a URL and return a FetchResult with the downloaded file.

    Tries direct HTTP fetch, HTML link scanning, then Playwright rendering.
    Returns None on failure (all errors are non-fatal).
    """
    download_dir = str(download_dir)
    Path(download_dir).mkdir(parents=True, exist_ok=True)

    # Step 1: HTTP fetch
    try:
        async with httpx.AsyncClient() as client:
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
        async with httpx.AsyncClient() as client:
            for link_url in candidates:
                result = await _follow_link(client, link_url, download_dir, timeout)
                if result:
                    return result

        # Step 4: Playwright render
        return await _playwright_fetch(url, download_dir, timeout)

    # Other content type — save raw, let downstream handle it
    file_path = _save_response(resp.content, content_type, download_dir)
    return FetchResult(
        file_path=file_path,
        content_type=ct_base,
        original_url=url,
        method="direct",
    )
