"""Tests for backend.ingestion.url_fetcher module."""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import httpx

from backend.ingestion.url_fetcher import (
    fetch_url,
    _playwright_fetch,
    FetchResult,
    _find_document_links,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(
    status_code: int = 200,
    content: bytes = b"data",
    content_type: str = "application/pdf",
    headers: dict | None = None,
    text: str = "",
) -> httpx.Response:
    """Build a fake httpx.Response."""
    h = {"content-type": content_type}
    if headers:
        h.update(headers)
    resp = httpx.Response(
        status_code=status_code,
        content=content,
        headers=h,
        request=httpx.Request("GET", "https://example.com"),
    )
    return resp


# ---------------------------------------------------------------------------
# Unit tests for _find_document_links
# ---------------------------------------------------------------------------


class TestFindDocumentLinks:
    def test_finds_pdf_link(self):
        html = '<html><body><a href="/files/invoice.pdf">Get PDF</a></body></html>'
        links = _find_document_links(html, "https://example.com")
        assert len(links) == 1
        assert links[0] == "https://example.com/files/invoice.pdf"

    def test_finds_image_link(self):
        html = '<html><body><a href="scan.jpg">Image</a></body></html>'
        links = _find_document_links(html, "https://example.com/page/")
        assert "https://example.com/page/scan.jpg" in links

    def test_finds_download_keyword_link(self):
        html = '<html><body><a href="/get?id=1">Download Receipt</a></body></html>'
        links = _find_document_links(html, "https://example.com")
        assert len(links) == 1

    def test_ignores_unrelated_links(self):
        html = '<html><body><a href="/about">About</a><a href="/contact">Contact</a></body></html>'
        links = _find_document_links(html, "https://example.com")
        assert len(links) == 0


# ---------------------------------------------------------------------------
# Integration tests for fetch_url
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFetchUrlDirect:
    """Direct PDF/image downloads."""

    async def test_direct_pdf_download(self, tmp_path):
        pdf_content = b"%PDF-1.4 fake pdf content"
        resp = _make_response(content=pdf_content, content_type="application/pdf")

        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get.return_value = resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await fetch_url("https://example.com/receipt.pdf", tmp_path)

        assert result is not None
        assert result.method == "direct"
        assert result.content_type == "application/pdf"
        assert result.original_url == "https://example.com/receipt.pdf"
        assert result.auth_wall is False
        assert Path(result.file_path).read_bytes() == pdf_content

    async def test_direct_image_download(self, tmp_path):
        img_content = b"\x89PNG fake image"
        resp = _make_response(content=img_content, content_type="image/png")

        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get.return_value = resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await fetch_url("https://example.com/scan.png", tmp_path)

        assert result is not None
        assert result.method == "direct"
        assert result.content_type == "image/png"
        assert Path(result.file_path).suffix == ".png"
        assert Path(result.file_path).read_bytes() == img_content

    async def test_direct_jpeg_download(self, tmp_path):
        resp = _make_response(content=b"\xff\xd8", content_type="image/jpeg")

        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get.return_value = resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await fetch_url("https://example.com/photo.jpg", tmp_path)

        assert result is not None
        assert result.content_type == "image/jpeg"
        assert Path(result.file_path).suffix == ".jpg"


@pytest.mark.asyncio
class TestFetchUrlHtmlLinkFollow:
    """HTML pages containing document links."""

    async def test_html_with_pdf_link(self, tmp_path):
        html_body = '<html><body><a href="/files/invoice.pdf">Download</a></body></html>'
        html_resp = _make_response(
            content=html_body.encode(),
            content_type="text/html; charset=utf-8",
            text=html_body,
        )
        # The text property needs to work
        pdf_resp = _make_response(
            content=b"%PDF-1.4 data", content_type="application/pdf"
        )

        call_count = 0

        async def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return html_resp
            return pdf_resp

        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await fetch_url("https://example.com/page", tmp_path)

        assert result is not None
        assert result.method == "link_follow"
        assert result.content_type == "application/pdf"

    async def test_html_without_links_falls_to_playwright(self, tmp_path):
        html_body = "<html><body><p>No links here</p></body></html>"
        html_resp = _make_response(
            content=html_body.encode(),
            content_type="text/html",
        )

        pw_result = FetchResult(
            file_path=str(tmp_path / "capture.pdf"),
            content_type="application/pdf",
            original_url="https://example.com/page",
            method="playwright_capture",
        )
        # Create the file so assertions can check it
        Path(pw_result.file_path).write_bytes(b"%PDF")

        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get.return_value = html_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with patch(
                "backend.ingestion.url_fetcher._playwright_fetch",
                return_value=pw_result,
            ) as pw_mock:
                result = await fetch_url("https://example.com/page", tmp_path)

        assert result is not None
        assert result.method == "playwright_capture"
        pw_mock.assert_awaited_once()


@pytest.mark.asyncio
class TestFetchUrlPlaywright:
    """Playwright-based fetch (mocked)."""

    async def test_auth_wall_detection(self, tmp_path):
        pw_result = FetchResult(
            file_path=str(tmp_path / "auth.pdf"),
            content_type="application/pdf",
            original_url="https://example.com/login",
            auth_wall=True,
            method="playwright_capture",
        )
        Path(pw_result.file_path).write_bytes(b"%PDF")

        html_body = "<html><body>Login required</body></html>"
        html_resp = _make_response(
            content=html_body.encode(), content_type="text/html"
        )

        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get.return_value = html_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            with patch(
                "backend.ingestion.url_fetcher._playwright_fetch",
                return_value=pw_result,
            ):
                result = await fetch_url("https://example.com/login", tmp_path)

        assert result is not None
        assert result.auth_wall is True
        assert result.method == "playwright_capture"


@pytest.mark.asyncio
class TestFetchUrlErrors:
    """Error handling — all errors return None."""

    async def test_timeout_returns_none(self, tmp_path):
        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.ReadTimeout("timed out")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await fetch_url("https://example.com/slow", tmp_path)

        assert result is None

    async def test_http_error_returns_none(self, tmp_path):
        resp = _make_response(status_code=404, content_type="text/html")

        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get.return_value = resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await fetch_url("https://example.com/missing", tmp_path)

        assert result is None

    async def test_http_500_returns_none(self, tmp_path):
        resp = _make_response(status_code=500, content_type="text/html")

        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get.return_value = resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await fetch_url("https://example.com/error", tmp_path)

        assert result is None

    async def test_connect_error_returns_none(self, tmp_path):
        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.ConnectError("connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await fetch_url("https://example.com/down", tmp_path)

        assert result is None


@pytest.mark.asyncio
class TestPlaywrightFetchImportError:
    """_playwright_fetch handles missing playwright gracefully."""

    async def test_import_error_returns_none(self, tmp_path):
        with patch.dict("sys.modules", {"playwright": None, "playwright.async_api": None}):
            # Force re-import to trigger ImportError
            result = await _playwright_fetch(
                "https://example.com", str(tmp_path), timeout=5
            )
        assert result is None
