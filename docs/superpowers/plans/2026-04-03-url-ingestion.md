# URL-Based Document Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable Telegram and email ingestion pipelines to accept text messages and emails containing URLs to receipts/invoices, fetch the linked documents, and process them through the existing pipeline.

**Architecture:** New `url_fetcher` module handles URL→file resolution (HTTP fetch → HTML link scan → Playwright render). New `url_triage` module uses LLM to decide which URLs/attachments to ingest. Both Telegram and email pipelines are modified to detect URLs and route through triage before fetching. A new `source_url` column tracks document provenance.

**Tech Stack:** httpx (already in deps), beautifulsoup4, playwright (headless Chromium), litellm (existing)

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `backend/ingestion/url_fetcher.py` | URL→file resolution: HTTP fetch, HTML link scan, Playwright render |
| Create | `backend/ingestion/url_triage.py` | LLM-based triage: decide which URLs/attachments to ingest |
| Create | `tests/test_url_fetcher.py` | Unit tests for URL fetcher |
| Create | `tests/test_url_triage.py` | Unit tests for URL triage |
| Create | `tests/test_telegram_urls.py` | Tests for Telegram text message URL handling |
| Create | `tests/test_gmail_urls.py` | Tests for email URL handling |
| Create | `migrations/004_add_source_url.sql` | Add source_url column to documents |
| Modify | `backend/ingestion/telegram.py` | Add `handle_text` handler for URL messages |
| Modify | `backend/ingestion/gmail.py` | Replace attachment logic with triage-based flow |
| Modify | `backend/config.py` | Add `url_fetch_timeout` setting |
| Modify | `pyproject.toml` | Add beautifulsoup4, playwright dependencies |
| Modify | `Dockerfile` | Install Playwright + Chromium |

---

### Task 1: Database Migration — Add `source_url` Column

**Files:**
- Create: `migrations/004_add_source_url.sql`

- [ ] **Step 1: Write the migration**

```sql
-- 004_add_source_url.sql
-- Add source_url column to track documents fetched from URLs
ALTER TABLE documents ADD COLUMN source_url TEXT DEFAULT NULL;
```

- [ ] **Step 2: Verify migration applies**

Run: `uv run python -c "from backend.database import init_db; init_db('/tmp/test_mig.db'); print('OK')"`
Expected: OK (no errors)

- [ ] **Step 3: Commit**

```bash
git add migrations/004_add_source_url.sql
git commit -m "feat: add source_url column to documents table"
```

---

### Task 2: Configuration — Add `url_fetch_timeout` Setting

**Files:**
- Modify: `backend/config.py:10-71` (DEFAULTS dict)

- [ ] **Step 1: Write the failing test**

Create `tests/test_url_fetch_config.py`:

```python
"""Test url_fetch_timeout configuration."""
from backend.config import get_setting, DEFAULTS
from backend.database import init_db


def test_url_fetch_timeout_default(db_path):
    assert DEFAULTS["url_fetch_timeout"] == 5
    assert get_setting("url_fetch_timeout") == 5


def test_url_fetch_timeout_env(db_path, monkeypatch):
    monkeypatch.setenv("RECEIPTORY_URL_FETCH_TIMEOUT", "10")
    assert get_setting("url_fetch_timeout") == 10
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_url_fetch_config.py -v`
Expected: FAIL — KeyError on `url_fetch_timeout`

- [ ] **Step 3: Add the setting to DEFAULTS**

In `backend/config.py`, add to the `DEFAULTS` dict after `"watched_folder_poll_interval": 10,`:

```python
    "url_fetch_timeout": 5,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_url_fetch_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/config.py tests/test_url_fetch_config.py
git commit -m "feat: add url_fetch_timeout setting (default 5s)"
```

---

### Task 3: URL Fetcher Module

**Files:**
- Create: `backend/ingestion/url_fetcher.py`
- Create: `tests/test_url_fetcher.py`

- [ ] **Step 1: Write failing tests for the URL fetcher**

Create `tests/test_url_fetcher.py`:

```python
"""Tests for URL fetcher module."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path
from backend.ingestion.url_fetcher import fetch_url, FetchResult


@pytest.fixture
def tmp_download_dir(tmp_path):
    return tmp_path


class TestDirectDownload:
    """Test fetching URLs that return files directly."""

    @pytest.mark.asyncio
    async def test_fetch_pdf_direct(self, tmp_download_dir):
        """URL returning application/pdf should save directly."""
        pdf_bytes = b"%PDF-1.4 fake pdf content"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/pdf"}
        mock_response.content = pdf_bytes
        mock_response.raise_for_status = MagicMock()

        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await fetch_url("https://example.com/receipt.pdf", tmp_download_dir, timeout=5)

        assert result is not None
        assert result.method == "direct"
        assert result.auth_wall is False
        assert Path(result.file_path).exists()
        assert Path(result.file_path).read_bytes() == pdf_bytes

    @pytest.mark.asyncio
    async def test_fetch_image_direct(self, tmp_download_dir):
        """URL returning image/* should save directly."""
        img_bytes = b"\x89PNG\r\n\x1a\n fake image"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "image/png"}
        mock_response.content = img_bytes
        mock_response.raise_for_status = MagicMock()

        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await fetch_url("https://example.com/receipt.png", tmp_download_dir, timeout=5)

        assert result is not None
        assert result.method == "direct"
        assert Path(result.file_path).suffix == ".png"


class TestHtmlLinkScan:
    """Test fetching HTML pages that contain download links."""

    @pytest.mark.asyncio
    async def test_html_with_pdf_link(self, tmp_download_dir):
        """HTML page containing a PDF download link should follow the link."""
        html = b'<html><body><a href="https://example.com/invoice.pdf">Download PDF</a></body></html>'
        pdf_bytes = b"%PDF-1.4 invoice content"

        html_response = MagicMock()
        html_response.status_code = 200
        html_response.headers = {"content-type": "text/html; charset=utf-8"}
        html_response.content = html
        html_response.text = html.decode()
        html_response.raise_for_status = MagicMock()

        pdf_response = MagicMock()
        pdf_response.status_code = 200
        pdf_response.headers = {"content-type": "application/pdf"}
        pdf_response.content = pdf_bytes
        pdf_response.raise_for_status = MagicMock()

        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=[html_response, pdf_response])
            mock_client_cls.return_value = mock_client

            result = await fetch_url("https://example.com/receipt-page", tmp_download_dir, timeout=5)

        assert result is not None
        assert result.method == "link_follow"
        assert Path(result.file_path).read_bytes() == pdf_bytes

    @pytest.mark.asyncio
    async def test_html_without_links_falls_through_to_playwright(self, tmp_download_dir):
        """HTML page with no download links should fall through to Playwright."""
        html = b"<html><body><p>Your receipt</p></body></html>"
        pdf_bytes = b"%PDF-captured"

        html_response = MagicMock()
        html_response.status_code = 200
        html_response.headers = {"content-type": "text/html; charset=utf-8"}
        html_response.content = html
        html_response.text = html.decode()
        html_response.raise_for_status = MagicMock()

        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=html_response)
            mock_client_cls.return_value = mock_client

            with patch("backend.ingestion.url_fetcher._playwright_fetch") as mock_pw:
                mock_pw.return_value = FetchResult(
                    file_path=str(tmp_download_dir / "captured.pdf"),
                    content_type="application/pdf",
                    original_url="https://example.com/receipt-page",
                    auth_wall=False,
                    method="playwright_capture",
                )
                (tmp_download_dir / "captured.pdf").write_bytes(pdf_bytes)

                result = await fetch_url("https://example.com/receipt-page", tmp_download_dir, timeout=5)

        assert result is not None
        assert result.method == "playwright_capture"


class TestPlaywrightFetch:
    """Test Playwright-based page rendering."""

    @pytest.mark.asyncio
    async def test_auth_wall_detection(self, tmp_download_dir):
        """Pages with password fields should be flagged as auth walls."""
        html = '<html><body><form><input type="password" name="pwd"/></form></body></html>'

        with patch("backend.ingestion.url_fetcher._playwright_fetch") as mock_pw:
            captured_pdf = tmp_download_dir / "auth_page.pdf"
            captured_pdf.write_bytes(b"%PDF-auth-wall")
            mock_pw.return_value = FetchResult(
                file_path=str(captured_pdf),
                content_type="application/pdf",
                original_url="https://example.com/login",
                auth_wall=True,
                method="playwright_capture",
            )

            # Simulate: HTTP fetch returns HTML, no download links, Playwright detects auth wall
            html_response = MagicMock()
            html_response.status_code = 200
            html_response.headers = {"content-type": "text/html"}
            html_response.content = html.encode()
            html_response.text = html
            html_response.raise_for_status = MagicMock()

            with patch("backend.ingestion.url_fetcher.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                mock_client.get = AsyncMock(return_value=html_response)
                mock_client_cls.return_value = mock_client

                result = await fetch_url("https://example.com/login", tmp_download_dir, timeout=5)

        assert result is not None
        assert result.auth_wall is True


class TestErrorHandling:
    """Test error cases."""

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self, tmp_download_dir):
        """Timeout should return None, not raise."""
        import httpx

        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            mock_client_cls.return_value = mock_client

            result = await fetch_url("https://example.com/slow", tmp_download_dir, timeout=1)

        assert result is None

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self, tmp_download_dir):
        """HTTP 4xx/5xx should return None."""
        import httpx

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("Not Found", request=MagicMock(), response=mock_response)
        )

        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await fetch_url("https://example.com/missing", tmp_download_dir, timeout=5)

        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_url_fetcher.py -v`
Expected: FAIL — `url_fetcher` module does not exist

- [ ] **Step 3: Implement the URL fetcher**

Create `backend/ingestion/url_fetcher.py`:

```python
"""URL fetcher: resolve a URL into a downloadable document file.

Pipeline: HTTP fetch → HTML link scan → Playwright render.
"""
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DOCUMENT_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif"}
DIRECT_CONTENT_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/tiff", "image/webp"}


@dataclass
class FetchResult:
    file_path: str           # Temp file with fetched content
    content_type: str        # MIME type
    original_url: str        # Source URL
    auth_wall: bool = False  # True if login page detected
    method: str = ""         # "direct", "link_follow", "playwright_download", "playwright_capture"


def _ext_from_content_type(content_type: str) -> str:
    """Map content-type to file extension."""
    ct = content_type.split(";")[0].strip().lower()
    mapping = {
        "application/pdf": ".pdf",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/tiff": ".tiff",
        "image/webp": ".webp",
        "text/html": ".html",
    }
    return mapping.get(ct, ".bin")


def _find_download_links(html: str, base_url: str) -> list[str]:
    """Scan HTML for links that likely point to downloadable documents."""
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        full_url = urljoin(base_url, href)
        link_text = a_tag.get_text(strip=True).lower()

        # Check if href points to a document file
        path_lower = href.lower().split("?")[0]
        if any(path_lower.endswith(ext) for ext in DOCUMENT_EXTENSIONS):
            candidates.append(full_url)
            continue

        # Check if link text suggests a download
        download_keywords = {"download", "invoice", "receipt", "pdf", "print"}
        if any(kw in link_text for kw in download_keywords):
            candidates.append(full_url)

    return candidates


async def _playwright_fetch(url: str, download_dir: str, timeout: int) -> FetchResult | None:
    """Use Playwright to render a JS-heavy page and capture/download the document."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("Playwright not installed — skipping JS rendering for %s", url)
        return None

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, timeout=timeout * 1000, wait_until="networkidle")

            # Check for auth wall (password input field)
            password_fields = await page.query_selector_all('input[type="password"]')
            if password_fields:
                pdf_path = os.path.join(download_dir, "auth_wall_capture.pdf")
                await page.pdf(path=pdf_path)
                await browser.close()
                return FetchResult(
                    file_path=pdf_path,
                    content_type="application/pdf",
                    original_url=url,
                    auth_wall=True,
                    method="playwright_capture",
                )

            # Scan rendered DOM for download links
            html = await page.content()
            download_links = _find_download_links(html, url)
            if download_links:
                # Try to download the first candidate
                try:
                    resp = await page.request.get(download_links[0])
                    ct = resp.headers.get("content-type", "")
                    if any(dt in ct for dt in ["pdf", "image"]):
                        ext = _ext_from_content_type(ct)
                        file_path = os.path.join(download_dir, f"pw_download{ext}")
                        with open(file_path, "wb") as f:
                            f.write(await resp.body())
                        await browser.close()
                        return FetchResult(
                            file_path=file_path,
                            content_type=ct.split(";")[0].strip(),
                            original_url=url,
                            method="playwright_download",
                        )
                except Exception as e:
                    logger.debug("Playwright download link failed: %s", e)

            # Fallback: capture the rendered page as PDF
            pdf_path = os.path.join(download_dir, "page_capture.pdf")
            await page.pdf(path=pdf_path)
            await browser.close()
            return FetchResult(
                file_path=pdf_path,
                content_type="application/pdf",
                original_url=url,
                method="playwright_capture",
            )

    except Exception as e:
        logger.error("Playwright fetch failed for %s: %s", url, e)
        return None


async def fetch_url(url: str, download_dir: str | Path, timeout: int = 5) -> FetchResult | None:
    """Fetch a URL and return the document file, or None on failure.

    Pipeline:
    1. HTTP GET — if response is PDF/image, save directly
    2. If HTML — scan for download links, follow if found
    3. If no download link — use Playwright to render and capture
    """
    download_dir = str(download_dir)
    os.makedirs(download_dir, exist_ok=True)

    # Step 1: HTTP fetch
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.TimeoutException:
        logger.warning("URL fetch timeout: %s", url)
        return None
    except httpx.HTTPStatusError as e:
        logger.warning("URL fetch HTTP error %s: %s", e.response.status_code, url)
        return None
    except Exception as e:
        logger.warning("URL fetch failed for %s: %s", url, e)
        return None

    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()

    # Step 2: Direct document download
    if content_type in DIRECT_CONTENT_TYPES:
        ext = _ext_from_content_type(content_type)
        file_path = os.path.join(download_dir, f"fetched{ext}")
        with open(file_path, "wb") as f:
            f.write(response.content)
        return FetchResult(
            file_path=file_path,
            content_type=content_type,
            original_url=url,
            method="direct",
        )

    # Step 3: HTML — scan for download links
    if "html" in content_type:
        download_links = _find_download_links(response.text, url)
        if download_links:
            # Try to follow the first download link
            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
                    link_response = await client.get(download_links[0])
                    link_response.raise_for_status()
                    link_ct = link_response.headers.get("content-type", "").split(";")[0].strip().lower()
                    if link_ct in DIRECT_CONTENT_TYPES:
                        ext = _ext_from_content_type(link_ct)
                        file_path = os.path.join(download_dir, f"followed{ext}")
                        with open(file_path, "wb") as f:
                            f.write(link_response.content)
                        return FetchResult(
                            file_path=file_path,
                            content_type=link_ct,
                            original_url=url,
                            method="link_follow",
                        )
            except Exception as e:
                logger.debug("Link follow failed for %s: %s", download_links[0], e)

        # Step 4: Playwright fallback
        result = await _playwright_fetch(url, download_dir, timeout)
        if result:
            return result

        # Final fallback: save the raw HTML
        html_path = os.path.join(download_dir, "fetched.html")
        with open(html_path, "wb") as f:
            f.write(response.content)
        return FetchResult(
            file_path=html_path,
            content_type="text/html",
            original_url=url,
            method="direct",
        )

    # Unknown content type — save raw
    ext = _ext_from_content_type(content_type)
    file_path = os.path.join(download_dir, f"fetched{ext}")
    with open(file_path, "wb") as f:
        f.write(response.content)
    return FetchResult(
        file_path=file_path,
        content_type=content_type,
        original_url=url,
        method="direct",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_url_fetcher.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/ingestion/url_fetcher.py tests/test_url_fetcher.py
git commit -m "feat: add URL fetcher module with HTTP, link scan, and Playwright fallback"
```

---

### Task 4: URL Triage Module

**Files:**
- Create: `backend/ingestion/url_triage.py`
- Create: `tests/test_url_triage.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_url_triage.py`:

```python
"""Tests for URL triage module (LLM-based decision making)."""
import json
import pytest
from unittest.mock import patch, MagicMock
from backend.ingestion.url_triage import triage_telegram_urls, triage_email, TriageResult


class TestTriageTelegramUrls:
    """Test Telegram URL triage."""

    @pytest.mark.asyncio
    async def test_identifies_receipt_url(self, db_path):
        """LLM should identify receipt URLs from message text."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "receipt_urls": ["https://store.example.com/receipt/12345"]
        })
        mock_response.usage = MagicMock(prompt_tokens=50, completion_tokens=20)

        with patch("backend.ingestion.url_triage.litellm_completion", return_value=mock_response):
            urls = await triage_telegram_urls(
                message_text="Here's my receipt from today https://store.example.com/receipt/12345 and check out https://example.com/news",
                urls=["https://store.example.com/receipt/12345", "https://example.com/news"],
            )

        assert urls == ["https://store.example.com/receipt/12345"]

    @pytest.mark.asyncio
    async def test_returns_all_urls_on_llm_failure(self, db_path):
        """On LLM error, fall back to returning all URLs."""
        with patch("backend.ingestion.url_triage.litellm_completion", side_effect=Exception("API error")):
            urls = await triage_telegram_urls(
                message_text="Receipt: https://a.com/r.pdf",
                urls=["https://a.com/r.pdf"],
            )

        assert urls == ["https://a.com/r.pdf"]


class TestTriageEmail:
    """Test email triage."""

    @pytest.mark.asyncio
    async def test_recommends_url_over_logo_attachment(self, db_path):
        """Should recommend URL when attachment is a small logo image."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "ingest_attachments": [],
            "ingest_urls": ["https://billing.example.com/invoice/789"],
        })
        mock_response.usage = MagicMock(prompt_tokens=80, completion_tokens=30)

        with patch("backend.ingestion.url_triage.litellm_completion", return_value=mock_response):
            result = await triage_email(
                body_text="Thank you for your purchase. View your invoice: https://billing.example.com/invoice/789",
                attachments=[{"filename": "logo.png", "content_type": "image/png", "size": 2048}],
                urls=["https://billing.example.com/invoice/789"],
            )

        assert isinstance(result, TriageResult)
        assert result.ingest_urls == ["https://billing.example.com/invoice/789"]
        assert result.ingest_attachments == []

    @pytest.mark.asyncio
    async def test_recommends_attachment_when_its_the_invoice(self, db_path):
        """Should recommend attachment when it's clearly the invoice PDF."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "ingest_attachments": ["Invoice_2026_04.pdf"],
            "ingest_urls": [],
        })
        mock_response.usage = MagicMock(prompt_tokens=80, completion_tokens=30)

        with patch("backend.ingestion.url_triage.litellm_completion", return_value=mock_response):
            result = await triage_email(
                body_text="Please find your invoice attached. Manage account: https://example.com/account",
                attachments=[{"filename": "Invoice_2026_04.pdf", "content_type": "application/pdf", "size": 245000}],
                urls=["https://example.com/account"],
            )

        assert result.ingest_attachments == ["Invoice_2026_04.pdf"]
        assert result.ingest_urls == []

    @pytest.mark.asyncio
    async def test_fallback_on_llm_failure(self, db_path):
        """On LLM error, fall back to ingesting all attachments."""
        with patch("backend.ingestion.url_triage.litellm_completion", side_effect=Exception("API error")):
            result = await triage_email(
                body_text="Your receipt",
                attachments=[{"filename": "doc.pdf", "content_type": "application/pdf", "size": 50000}],
                urls=["https://example.com/receipt"],
            )

        assert result.ingest_attachments == ["doc.pdf"]
        assert result.ingest_urls == ["https://example.com/receipt"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_url_triage.py -v`
Expected: FAIL — module does not exist

- [ ] **Step 3: Implement the URL triage module**

Create `backend/ingestion/url_triage.py`:

```python
"""LLM-based triage for deciding which URLs/attachments to ingest."""
import json
import logging
from dataclasses import dataclass, field

from backend.config import get_setting
from backend.processing.extract import litellm_completion

logger = logging.getLogger(__name__)


@dataclass
class TriageResult:
    ingest_attachments: list[str] = field(default_factory=list)  # Filenames to ingest
    ingest_urls: list[str] = field(default_factory=list)         # URLs to fetch and ingest


TELEGRAM_TRIAGE_PROMPT = """You are analyzing a Telegram message to identify URLs that link to receipt, invoice, or financial document files.

Message text:
{message_text}

URLs found in the message:
{urls_list}

Which of these URLs are likely links to receipts, invoices, flight tickets, or other financial/business documents?
Exclude tracking links, marketing pages, social media links, news articles, and other non-document URLs.

Return a JSON object with a single key:
- "receipt_urls": array of URLs from the list that are likely document links

Return ONLY the JSON object, no explanation."""


EMAIL_TRIAGE_PROMPT = """You are analyzing an email to decide what content to ingest as receipts/invoices.

Email body:
{body_text}

Attachments:
{attachments_list}

URLs found in the email body:
{urls_list}

Decide what to ingest. Consider:
- PDF attachments named like invoices/receipts are usually the actual documents
- Small image attachments (under 10KB) are usually logos or tracking pixels, not documents
- Large image attachments may be scanned documents
- URLs containing words like "invoice", "receipt", "download", "pdf" may link to the actual document
- URLs to account management, unsubscribe, or marketing pages should be excluded
- Some emails attach unrelated files (terms, licenses) while linking to the actual receipt

Return a JSON object with:
- "ingest_attachments": array of filenames to ingest (from the attachments list)
- "ingest_urls": array of URLs to fetch and ingest (from the URLs list)

Return ONLY the JSON object, no explanation."""


async def triage_telegram_urls(message_text: str, urls: list[str]) -> list[str]:
    """Use LLM to identify which URLs in a Telegram message are receipt/invoice links.

    Returns list of URLs to fetch. Falls back to all URLs on LLM failure.
    """
    model = get_setting("llm_model")
    api_key = get_setting("llm_api_key")
    if not model or not api_key:
        logger.warning("LLM not configured — returning all URLs for fetching")
        return urls

    prompt = TELEGRAM_TRIAGE_PROMPT.format(
        message_text=message_text,
        urls_list="\n".join(f"- {u}" for u in urls),
    )

    try:
        response = litellm_completion(
            model=model,
            api_key=api_key,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=512,
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            import re
            match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", raw, re.DOTALL)
            if match:
                raw = match.group(1).strip()
        data = json.loads(raw)
        result_urls = data.get("receipt_urls", [])
        # Filter to only URLs that were in the original list
        return [u for u in result_urls if u in urls]

    except Exception as e:
        logger.error("Telegram URL triage failed: %s — falling back to all URLs", e)
        return urls


async def triage_email(body_text: str, attachments: list[dict], urls: list[str]) -> TriageResult:
    """Use LLM to decide which email attachments and URLs to ingest.

    Returns TriageResult. Falls back to all attachments + all URLs on LLM failure.
    """
    model = get_setting("llm_model")
    api_key = get_setting("llm_api_key")
    all_filenames = [a["filename"] for a in attachments]

    if not model or not api_key:
        logger.warning("LLM not configured — returning all attachments and URLs")
        return TriageResult(ingest_attachments=all_filenames, ingest_urls=urls)

    attachments_desc = "\n".join(
        f"- {a['filename']} ({a['content_type']}, {a['size']} bytes)"
        for a in attachments
    )
    prompt = EMAIL_TRIAGE_PROMPT.format(
        body_text=body_text[:3000],  # Truncate long bodies
        attachments_list=attachments_desc or "(none)",
        urls_list="\n".join(f"- {u}" for u in urls) or "(none)",
    )

    try:
        response = litellm_completion(
            model=model,
            api_key=api_key,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=512,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            import re
            match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", raw, re.DOTALL)
            if match:
                raw = match.group(1).strip()
        data = json.loads(raw)

        # Filter to only items from the original lists
        ingest_att = [f for f in data.get("ingest_attachments", []) if f in all_filenames]
        ingest_urls = [u for u in data.get("ingest_urls", []) if u in urls]
        return TriageResult(ingest_attachments=ingest_att, ingest_urls=ingest_urls)

    except Exception as e:
        logger.error("Email triage failed: %s — falling back to all content", e)
        return TriageResult(ingest_attachments=all_filenames, ingest_urls=urls)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_url_triage.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add backend/ingestion/url_triage.py tests/test_url_triage.py
git commit -m "feat: add LLM-based URL triage for Telegram and email"
```

---

### Task 5: Telegram Pipeline — Add Text Message Handler

**Files:**
- Modify: `backend/ingestion/telegram.py`
- Create: `tests/test_telegram_urls.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_telegram_urls.py`:

```python
"""Tests for Telegram text message URL handling."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from backend.ingestion.telegram import handle_text


def _make_update(text: str, user_id: int = 123, username: str = "testuser"):
    """Create a mock Telegram Update with a text message."""
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = username
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


def _make_context(data_dir: str):
    """Create a mock Telegram context."""
    context = MagicMock()
    context.bot_data = {"data_dir": data_dir}
    return context


class TestHandleText:

    @pytest.mark.asyncio
    async def test_ignores_message_without_urls(self, db_path, tmp_data_dir):
        """Text without URLs should get a 'no URL found' reply."""
        update = _make_update("just a random message with no links")
        context = _make_context(str(tmp_data_dir))

        with patch("backend.ingestion.telegram._is_authorized", return_value=True):
            await handle_text(update, context)

        update.message.reply_text.assert_called_once()
        assert "no" in update.message.reply_text.call_args[0][0].lower() or \
               "No" in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_unauthorized_user_rejected(self, db_path, tmp_data_dir):
        """Unauthorized users should be rejected."""
        update = _make_update("https://example.com/receipt.pdf")
        context = _make_context(str(tmp_data_dir))

        with patch("backend.ingestion.telegram._is_authorized", return_value=False):
            await handle_text(update, context)

        assert "not authorized" in update.message.reply_text.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_fetches_triaged_url_and_creates_document(self, db_path, tmp_data_dir):
        """Valid URL should be triaged, fetched, and create a document record."""
        import os
        update = _make_update("My receipt: https://store.example.com/receipt/123")
        context = _make_context(str(tmp_data_dir))

        # Create a fake PDF file for the fetcher to "download"
        fake_pdf = tmp_data_dir / "fetched.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4 fake content for hashing uniqueness test1")

        from backend.ingestion.url_fetcher import FetchResult
        mock_fetch_result = FetchResult(
            file_path=str(fake_pdf),
            content_type="application/pdf",
            original_url="https://store.example.com/receipt/123",
            method="direct",
        )

        with patch("backend.ingestion.telegram._is_authorized", return_value=True), \
             patch("backend.ingestion.telegram.triage_telegram_urls", new_callable=AsyncMock,
                   return_value=["https://store.example.com/receipt/123"]), \
             patch("backend.ingestion.telegram.fetch_url", new_callable=AsyncMock,
                   return_value=mock_fetch_result):
            await handle_text(update, context)

        # Should have replied with a document ID
        reply_text = update.message.reply_text.call_args[0][0]
        assert "document" in reply_text.lower() or "#" in reply_text

    @pytest.mark.asyncio
    async def test_auth_wall_creates_needs_review(self, db_path, tmp_data_dir):
        """Auth-walled URLs should create document with needs_review status."""
        update = _make_update("Invoice: https://portal.example.com/invoice/456")
        context = _make_context(str(tmp_data_dir))

        fake_pdf = tmp_data_dir / "auth.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4 auth wall capture unique content 789")

        from backend.ingestion.url_fetcher import FetchResult
        mock_fetch_result = FetchResult(
            file_path=str(fake_pdf),
            content_type="application/pdf",
            original_url="https://portal.example.com/invoice/456",
            auth_wall=True,
            method="playwright_capture",
        )

        with patch("backend.ingestion.telegram._is_authorized", return_value=True), \
             patch("backend.ingestion.telegram.triage_telegram_urls", new_callable=AsyncMock,
                   return_value=["https://portal.example.com/invoice/456"]), \
             patch("backend.ingestion.telegram.fetch_url", new_callable=AsyncMock,
                   return_value=mock_fetch_result):
            await handle_text(update, context)

        from backend.database import get_connection
        with get_connection() as conn:
            doc = conn.execute(
                "SELECT status, source_url FROM documents ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert doc["status"] == "needs_review"
        assert doc["source_url"] == "https://portal.example.com/invoice/456"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_telegram_urls.py -v`
Expected: FAIL — `handle_text` not defined

- [ ] **Step 3: Implement `handle_text` in telegram.py**

Add these imports at the top of `backend/ingestion/telegram.py` (after existing imports):

```python
import re as _re
from backend.ingestion.url_triage import triage_telegram_urls
from backend.ingestion.url_fetcher import fetch_url
```

Add the `handle_text` function after `handle_photo` (after line 59):

```python
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text messages — extract URLs, triage, fetch, and ingest."""
    user = update.effective_user
    if not _is_authorized(user.id):
        await update.message.reply_text("You are not authorized to use this bot.")
        logger.warning(f"Unauthorized text from user {user.id} ({user.username})")
        return

    text = update.message.text or ""
    urls = _re.findall(r"https?://[^\s<>\"']+", text)
    if not urls:
        await update.message.reply_text("No document or URL found in your message.")
        return

    data_dir = context.bot_data.get("data_dir", "data")
    sender = f"telegram:{user.id}"
    if user.username:
        sender = f"telegram:@{user.username}"

    # LLM triage: which URLs are receipt/invoice links?
    receipt_urls = await triage_telegram_urls(text, urls)
    if not receipt_urls:
        await update.message.reply_text("No receipt/invoice URLs identified in your message.")
        return

    timeout = get_setting("url_fetch_timeout")
    ingested = []
    for url in receipt_urls:
        try:
            result = await fetch_url(url, os.path.join(data_dir, "storage", "tmp"), timeout)
            if result is None:
                await update.message.reply_text(f"Could not fetch: {url}")
                continue

            file_hash = compute_file_hash(result.file_path)
            file_size = os.path.getsize(result.file_path)
            ext = os.path.splitext(result.file_path)[1].lower() or ".pdf"

            # Check duplicate
            with get_connection() as conn:
                existing = conn.execute(
                    "SELECT id FROM documents WHERE file_hash = ?", (file_hash,)
                ).fetchone()

            if existing:
                await update.message.reply_text(
                    f"Duplicate — URL content matches document #{existing['id']}."
                )
                continue

            save_original(result.file_path, file_hash, ext, data_dir)

            status = "needs_review" if result.auth_wall else "pending"
            filename = os.path.basename(result.file_path)

            with get_connection() as conn:
                conn.execute(
                    """INSERT INTO documents
                       (original_filename, file_hash, file_size_bytes, status,
                        submission_channel, sender_identifier, source_url, user_notes)
                       VALUES (?, ?, ?, ?, 'telegram', ?, ?, ?)""",
                    (filename, file_hash, file_size, status, sender, url,
                     "Auth-gated URL — page may require login" if result.auth_wall else None),
                )
                doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            ingested.append(doc_id)
            try:
                from backend.notifications.notifier import notify
                notify("ingested", {
                    "id": doc_id,
                    "original_filename": filename,
                    "file_hash": file_hash,
                    "submission_channel": "telegram",
                    "sender_identifier": sender,
                    "source_url": url,
                })
            except Exception:
                pass

        except Exception as e:
            logger.error(f"Telegram URL ingestion failed for {url}: {e}")
            await update.message.reply_text(f"Failed to process URL: {e}")

    if ingested:
        ids = ", ".join(f"#{d}" for d in ingested)
        await update.message.reply_text(f"Received! Document(s) {ids} queued for processing.")
```

Register the new handler in `start_telegram_bot` (after line 155, before `await _app.initialize()`):

```python
    _app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_telegram_urls.py -v`
Expected: All PASS

- [ ] **Step 5: Run existing Telegram tests to check for regressions**

Run: `uv run pytest tests/ -v -k "telegram" --ignore=tests/test_e2e.py`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add backend/ingestion/telegram.py tests/test_telegram_urls.py
git commit -m "feat: handle text messages with URLs in Telegram bot"
```

---

### Task 6: Email Pipeline — Triage-Based Flow

**Files:**
- Modify: `backend/ingestion/gmail.py:105-161`
- Create: `tests/test_gmail_urls.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_gmail_urls.py`:

```python
"""Tests for email URL-based ingestion."""
import email
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

from backend.ingestion.gmail import _process_message
from backend.database import get_connection


def _build_email(subject, body_html, attachments=None, sender="vendor@example.com"):
    """Build a raw email for testing."""
    msg = MIMEMultipart()
    msg["From"] = f"Vendor <{sender}>"
    msg["Subject"] = subject
    msg.attach(MIMEText(body_html, "html"))
    for att in (attachments or []):
        part = MIMEApplication(att["content"], Name=att["filename"])
        part["Content-Disposition"] = f'attachment; filename="{att["filename"]}"'
        if "content_type" in att:
            part.set_type(att["content_type"])
        msg.attach(part)
    return msg.as_bytes()


def _mock_imap(raw_email_bytes):
    """Create a mock IMAP connection that returns the given email."""
    mail = MagicMock()
    mail.fetch.return_value = ("OK", [(b"1", raw_email_bytes)])
    mail.store.return_value = ("OK", [])
    return mail


class TestEmailWithUrlsAndAttachments:

    def test_attachment_plus_url_triggers_triage(self, db_path, tmp_data_dir, monkeypatch):
        """Email with attachment AND URL should call LLM triage."""
        import json
        raw = _build_email(
            subject="Your Invoice",
            body_html='<html><body>View invoice: <a href="https://billing.example.com/inv/99">here</a></body></html>',
            attachments=[{"filename": "logo.png", "content": b"\x89PNG" + b"\x00" * 100, "content_type": "image/png"}],
        )
        mail = _mock_imap(raw)

        triage_called = False

        async def mock_triage(body_text, attachments, urls):
            nonlocal triage_called
            triage_called = True
            from backend.ingestion.url_triage import TriageResult
            return TriageResult(ingest_attachments=[], ingest_urls=["https://billing.example.com/inv/99"])

        fake_pdf = tmp_data_dir / "fetched_inv.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4 invoice content triage test unique")

        from backend.ingestion.url_fetcher import FetchResult
        mock_fetch = FetchResult(
            file_path=str(fake_pdf),
            content_type="application/pdf",
            original_url="https://billing.example.com/inv/99",
            method="direct",
        )

        import asyncio
        with patch("backend.ingestion.gmail.triage_email", side_effect=mock_triage), \
             patch("backend.ingestion.gmail.fetch_url", new_callable=AsyncMock, return_value=mock_fetch), \
             patch("backend.ingestion.gmail._is_sender_authorized", return_value=True):
            result = _process_message(mail, b"1", str(tmp_data_dir))

        assert triage_called

    def test_attachment_only_no_triage(self, db_path, tmp_data_dir):
        """Email with only attachments (no URLs) should not call triage."""
        raw = _build_email(
            subject="Invoice Attached",
            body_html="<html><body>Please see attached invoice.</body></html>",
            attachments=[{"filename": "invoice.pdf", "content": b"%PDF-1.4 real invoice content unique xyz"}],
        )
        mail = _mock_imap(raw)

        with patch("backend.ingestion.gmail.triage_email") as mock_triage, \
             patch("backend.ingestion.gmail._is_sender_authorized", return_value=True):
            result = _process_message(mail, b"1", str(tmp_data_dir))

        mock_triage.assert_not_called()

    def test_no_attachment_no_url_creates_needs_review(self, db_path, tmp_data_dir):
        """Email with neither attachments nor URLs should create needs_review record."""
        raw = _build_email(
            subject="Your Monthly Statement",
            body_html="<html><body>Your statement is ready.</body></html>",
        )
        mail = _mock_imap(raw)

        with patch("backend.ingestion.gmail._is_sender_authorized", return_value=True):
            result = _process_message(mail, b"1", str(tmp_data_dir))

        with get_connection() as conn:
            doc = conn.execute(
                "SELECT status FROM documents ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert doc is not None
        assert doc["status"] == "needs_review"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gmail_urls.py -v`
Expected: FAIL — triage_email not imported in gmail.py, _process_message logic doesn't match

- [ ] **Step 3: Rewrite `_process_message` in gmail.py**

Add imports at the top of `backend/ingestion/gmail.py` (after existing imports):

```python
import asyncio as _asyncio
from bs4 import BeautifulSoup as _BS
from backend.ingestion.url_triage import triage_email, triage_telegram_urls
from backend.ingestion.url_fetcher import fetch_url
```

Replace the `_process_message` function (lines 105-161) with:

```python
def _extract_urls_from_html(html: str) -> list[str]:
    """Extract HTTP(S) URLs from HTML email body."""
    soup = _BS(html, "html.parser")
    urls = set()
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if href.startswith("http://") or href.startswith("https://"):
            # Skip common non-document URLs
            skip_patterns = ["unsubscribe", "mailto:", "tel:", "manage-preferences", "list-unsubscribe"]
            if not any(p in href.lower() for p in skip_patterns):
                urls.add(href)
    return list(urls)


def _collect_attachments(parsed) -> list[dict]:
    """Walk email MIME parts and collect attachment metadata + content."""
    attachments = []
    for part in parsed.walk():
        content_type = part.get_content_type()
        content_disposition = str(part.get("Content-Disposition", ""))

        if "attachment" in content_disposition or content_type in (
            "application/pdf", "image/jpeg", "image/png", "image/tiff"
        ):
            filename = part.get_filename() or "attachment"
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            attachments.append({
                "filename": filename,
                "content_type": content_type,
                "size": len(payload),
                "content": payload,
            })
    return attachments


def _process_message(mail: imaplib.IMAP4_SSL, msg_id: bytes, data_dir: str) -> dict:
    """Fetch and process a single email message with URL triage."""
    status, msg_data = mail.fetch(msg_id, "(RFC822)")
    if status != "OK" or not msg_data or not msg_data[0]:
        return {"msg_id": msg_id.decode(), "status": "error", "error": "Failed to fetch message"}

    raw_bytes = msg_data[0][1]
    parsed = email.message_from_bytes(raw_bytes, policy=email_policy.default)

    from_header = str(parsed.get("From", ""))
    subject = str(parsed.get("Subject", "(no subject)"))
    sender_email = _extract_sender_email(from_header)
    authorized = _is_sender_authorized(sender_email)

    ingested = []

    # Collect attachments and URLs
    attachments = _collect_attachments(parsed)

    # Extract HTML body and URLs
    html_body = ""
    text_body = ""
    for part in parsed.walk():
        ct = part.get_content_type()
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        if ct == "text/html" and not html_body:
            html_body = payload.decode("utf-8", errors="replace")
        elif ct == "text/plain" and not text_body:
            text_body = payload.decode("utf-8", errors="replace")

    urls = _extract_urls_from_html(html_body) if html_body else []
    body_text = text_body or html_body

    # Decision tree
    has_attachments = len(attachments) > 0
    has_urls = len(urls) > 0

    ingest_attachments = []
    ingest_urls = []

    if has_attachments and has_urls:
        # LLM triage decides what to ingest
        att_meta = [{"filename": a["filename"], "content_type": a["content_type"], "size": a["size"]}
                    for a in attachments]
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    triage_result = pool.submit(
                        _asyncio.run, triage_email(body_text, att_meta, urls)
                    ).result()
            else:
                triage_result = loop.run_until_complete(triage_email(body_text, att_meta, urls))
        except RuntimeError:
            triage_result = _asyncio.run(triage_email(body_text, att_meta, urls))
        ingest_attachments = [a for a in attachments if a["filename"] in triage_result.ingest_attachments]
        ingest_urls = triage_result.ingest_urls

    elif has_attachments and not has_urls:
        # No URLs — ingest all attachments as before
        ingest_attachments = attachments

    elif not has_attachments and has_urls:
        # No attachments — triage URLs
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    ingest_urls = pool.submit(
                        _asyncio.run, triage_telegram_urls(body_text, urls)
                    ).result()
            else:
                ingest_urls = loop.run_until_complete(triage_telegram_urls(body_text, urls))
        except RuntimeError:
            ingest_urls = _asyncio.run(triage_telegram_urls(body_text, urls))

    else:
        # No attachments, no URLs — flag for review
        with get_connection() as conn:
            conn.execute(
                """INSERT INTO documents
                   (original_filename, file_hash, file_size_bytes, status,
                    submission_channel, sender_identifier, user_notes)
                   VALUES (?, ?, 0, 'needs_review', 'email', ?, ?)""",
                (f"email_{msg_id.decode()}.txt",
                 f"email_no_content_{msg_id.decode()}",
                 f"email:{sender_email}",
                 f"Email with subject '{subject}' had no attachments or URLs"),
            )
        mail.store(msg_id, "+FLAGS", "\\Seen")
        return {
            "msg_id": msg_id.decode(), "subject": subject, "sender": sender_email,
            "authorized": authorized, "ingested": [{"status": "needs_review", "reason": "no content"}],
        }

    # Ingest selected attachments
    for att in ingest_attachments:
        result = _ingest_attachment(att["content"], att["filename"], sender_email, data_dir, authorized)
        ingested.append(result)

    # Fetch and ingest selected URLs
    timeout = get_setting("url_fetch_timeout")
    for url in ingest_urls:
        try:
            try:
                loop = _asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        fetch_result = pool.submit(
                            _asyncio.run, fetch_url(url, os.path.join(data_dir, "storage", "tmp"), timeout)
                        ).result()
                else:
                    fetch_result = loop.run_until_complete(
                        fetch_url(url, os.path.join(data_dir, "storage", "tmp"), timeout)
                    )
            except RuntimeError:
                fetch_result = _asyncio.run(
                    fetch_url(url, os.path.join(data_dir, "storage", "tmp"), timeout)
                )

            if fetch_result is None:
                logger.warning(f"Email: could not fetch URL {url}")
                continue

            file_hash = compute_file_hash(fetch_result.file_path)
            file_size = os.path.getsize(fetch_result.file_path)
            ext = os.path.splitext(fetch_result.file_path)[1].lower() or ".pdf"

            with get_connection() as conn:
                existing = conn.execute(
                    "SELECT id FROM documents WHERE file_hash = ?", (file_hash,)
                ).fetchone()

            if existing:
                ingested.append({"url": url, "status": "duplicate", "existing_id": existing["id"]})
                continue

            save_original(fetch_result.file_path, file_hash, ext, data_dir)

            status_val = "needs_review" if (fetch_result.auth_wall or not authorized) else "pending"
            notes = None
            if fetch_result.auth_wall:
                notes = "Auth-gated URL — page may require login"

            category_id = None
            if not authorized:
                with get_connection() as conn:
                    cat = conn.execute(
                        "SELECT id FROM categories WHERE name = 'unauthorized_sender' AND is_system = 1"
                    ).fetchone()
                    if cat:
                        category_id = cat["id"]

            filename = os.path.basename(fetch_result.file_path)
            with get_connection() as conn:
                conn.execute(
                    """INSERT INTO documents
                       (original_filename, file_hash, file_size_bytes, status,
                        submission_channel, sender_identifier, source_url, category_id, user_notes)
                       VALUES (?, ?, ?, ?, 'email', ?, ?, ?, ?)""",
                    (filename, file_hash, file_size, status_val,
                     f"email:{sender_email}", url, category_id, notes),
                )
                doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            ingested.append({"url": url, "status": "ingested", "doc_id": doc_id})
            try:
                from backend.notifications.notifier import notify
                notify("ingested", {
                    "id": doc_id, "original_filename": filename, "file_hash": file_hash,
                    "submission_channel": "email", "sender_identifier": f"email:{sender_email}",
                    "source_url": url,
                })
            except Exception:
                pass

        except Exception as e:
            logger.error(f"Email: failed to fetch URL {url}: {e}")
            ingested.append({"url": url, "status": "error", "error": str(e)})

    mail.store(msg_id, "+FLAGS", "\\Seen")
    return {
        "msg_id": msg_id.decode(), "subject": subject, "sender": sender_email,
        "authorized": authorized, "ingested": ingested,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gmail_urls.py -v`
Expected: All PASS

- [ ] **Step 5: Run existing email tests to check for regressions**

Run: `uv run pytest tests/ -v -k "gmail or email" --ignore=tests/test_e2e.py`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add backend/ingestion/gmail.py tests/test_gmail_urls.py
git commit -m "feat: add URL triage and fetching to email ingestion pipeline"
```

---

### Task 7: Dependencies and Docker

**Files:**
- Modify: `pyproject.toml`
- Modify: `Dockerfile`

- [ ] **Step 1: Add Python dependencies**

In `pyproject.toml`, add to the `dependencies` list:

```toml
    "beautifulsoup4>=4.12.0",
    "playwright>=1.40.0",
```

- [ ] **Step 2: Add Playwright to Dockerfile**

In `Dockerfile`, after the `RUN uv sync --no-dev --frozen` line, add:

```dockerfile
# Install Playwright and Chromium for URL fetching (JS-rendered receipt pages)
RUN uv run playwright install chromium --with-deps
```

- [ ] **Step 3: Update lock file**

Run: `uv lock`
Expected: Lock file updated without errors

- [ ] **Step 4: Verify dependencies install**

Run: `uv sync --all-extras`
Expected: All dependencies installed

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock Dockerfile
git commit -m "feat: add beautifulsoup4 and playwright dependencies"
```

---

### Task 8: Integration Test

**Files:**
- Create: `tests/test_url_integration.py`

- [ ] **Step 1: Write integration test**

Create `tests/test_url_integration.py`:

```python
"""Integration test: full URL ingestion flow from fetch to document creation."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path

from backend.database import get_connection


class TestFullUrlFlow:

    @pytest.mark.asyncio
    async def test_telegram_text_to_processed_document(self, db_path, tmp_data_dir):
        """Full flow: Telegram text message → triage → fetch → document in DB."""
        from backend.ingestion.telegram import handle_text
        from backend.ingestion.url_fetcher import FetchResult

        # Setup: create a fake downloaded PDF
        fake_pdf = tmp_data_dir / "receipt.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4 integration test receipt content unique abc")

        update = MagicMock()
        update.effective_user.id = 100
        update.effective_user.username = "integrationuser"
        update.message.text = "Check out my receipt https://shop.example.com/r/555"
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.bot_data = {"data_dir": str(tmp_data_dir)}

        with patch("backend.ingestion.telegram._is_authorized", return_value=True), \
             patch("backend.ingestion.telegram.triage_telegram_urls", new_callable=AsyncMock,
                   return_value=["https://shop.example.com/r/555"]), \
             patch("backend.ingestion.telegram.fetch_url", new_callable=AsyncMock,
                   return_value=FetchResult(
                       file_path=str(fake_pdf),
                       content_type="application/pdf",
                       original_url="https://shop.example.com/r/555",
                       method="direct",
                   )):
            await handle_text(update, context)

        # Verify document was created
        with get_connection() as conn:
            doc = conn.execute("SELECT * FROM documents ORDER BY id DESC LIMIT 1").fetchone()

        assert doc is not None
        assert doc["status"] == "pending"
        assert doc["submission_channel"] == "telegram"
        assert doc["source_url"] == "https://shop.example.com/r/555"
        assert doc["sender_identifier"] == "telegram:@integrationuser"

    @pytest.mark.asyncio
    async def test_migration_adds_source_url_column(self, db_path):
        """Verify the source_url column exists after migration."""
        with get_connection() as conn:
            # This should not raise — column exists
            conn.execute("SELECT source_url FROM documents LIMIT 1")
```

- [ ] **Step 2: Run integration tests**

Run: `uv run pytest tests/test_url_integration.py -v`
Expected: All PASS

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -v --ignore=tests/test_e2e.py`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_url_integration.py
git commit -m "test: add integration tests for URL ingestion flow"
```

---

### Task 9: Final Verification and Cleanup

- [ ] **Step 1: Run the full test suite one final time**

Run: `uv run pytest tests/ -v --ignore=tests/test_e2e.py`
Expected: All PASS, no warnings about missing modules

- [ ] **Step 2: Verify Docker build works**

Run: `docker compose build --no-cache`
Expected: Build succeeds, Playwright installs Chromium

- [ ] **Step 3: Final commit with any cleanup**

If any files need cleanup (unused imports, formatting), fix and commit:

```bash
git add -A
git commit -m "chore: URL ingestion cleanup and final polish"
```
