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


def _make_playwright_mock(page):
    """Build an async_playwright() mock whose page is `page`."""
    browser = MagicMock()
    browser.new_page = AsyncMock(return_value=page)
    browser.close = AsyncMock()

    pw = MagicMock()
    pw.chromium = MagicMock()
    pw.chromium.launch = AsyncMock(return_value=browser)

    pw_cm = MagicMock()
    pw_cm.__aenter__ = AsyncMock(return_value=pw)
    pw_cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=pw_cm)


def _fake_pdf_response(body: bytes = b"%PDF-1.4 doc", url: str = "https://cdn.example.com/doc.pdf"):
    """A mock Playwright Response for a streamed application/pdf."""
    resp = MagicMock()
    resp.url = url
    resp.headers = {"content-type": "application/pdf"}
    resp.body = AsyncMock(return_value=body)
    return resp


def _streaming_page(fake_resp=None):
    """A mock Playwright Page that fires `fake_resp` to its response listener
    during goto (simulating a canvas viewer streaming its PDF)."""
    handlers: dict = {}
    page = MagicMock()
    page.on = MagicMock(side_effect=lambda evt, cb: handlers.__setitem__(evt, cb))

    async def fake_goto(url, **kwargs):
        if fake_resp is not None and "response" in handlers:
            handlers["response"](fake_resp)

    page.goto = AsyncMock(side_effect=fake_goto)
    page.wait_for_timeout = AsyncMock()
    page.query_selector_all = AsyncMock(return_value=[])
    page.content = AsyncMock(return_value="<html></html>")
    page.pdf = AsyncMock(return_value=b"%PDF-1.4 page capture")
    return page


@pytest.mark.asyncio
class TestPlaywrightNetworkPdfCapture:
    """Regression: canvas-based viewers (Adobe Acrobat share links) stream the
    real document as an application/pdf response and never expose it via an <a>
    link or page.pdf(). The fetcher must intercept that response.

    Also guards against the networkidle regression: Adobe's viewer keeps
    connections open forever, so wait_until must not be "networkidle".
    """

    async def test_streamed_pdf_is_captured(self, tmp_path):
        pdf_bytes = b"%PDF-1.4 real adobe document bytes"
        page = _streaming_page(_fake_pdf_response(body=pdf_bytes))

        with patch(
            "playwright.async_api.async_playwright",
            _make_playwright_mock(page),
        ), patch("backend.ingestion.url_fetcher._is_safe_url", return_value=True):
            result = await _playwright_fetch(
                "https://acrobat.adobe.com/id/urn:aaid:sc:AP:test",
                str(tmp_path),
                timeout=5,
            )

        # The streamed PDF is saved, not the blank page.pdf() capture.
        assert result is not None
        assert result.content_type == "application/pdf"
        assert result.method == "playwright_network"
        assert Path(result.file_path).read_bytes() == pdf_bytes

        # Guard the networkidle regression that caused the original failure.
        assert page.goto.call_args.kwargs.get("wait_until") == "load"
        # Browser render timeout must be floored well above the 5s http timeout.
        assert page.goto.call_args.kwargs.get("timeout", 0) >= 30_000

    async def test_streamed_pdf_body_error_returns_none(self, tmp_path):
        # A canvas viewer streams a PDF response, but its body can't be read
        # (stalled/evicted). We must NOT file a blank page.pdf() capture as if
        # it were the real document — return None (fetch failure) instead.
        resp = _fake_pdf_response()
        resp.body = AsyncMock(side_effect=Exception("body not available"))
        page = _streaming_page(resp)

        with patch(
            "playwright.async_api.async_playwright",
            _make_playwright_mock(page),
        ), patch("backend.ingestion.url_fetcher._is_safe_url", return_value=True):
            result = await _playwright_fetch(
                "https://acrobat.adobe.com/id/urn:aaid:sc:AP:test",
                str(tmp_path),
                timeout=5,
            )

        assert result is None
        page.pdf.assert_not_awaited()
        # A stalled/erroring body must be attempted once, not re-awaited every
        # settle iteration (which would multiply the worst-case hang).
        assert resp.body.await_count == 1

    async def test_streamed_pdf_from_unsafe_host_is_skipped(self, tmp_path):
        # A rendered page streaming application/pdf from an internal host must
        # not be captured (SSRF); it falls through to the page.pdf() capture.
        page = _streaming_page(_fake_pdf_response(body=b"internal secret pdf"))

        with patch(
            "playwright.async_api.async_playwright",
            _make_playwright_mock(page),
        ), patch("backend.ingestion.url_fetcher._is_safe_url", return_value=False):
            result = await _playwright_fetch(
                "https://example.com/viewer", str(tmp_path), timeout=5
            )

        assert result is not None
        assert result.method == "playwright_capture"
        assert Path(result.file_path).read_bytes() == b"%PDF-1.4 page capture"

    async def test_oversized_streamed_pdf_discarded(self, tmp_path):
        # A PDF body over the size cap is discarded (OOM guard); with no other
        # readable body, the fetch fails rather than capturing a blank page.
        from backend.ingestion import url_fetcher

        big = b"x" * 32
        page = _streaming_page(_fake_pdf_response(body=big))

        with patch(
            "playwright.async_api.async_playwright",
            _make_playwright_mock(page),
        ), patch("backend.ingestion.url_fetcher._is_safe_url", return_value=True), \
                patch.object(url_fetcher, "_MAX_CAPTURE_BYTES", 16):
            result = await _playwright_fetch(
                "https://acrobat.adobe.com/id/urn:aaid:sc:AP:test",
                str(tmp_path),
                timeout=5,
            )

        assert result is None

    async def test_falls_back_to_page_capture_when_no_pdf_streamed(self, tmp_path):
        # No application/pdf response and no document links -> page.pdf() capture.
        page = MagicMock()
        page.on = MagicMock()
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.query_selector_all = AsyncMock(return_value=[])
        page.content = AsyncMock(return_value="<html><body>no links</body></html>")
        page.pdf = AsyncMock(return_value=b"%PDF-1.4 page capture")

        with patch(
            "playwright.async_api.async_playwright",
            _make_playwright_mock(page),
        ):
            result = await _playwright_fetch(
                "https://example.com/viewer", str(tmp_path), timeout=5
            )

        assert result is not None
        assert result.method == "playwright_capture"
        assert Path(result.file_path).read_bytes() == b"%PDF-1.4 page capture"

    async def test_no_pdf_bails_early_without_full_settle(self, tmp_path):
        # A page that never streams a PDF must not wait out the full settle
        # window before falling through to the capture path.
        from backend.ingestion import url_fetcher

        page = MagicMock()
        page.on = MagicMock()
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.query_selector_all = AsyncMock(return_value=[])
        page.content = AsyncMock(return_value="<html><body>plain</body></html>")
        page.pdf = AsyncMock(return_value=b"%PDF-1.4 page capture")

        with patch(
            "playwright.async_api.async_playwright",
            _make_playwright_mock(page),
        ):
            await _playwright_fetch(
                "https://example.com/viewer", str(tmp_path), timeout=5
            )

        # Bailed at the detect window, not the full settle window.
        assert page.wait_for_timeout.await_count == url_fetcher._DOC_STREAM_DETECT
        assert url_fetcher._DOC_STREAM_DETECT < url_fetcher._DOC_STREAM_SETTLE


# ---------------------------------------------------------------------------
# Issue #32: button-click download capture (_try_click_download)
# ---------------------------------------------------------------------------

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from backend.ingestion.url_fetcher import _try_click_download


def _fake_element(inner_text="", attrs=None, visible=True, enabled=True):
    """A mock Playwright element handle for a clickable control.

    `attrs` maps aria-label/title/value/download -> value (missing -> None).
    """
    attrs = attrs or {}
    el = MagicMock()
    el.is_visible = AsyncMock(return_value=visible)
    el.is_enabled = AsyncMock(return_value=enabled)
    el.inner_text = AsyncMock(return_value=inner_text)

    async def _get_attribute(name):
        return attrs.get(name)

    el.get_attribute = AsyncMock(side_effect=_get_attribute)
    el.click = AsyncMock()
    return el


def _fake_download(tmp_path, url="blob:https://example.com/abc", body=b"%PDF-1.4 downloaded doc", suggested="invoice.pdf"):
    """A mock Playwright Download whose path() points at real bytes on disk."""
    import uuid as _uuid
    disk = tmp_path / f"pw-dl-{_uuid.uuid4().hex}"
    disk.write_bytes(body)
    dl = MagicMock()
    dl.url = url
    dl.suggested_filename = suggested
    dl.path = AsyncMock(return_value=str(disk))
    return dl


class _ExpectDownloadCM:
    """Async context manager standing in for page.expect_download(...).

    On enter yields an info object whose `.value` awaits to `download`. If
    `aexit_exc` is set it is raised from __aexit__ (simulates no download event
    within the timeout — Playwright raises TimeoutError there).
    """

    def __init__(self, download=None, aexit_exc=None, value_exc=None):
        self._download = download
        self._aexit_exc = aexit_exc
        self._value_exc = value_exc

    async def __aenter__(self):
        info = MagicMock()

        async def _value():
            if self._value_exc is not None:
                raise self._value_exc
            return self._download

        # `.value` is awaited by the source: `download = await dl_info.value`.
        type(info).value = property(lambda _self: _value())
        return info

    async def __aexit__(self, exc_type, exc, tb):
        if self._aexit_exc is not None and exc_type is None:
            raise self._aexit_exc
        return False  # never suppress a click exception


def _click_page(elements, expect_cm, url="https://example.com/bill-viewer"):
    """A mock Playwright Page for driving _try_click_download directly."""
    page = MagicMock()
    page.url = url
    page.query_selector_all = AsyncMock(return_value=elements)
    page.expect_download = MagicMock(return_value=expect_cm)
    page.wait_for_timeout = AsyncMock()
    return page


@pytest.mark.asyncio
class TestTryClickDownload:
    """Issue #32: click the single best download control and capture the PDF."""

    async def test_hebrew_download_button_captures(self, tmp_path):
        pdf = b"%PDF-1.4 hebrew download"
        el = _fake_element(inner_text="הורדה")
        dl = _fake_download(tmp_path, url="blob:https://mast.co.il/x", body=pdf)
        page = _click_page([el], _ExpectDownloadCM(download=dl))

        result = await _try_click_download(page, str(tmp_path), [])

        assert result is not None
        assert result.method == "playwright_download_click"
        assert result.page_capture is False
        assert result.content_type == "application/pdf"
        assert Path(result.file_path).read_bytes() == pdf
        el.click.assert_awaited_once()

    async def test_english_download_button_captures(self, tmp_path):
        pdf = b"%PDF-1.4 english download"
        el = _fake_element(inner_text="Download")
        dl = _fake_download(tmp_path, url="blob:https://example.com/y", body=pdf)
        page = _click_page([el], _ExpectDownloadCM(download=dl))

        result = await _try_click_download(page, str(tmp_path), [])

        assert result is not None
        assert result.method == "playwright_download_click"
        assert result.page_capture is False
        assert Path(result.file_path).read_bytes() == pdf
        el.click.assert_awaited_once()

    async def test_keyword_match_via_aria_label_only(self, tmp_path):
        # Empty visible text; the keyword lives only in aria-label.
        pdf = b"%PDF-1.4 aria download"
        el = _fake_element(inner_text="", attrs={"aria-label": "Download invoice"})
        dl = _fake_download(tmp_path, url="blob:https://example.com/z", body=pdf)
        page = _click_page([el], _ExpectDownloadCM(download=dl))

        result = await _try_click_download(page, str(tmp_path), [])

        assert result is not None
        assert result.method == "playwright_download_click"
        assert Path(result.file_path).read_bytes() == pdf
        el.click.assert_awaited_once()

    async def test_ranking_and_denylist(self, tmp_path):
        # Four controls: an ignored non-match, a weak keyword match, the strong
        # download control, and a denylisted money-mover that also matches a
        # keyword. Only the strong download control must be clicked.
        print_btn = _fake_element(inner_text="Print")
        weak = _fake_element(inner_text="Invoice")              # rank 1 (keyword only)
        strong = _fake_element(inner_text="הורדה")              # rank 0 (strong)
        pay = _fake_element(inner_text="שלם חשבונית")           # keyword + denylist -> excluded

        dl = _fake_download(tmp_path, url="blob:https://example.com/dl", body=b"%PDF-1.4 x")
        page = _click_page([print_btn, weak, strong, pay], _ExpectDownloadCM(download=dl))

        result = await _try_click_download(page, str(tmp_path), [])

        assert result is not None
        assert result.method == "playwright_download_click"
        # The strong download control wins ranking and is the only one clicked.
        strong.click.assert_awaited_once()
        weak.click.assert_not_awaited()
        print_btn.click.assert_not_awaited()
        # The money-moving control is NEVER clicked, even though it matched a keyword.
        pay.click.assert_not_awaited()

    async def test_click_raises_returns_none(self, tmp_path):
        # A click that raises must not crash; _try_click_download returns None so
        # the caller falls back to the page.pdf() capture path.
        el = _fake_element(inner_text="Download")
        el.click = AsyncMock(side_effect=Exception("element detached"))
        page = _click_page([el], _ExpectDownloadCM(download=_fake_download(tmp_path)))

        result = await _try_click_download(page, str(tmp_path), [])

        assert result is None
        el.click.assert_awaited_once()

    async def test_no_download_event_no_inline_returns_none(self, tmp_path):
        # expect_download times out (no download fired) and no inline PDF was
        # streamed -> None.
        el = _fake_element(inner_text="Download")
        page = _click_page([el], _ExpectDownloadCM(aexit_exc=PlaywrightTimeoutError("no download")))

        result = await _try_click_download(page, str(tmp_path), [])

        assert result is None
        el.click.assert_awaited_once()

    async def test_no_download_event_but_inline_pdf_captured(self, tmp_path):
        # No download event, but the click streamed an inline application/pdf
        # response -> captured via the inline path.
        el = _fake_element(inner_text="Download")
        inline = _fake_pdf_response(body=b"%PDF-1.4 inline after click")
        page = _click_page([el], _ExpectDownloadCM(aexit_exc=PlaywrightTimeoutError("no download")))

        result = await _try_click_download(page, str(tmp_path), [inline])

        assert result is not None
        assert result.method == "playwright_download_click"
        assert result.content_type == "application/pdf"
        assert Path(result.file_path).read_bytes() == b"%PDF-1.4 inline after click"

    async def test_no_matching_control_returns_none(self, tmp_path):
        # Controls exist but none match the download keyword net.
        page = _click_page(
            [_fake_element(inner_text="Home"), _fake_element(inner_text="Settings")],
            _ExpectDownloadCM(download=_fake_download(tmp_path)),
        )

        result = await _try_click_download(page, str(tmp_path), [])

        assert result is None

    async def test_unsafe_http_download_url_rejected(self, tmp_path):
        # A download whose URL is a real http(s) URL that _is_safe_url rejects
        # must not be captured (SSRF) -> None.
        el = _fake_element(inner_text="Download")
        dl = _fake_download(tmp_path, url="http://169.254.169.254/latest/meta-data")
        page = _click_page([el], _ExpectDownloadCM(download=dl))

        with patch("backend.ingestion.url_fetcher._is_safe_url", return_value=False):
            result = await _try_click_download(page, str(tmp_path), [])

        assert result is None

    async def test_blob_download_captured_when_page_origin_safe(self, tmp_path):
        # blob: URLs are browser-created, so they skip the http(s) SSRF check
        # and are captured when the page's own origin is safe (the default).
        pdf = b"%PDF-1.4 blob doc"
        el = _fake_element(inner_text="Download")
        dl = _fake_download(tmp_path, url="blob:https://example.com/blobbie", body=pdf)
        page = _click_page([el], _ExpectDownloadCM(download=dl))

        result = await _try_click_download(page, str(tmp_path), [])

        assert result is not None
        assert result.method == "playwright_download_click"
        assert Path(result.file_path).read_bytes() == pdf

    async def test_blob_download_rejected_when_page_origin_unsafe(self, tmp_path):
        # Security (issue #32): a blob download from an UNSAFE page origin is
        # rejected — a safe-looking page could have fetched internal bytes into
        # a blob. _is_safe_url(page.url) is the gate for blob/data.
        el = _fake_element(inner_text="Download")
        dl = _fake_download(tmp_path, url="blob:https://example.com/blobbie", body=b"%PDF internal")
        page = _click_page([el], _ExpectDownloadCM(download=dl))

        with patch("backend.ingestion.url_fetcher._is_safe_url", return_value=False):
            result = await _try_click_download(page, str(tmp_path), [])

        assert result is None

    async def test_oversized_download_discarded(self, tmp_path):
        # A downloaded body over the size cap is discarded; with no inline PDF
        # fallback the attempt returns None.
        from backend.ingestion import url_fetcher

        el = _fake_element(inner_text="Download")
        dl = _fake_download(tmp_path, url="blob:https://example.com/big", body=b"x" * 64)
        page = _click_page([el], _ExpectDownloadCM(download=dl))

        with patch.object(url_fetcher, "_MAX_CAPTURE_BYTES", 16):
            result = await _try_click_download(page, str(tmp_path), [])

        assert result is None

    async def test_hidden_or_disabled_control_skipped(self, tmp_path):
        # A matching download control that is not visible/enabled is skipped;
        # with no other candidate, returns None (caller falls back to page.pdf()).
        hidden = _fake_element(inner_text="הורדה", visible=False)
        disabled = _fake_element(inner_text="Download", enabled=False)
        page = _click_page([hidden, disabled], _ExpectDownloadCM(download=None))
        result = await _try_click_download(page, str(tmp_path), [])
        assert result is None
        hidden.click.assert_not_awaited()
        disabled.click.assert_not_awaited()


@pytest.mark.asyncio
class TestPlaywrightFetchClickAndCaptureFallback:
    """Issue #32: end-to-end _playwright_fetch behaviour around click capture."""

    async def test_falls_to_page_capture_flags_page_capture(self, tmp_path):
        # No load-time PDF stream, no <a> links, and no matching download
        # control -> the viewer-shell page.pdf() capture, flagged page_capture.
        page = MagicMock()
        page.on = MagicMock()
        page.url = "https://example.com/viewer"
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        # [] for password fields AND for the click-download control scan.
        page.query_selector_all = AsyncMock(return_value=[])
        page.content = AsyncMock(return_value="<html><body>viewer shell</body></html>")
        page.pdf = AsyncMock(return_value=b"%PDF-1.4 shell capture")

        with patch(
            "playwright.async_api.async_playwright",
            _make_playwright_mock(page),
        ):
            result = await _playwright_fetch(
                "https://example.com/viewer", str(tmp_path), timeout=5
            )

        assert result is not None
        assert result.method == "playwright_capture"
        assert result.page_capture is True
        assert result.auth_wall is False
        assert Path(result.file_path).read_bytes() == b"%PDF-1.4 shell capture"

    async def test_auth_wall_capture_is_not_page_capture(self, tmp_path):
        # The password-field branch produces an auth_wall capture that must NOT
        # be flagged page_capture (it is intentionally routed differently).
        password_field = MagicMock()
        page = MagicMock()
        page.on = MagicMock()
        page.url = "https://example.com/login"
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        # Password branch is reached first and returns before the click scan.
        page.query_selector_all = AsyncMock(return_value=[password_field])
        page.content = AsyncMock(return_value="<html><body>login</body></html>")
        page.pdf = AsyncMock(return_value=b"%PDF-1.4 login shell")

        with patch(
            "playwright.async_api.async_playwright",
            _make_playwright_mock(page),
        ):
            result = await _playwright_fetch(
                "https://example.com/login", str(tmp_path), timeout=5
            )

        assert result is not None
        assert result.auth_wall is True
        assert result.page_capture is False
        assert result.method == "playwright_capture"

    async def test_click_download_result_is_returned(self, tmp_path):
        # When _try_click_download captures a real download, _playwright_fetch
        # returns it and does NOT fall through to the page.pdf() shell capture.
        page = MagicMock()
        page.on = MagicMock()
        page.url = "https://example.com/viewer"
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.query_selector_all = AsyncMock(return_value=[])  # no password fields
        page.content = AsyncMock(return_value="<html><body>viewer</body></html>")
        page.pdf = AsyncMock(return_value=b"%PDF-1.4 shell")  # must NOT be used
        (tmp_path / "real.pdf").write_bytes(b"%PDF-1.4 real bill")
        clicked = FetchResult(
            file_path=str(tmp_path / "real.pdf"), content_type="application/pdf",
            original_url="https://example.com/viewer", method="playwright_download_click",
        )
        with patch("playwright.async_api.async_playwright", _make_playwright_mock(page)), \
             patch("backend.ingestion.url_fetcher._try_click_download", AsyncMock(return_value=clicked)):
            result = await _playwright_fetch("https://example.com/viewer", str(tmp_path), timeout=5)
        assert result is clicked
        assert result.method == "playwright_download_click"
        page.pdf.assert_not_awaited()


# ---------------------------------------------------------------------------
# Mast.co.il bill-viewer site handler (issue #32)
# ---------------------------------------------------------------------------

import json as _json
from backend.ingestion.url_fetcher import _mast_guid, _fetch_mast_bill


def _mast_client(api_json, pdf_bytes=b"%PDF-1.4 real bill", pdf_status=200, pdf_ct="application/pdf"):
    """Patched AsyncClient whose .get dispatches the mast API vs the pdfLink."""
    async def fake_get(url, **kw):
        if "GetStubsByGuid" in url:
            return _make_response(200, _json.dumps(api_json).encode(), "application/json")
        return _make_response(pdf_status, pdf_bytes, pdf_ct)
    mc = AsyncMock()
    mc.get = fake_get
    mc.__aenter__ = AsyncMock(return_value=mc)
    mc.__aexit__ = AsyncMock(return_value=False)
    return mc


class TestMastBillHandler:
    def test_guid_detection(self):
        assert _mast_guid("https://mast.co.il/bill-viewer/abc%2Fdef%3D%3D") == "abc/def=="
        assert _mast_guid("https://www.mast.co.il/bill-viewer/xyz") == "xyz"
        assert _mast_guid("https://evil.com/bill-viewer/xyz") is None
        assert _mast_guid("https://mast.co.il/other/xyz") is None
        assert _mast_guid("https://mast.co.il/bill-viewer/") is None

    async def test_fetch_bill_happy(self, tmp_path):
        api = {"stubExtentionList": [{"pdfLink": "https://sabillsprod.blob.core.windows.net/orda/x.pdf"}]}
        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient", return_value=_mast_client(api)):
            r = await _fetch_mast_bill("https://mast.co.il/bill-viewer/abc", str(tmp_path), 5)
        assert r is not None and r.method == "mast_api" and not r.page_capture
        assert Path(r.file_path).read_bytes() == b"%PDF-1.4 real bill"

    async def test_multistub_takes_first(self, tmp_path):
        api = {"stubExtentionList": [
            {"pdfLink": "https://sabillsprod.blob.core.windows.net/a.pdf"},
            {"pdfLink": "https://sabillsprod.blob.core.windows.net/b.pdf"},
        ]}
        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient", return_value=_mast_client(api)):
            r = await _fetch_mast_bill("https://mast.co.il/bill-viewer/abc", str(tmp_path), 5)
        assert r is not None and r.method == "mast_api"

    async def test_no_pdflink_returns_none(self, tmp_path):
        api = {"stubExtentionList": []}
        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient", return_value=_mast_client(api)):
            r = await _fetch_mast_bill("https://mast.co.il/bill-viewer/abc", str(tmp_path), 5)
        assert r is None

    async def test_api_http_error_returns_none(self, tmp_path):
        async def fake_get(url, **kw):
            return _make_response(500, b"err", "text/html")
        mc = AsyncMock(); mc.get = fake_get
        mc.__aenter__ = AsyncMock(return_value=mc); mc.__aexit__ = AsyncMock(return_value=False)
        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient", return_value=mc):
            r = await _fetch_mast_bill("https://mast.co.il/bill-viewer/abc", str(tmp_path), 5)
        assert r is None

    async def test_unsafe_pdflink_returns_none(self, tmp_path):
        api = {"stubExtentionList": [{"pdfLink": "https://169.254.169.254/x.pdf"}]}
        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient", return_value=_mast_client(api)), \
             patch("backend.ingestion.url_fetcher._is_safe_url", return_value=False):
            r = await _fetch_mast_bill("https://mast.co.il/bill-viewer/abc", str(tmp_path), 5)
        assert r is None

    async def test_fetch_url_routes_mast_to_api(self, tmp_path):
        api = {"stubExtentionList": [{"pdfLink": "https://sabillsprod.blob.core.windows.net/x.pdf"}]}
        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient", return_value=_mast_client(api)), \
             patch("backend.ingestion.url_fetcher._is_safe_url", return_value=True):
            r = await fetch_url("https://mast.co.il/bill-viewer/abc", str(tmp_path))
        assert r is not None and r.method == "mast_api"

    async def test_pdf_fetch_error_returns_none(self, tmp_path):
        # API resolves a pdfLink, but the blob GET 500s -> None (not a crash).
        api = {"stubExtentionList": [{"pdfLink": "https://sabillsprod.blob.core.windows.net/x.pdf"}]}
        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient", return_value=_mast_client(api, pdf_status=500)):
            r = await _fetch_mast_bill("https://mast.co.il/bill-viewer/abc", str(tmp_path), 5)
        assert r is None

    async def test_api_non_dict_json_returns_none(self, tmp_path):
        # Valid JSON but wrong shape (a top-level array) must degrade to None,
        # NOT raise AttributeError up through the unguarded gmail caller.
        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient", return_value=_mast_client([])):
            r = await _fetch_mast_bill("https://mast.co.il/bill-viewer/abc", str(tmp_path), 5)
        assert r is None

    async def test_api_malformed_json_returns_none(self, tmp_path):
        # Non-JSON body with a JSON content-type -> resp.json() raises -> None.
        async def fake_get(url, **kw):
            return _make_response(200, b"<not json>", "application/json")
        mc = AsyncMock(); mc.get = fake_get
        mc.__aenter__ = AsyncMock(return_value=mc); mc.__aexit__ = AsyncMock(return_value=False)
        with patch("backend.ingestion.url_fetcher.httpx.AsyncClient", return_value=mc):
            r = await _fetch_mast_bill("https://mast.co.il/bill-viewer/abc", str(tmp_path), 5)
        assert r is None
