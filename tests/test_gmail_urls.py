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


