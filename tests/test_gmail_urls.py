"""Tests for Gmail email parsing utilities."""

import os
import pytest
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from unittest.mock import MagicMock, patch, AsyncMock


def _build_raw_email(subject="Test", sender="test@example.com",
                     html_body=None, attachments=None):
    """Build raw email bytes from components."""
    msg = MIMEMultipart()
    msg["From"] = f"Test User <{sender}>"
    msg["Subject"] = subject
    msg["To"] = "me@example.com"

    if html_body:
        html_part = MIMEText(html_body, "html")
        msg.attach(html_part)
    else:
        msg.attach(MIMEText("Plain text body", "plain"))

    if attachments:
        for att in attachments:
            part = MIMEApplication(att["content"], Name=att["filename"])
            part["Content-Disposition"] = f'attachment; filename="{att["filename"]}"'
            if "content_type" in att:
                part.set_type(att["content_type"])
            msg.attach(part)

    return msg.as_bytes()


def _mock_imap(raw_bytes):
    """Create a mock IMAP connection that returns raw_bytes for fetch."""
    mail = MagicMock()
    mail.fetch.return_value = ("OK", [(b"1", raw_bytes)])
    mail.store.return_value = ("OK", [])
    return mail


class TestExtractUrlsFromHtml:
    def test_extracts_http_urls(self):
        from backend.ingestion.gmail import _extract_urls_from_html

        html = '<a href="https://example.com/invoice.pdf">Invoice</a>'
        assert _extract_urls_from_html(html) == ["https://example.com/invoice.pdf"]

    def test_excludes_unsubscribe(self):
        from backend.ingestion.gmail import _extract_urls_from_html

        html = '<a href="https://example.com/unsubscribe">Unsub</a>'
        assert _extract_urls_from_html(html) == []

    def test_excludes_mailto(self):
        from backend.ingestion.gmail import _extract_urls_from_html

        html = '<a href="mailto:test@example.com">Email</a>'
        assert _extract_urls_from_html(html) == []

    def test_excludes_tel(self):
        from backend.ingestion.gmail import _extract_urls_from_html

        html = '<a href="tel:+1234567890">Call</a>'
        assert _extract_urls_from_html(html) == []

    def test_ignores_non_http(self):
        from backend.ingestion.gmail import _extract_urls_from_html

        html = '<a href="ftp://files.example.com/doc.pdf">FTP</a>'
        assert _extract_urls_from_html(html) == []


class TestCollectAttachments:
    def test_collects_pdf_attachment(self):
        from backend.ingestion.gmail import _collect_attachments
        import email as email_mod
        from email import policy as ep

        raw = _build_raw_email(
            attachments=[{"filename": "receipt.pdf", "content": b"%PDF-1.4 test"}]
        )
        parsed = email_mod.message_from_bytes(raw, policy=ep.default)
        atts = _collect_attachments(parsed)
        assert len(atts) == 1
        assert atts[0]["filename"] == "receipt.pdf"
        assert atts[0]["content"] == b"%PDF-1.4 test"
        assert atts[0]["size"] == len(b"%PDF-1.4 test")

    def test_no_attachments(self):
        from backend.ingestion.gmail import _collect_attachments
        import email as email_mod
        from email import policy as ep

        raw = _build_raw_email(html_body="<p>Hello</p>")
        parsed = email_mod.message_from_bytes(raw, policy=ep.default)
        atts = _collect_attachments(parsed)
        assert len(atts) == 0


def _make_fetched_file(tmp_data_dir, name, content):
    """Create a real fetched file on disk for _ingest_url to hash/save.

    _ingest_url computes a hash and copies the original, so the file must
    exist. Content is caller-supplied to keep each test's hash unique and
    avoid the SHA-256 duplicate-rejection path.
    """
    import os

    tmp_dir = os.path.join(str(tmp_data_dir), "storage", "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    file_path = os.path.join(tmp_dir, name)
    with open(file_path, "wb") as f:
        f.write(content)
    return file_path


class TestIngestUrlPageCapture:
    """Issue #32: a page.pdf() viewer-shell capture must route to needs_review,
    not be filed as a successfully processed real document."""

    def test_page_capture_routes_to_needs_review(self, db_path, tmp_data_dir):
        """page_capture=True (authorized) -> needs_review with a verify note."""
        from backend.ingestion.gmail import _ingest_url
        from backend.ingestion.url_fetcher import FetchResult
        from backend.database import get_connection

        url = "https://www.mast.co.il/bill-viewer/jpIBk123"
        fake_file = _make_fetched_file(
            tmp_data_dir, "capture.pdf",
            b"%PDF-1.4 viewer shell page capture unique content 32a",
        )
        fetch_result = FetchResult(
            file_path=fake_file,
            content_type="application/pdf",
            original_url=url,
            page_capture=True,
            method="playwright_capture",
        )

        result = _ingest_url(url, "biller@mast.co.il", str(tmp_data_dir), True,
                             fetch_result=fetch_result)
        assert result["status"] == "ingested"

        with get_connection() as conn:
            doc = conn.execute(
                "SELECT * FROM documents WHERE source_url = ?", (url,)
            ).fetchone()

        assert doc is not None
        # Downgraded despite an authorized sender.
        assert doc["status"] == "needs_review"
        assert doc["status"] != "pending"
        # user_notes set, non-empty, and tells the user to verify the PDF.
        notes = doc["user_notes"] or ""
        assert notes.strip()
        assert "verify the pdf" in notes.lower()

    def test_normal_authorized_url_stays_pending(self, db_path, tmp_data_dir):
        """page_capture=False, auth_wall=False (authorized) -> pending.

        Proves the downgrade is specific to page_capture, not applied to
        every authorized URL ingest."""
        from backend.ingestion.gmail import _ingest_url
        from backend.ingestion.url_fetcher import FetchResult
        from backend.database import get_connection

        url = "https://vendor.example.com/invoice/direct.pdf"
        fake_file = _make_fetched_file(
            tmp_data_dir, "direct.pdf",
            b"%PDF-1.4 real downloaded document unique content 32b",
        )
        fetch_result = FetchResult(
            file_path=fake_file,
            content_type="application/pdf",
            original_url=url,
            page_capture=False,
            auth_wall=False,
            method="direct",
        )

        result = _ingest_url(url, "vendor@example.com", str(tmp_data_dir), True,
                             fetch_result=fetch_result)
        assert result["status"] == "ingested"

        with get_connection() as conn:
            doc = conn.execute(
                "SELECT * FROM documents WHERE source_url = ?", (url,)
            ).fetchone()

        assert doc is not None
        assert doc["status"] == "pending"
        assert not (doc["user_notes"] or "").strip()

    def test_auth_wall_still_needs_review(self, db_path, tmp_data_dir):
        """auth_wall=True (authorized) -> needs_review (unchanged behavior)."""
        from backend.ingestion.gmail import _ingest_url
        from backend.ingestion.url_fetcher import FetchResult
        from backend.database import get_connection

        url = "https://portal.example.com/login/invoice/99"
        fake_file = _make_fetched_file(
            tmp_data_dir, "authwall.pdf",
            b"%PDF-1.4 login page capture unique content 32c",
        )
        fetch_result = FetchResult(
            file_path=fake_file,
            content_type="application/pdf",
            original_url=url,
            auth_wall=True,
            method="playwright_capture",
        )

        result = _ingest_url(url, "portal@example.com", str(tmp_data_dir), True,
                             fetch_result=fetch_result)
        assert result["status"] == "ingested"

        with get_connection() as conn:
            doc = conn.execute(
                "SELECT * FROM documents WHERE source_url = ?", (url,)
            ).fetchone()

        assert doc is not None
        assert doc["status"] == "needs_review"
        assert "auth" in (doc["user_notes"] or "").lower()


