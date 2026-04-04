"""Tests for the smart two-phase email ingestion pipeline."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from backend.ingestion.gmail import _process_message_logic


def _make_email_context(attachments=None, urls=None, sender="test@vendor.com",
                        subject="Invoice", body="Your invoice is attached."):
    return {
        "sender_email": sender,
        "subject": subject,
        "body_text": body,
        "html_body": f"<p>{body}</p>",
        "attachments": attachments or [],
        "urls": urls or [],
        "authorized": True,
    }


def _make_attachment(filename="invoice.pdf", content_type="application/pdf",
                     content=b"%PDF-1.4 fake pdf content"):
    return {
        "filename": filename,
        "content_type": content_type,
        "size": len(content),
        "content": content,
    }


class TestPhase1AttachmentClassification:
    """Phase 1: When email has attachments, classify them first."""

    def test_attachment_classified_as_financial_skips_urls(self, tmp_path):
        """If attachment is a financial doc, ingest it and skip URLs."""
        ctx = _make_email_context(
            attachments=[_make_attachment()],
            urls=["https://vendor.com/invoice/view"],
        )
        with patch("backend.ingestion.gmail._classify_attachments", return_value=["invoice.pdf"]) as mock_classify, \
             patch("backend.ingestion.gmail._ingest_attachment", return_value={"status": "ingested", "doc_id": 1}) as mock_ingest, \
             patch("backend.ingestion.gmail._process_urls") as mock_urls:
            result = _process_message_logic(ctx, str(tmp_path))
            mock_classify.assert_called_once()
            mock_ingest.assert_called_once()
            mock_urls.assert_not_called()
            assert len(result["ingested"]) == 1

    def test_no_attachment_qualifies_falls_through_to_urls(self, tmp_path):
        """If no attachment qualifies, proceed to URL processing."""
        ctx = _make_email_context(
            attachments=[_make_attachment(filename="logo.png", content_type="image/png", content=b"tiny")],
            urls=["https://vendor.com/invoice/view"],
        )
        with patch("backend.ingestion.gmail._classify_attachments", return_value=[]) as mock_classify, \
             patch("backend.ingestion.gmail._process_urls", return_value=[{"status": "ingested", "doc_id": 2}]) as mock_urls:
            result = _process_message_logic(ctx, str(tmp_path))
            mock_classify.assert_called_once()
            mock_urls.assert_called_once()
            assert len(result["ingested"]) == 1


class TestPhase2URLFallback:
    """Phase 2: URL processing when no attachment qualifies."""

    def test_urls_only_email_processes_urls(self, tmp_path):
        """Email with only URLs goes directly to URL processing."""
        ctx = _make_email_context(
            attachments=[],
            urls=["https://vendor.com/invoice/123"],
        )
        with patch("backend.ingestion.gmail._process_urls", return_value=[{"status": "ingested", "doc_id": 3}]) as mock_urls:
            result = _process_message_logic(ctx, str(tmp_path))
            mock_urls.assert_called_once()
            assert len(result["ingested"]) == 1


class TestNothingFound:
    """Notification when no documents are ingested."""

    def test_nothing_found_notification_sent(self, tmp_path):
        """When no attachments or URLs produce documents, notify."""
        ctx = _make_email_context(attachments=[], urls=[])
        with patch("backend.ingestion.gmail._notify_nothing_found") as mock_notify:
            result = _process_message_logic(ctx, str(tmp_path))
            mock_notify.assert_called_once_with(
                sender_email="test@vendor.com",
                subject="Invoice",
                attachment_count=0,
                url_count=0,
            )

    def test_nothing_found_when_urls_produce_no_docs(self, tmp_path):
        """URLs were checked but nothing qualified."""
        ctx = _make_email_context(
            attachments=[],
            urls=["https://marketing.com/promo"],
        )
        with patch("backend.ingestion.gmail._process_urls", return_value=[]) as mock_urls, \
             patch("backend.ingestion.gmail._notify_nothing_found") as mock_notify:
            result = _process_message_logic(ctx, str(tmp_path))
            mock_notify.assert_called_once()
