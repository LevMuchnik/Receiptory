"""Tests for Gmail triage-based email processing flow."""

import os
import pytest
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from unittest.mock import MagicMock, patch, AsyncMock

from backend.ingestion.url_triage import TriageResult


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


class TestProcessMessage:
    """Tests for the triage-based _process_message flow."""

    def test_attachment_and_url_triggers_triage(self, db_path, tmp_data_dir):
        """Email with attachment + URL should call triage_email."""
        from backend.ingestion.gmail import _process_message

        html_body = '<p>Your invoice: <a href="https://example.com/invoice.pdf">Download</a></p>'
        raw = _build_raw_email(
            html_body=html_body,
            attachments=[{"filename": "logo.png", "content": b"\x89PNG tiny logo"}],
        )
        mail = _mock_imap(raw)

        # Triage recommends: ingest the URL, skip the tiny logo
        triage_result = TriageResult(
            ingest_attachments=[],
            ingest_urls=["https://example.com/invoice.pdf"],
        )

        with (
            patch("backend.ingestion.gmail._is_sender_authorized", return_value=True),
            patch("backend.ingestion.gmail._run_async") as mock_run_async,
            patch("backend.ingestion.gmail._ingest_url") as mock_ingest_url,
        ):
            mock_run_async.return_value = triage_result
            mock_ingest_url.return_value = {"url": "https://example.com/invoice.pdf", "status": "ingested", "doc_id": 1}

            result = _process_message(mail, b"1", str(tmp_data_dir))

        # triage was called (via _run_async)
        mock_run_async.assert_called_once()
        # URL was ingested
        mock_ingest_url.assert_called_once_with(
            "https://example.com/invoice.pdf", result["sender"], str(tmp_data_dir), True
        )
        assert result["ingested"][0]["status"] == "ingested"

    def test_attachment_only_no_triage(self, db_path, tmp_data_dir):
        """Email with only attachments (no URLs) should NOT call triage."""
        from backend.ingestion.gmail import _process_message

        raw = _build_raw_email(
            html_body="<p>See attached.</p>",
            attachments=[{"filename": "receipt.pdf", "content": b"%PDF-1.4 receipt content"}],
        )
        mail = _mock_imap(raw)

        with (
            patch("backend.ingestion.gmail._is_sender_authorized", return_value=True),
            patch("backend.ingestion.gmail._run_async") as mock_run_async,
            patch("backend.ingestion.gmail._ingest_attachment") as mock_ingest_att,
        ):
            mock_ingest_att.return_value = {"filename": "receipt.pdf", "status": "ingested", "doc_id": 1}

            result = _process_message(mail, b"1", str(tmp_data_dir))

        # Triage should NOT have been called
        mock_run_async.assert_not_called()
        # Attachment was ingested directly
        mock_ingest_att.assert_called_once()
        assert result["ingested"][0]["status"] == "ingested"

    def test_no_attachment_no_url_creates_needs_review(self, db_path, tmp_data_dir):
        """Email with no attachments and no URLs should create needs_review document."""
        from backend.ingestion.gmail import _process_message
        from backend.database import get_connection

        raw = _build_raw_email(
            subject="Meeting notes",
            html_body="<p>Just some text, no links.</p>",
        )
        mail = _mock_imap(raw)

        with patch("backend.ingestion.gmail._is_sender_authorized", return_value=True):
            result = _process_message(mail, b"1", str(tmp_data_dir))

        assert len(result["ingested"]) == 1
        assert result["ingested"][0]["status"] == "needs_review"

        doc_id = result["ingested"][0]["doc_id"]
        with get_connection() as conn:
            doc = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()

        assert doc is not None
        assert doc["status"] == "needs_review"
        assert "Meeting notes" in doc["user_notes"]
        assert doc["submission_channel"] == "email"

    def test_urls_only_triggers_triage(self, db_path, tmp_data_dir):
        """Email with URLs but no attachments should call triage on URLs."""
        from backend.ingestion.gmail import _process_message

        html_body = '<p>Receipt: <a href="https://store.example.com/receipt/123">View</a></p>'
        raw = _build_raw_email(html_body=html_body)
        mail = _mock_imap(raw)

        triage_result = TriageResult(
            ingest_attachments=[],
            ingest_urls=["https://store.example.com/receipt/123"],
        )

        with (
            patch("backend.ingestion.gmail._is_sender_authorized", return_value=True),
            patch("backend.ingestion.gmail._run_async") as mock_run_async,
            patch("backend.ingestion.gmail._ingest_url") as mock_ingest_url,
        ):
            mock_run_async.return_value = triage_result
            mock_ingest_url.return_value = {"url": "https://store.example.com/receipt/123", "status": "ingested", "doc_id": 1}

            result = _process_message(mail, b"1", str(tmp_data_dir))

        mock_run_async.assert_called_once()
        mock_ingest_url.assert_called_once()
        assert result["ingested"][0]["status"] == "ingested"

    def test_message_marked_as_seen(self, db_path, tmp_data_dir):
        """All processed messages should be marked as seen."""
        from backend.ingestion.gmail import _process_message

        raw = _build_raw_email(html_body="<p>Hello</p>")
        mail = _mock_imap(raw)

        with patch("backend.ingestion.gmail._is_sender_authorized", return_value=True):
            _process_message(mail, b"1", str(tmp_data_dir))

        mail.store.assert_called_once_with(b"1", "+FLAGS", "\\Seen")
