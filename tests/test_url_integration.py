"""Integration test: full URL ingestion flow from fetch to document creation."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path

from backend.database import get_connection


class TestFullUrlFlow:

    @pytest.mark.asyncio
    async def test_telegram_text_to_document(self, db_path, tmp_data_dir):
        """Full flow: Telegram text message -> triage -> fetch -> document in DB."""
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

    def test_migration_adds_source_url_column(self, db_path):
        """Verify the source_url column exists after migration."""
        with get_connection() as conn:
            conn.execute("SELECT source_url FROM documents LIMIT 1")

    @pytest.mark.asyncio
    async def test_telegram_auth_wall_sets_needs_review(self, db_path, tmp_data_dir):
        """Auth-walled URL creates needs_review document with note."""
        from backend.ingestion.telegram import handle_text
        from backend.ingestion.url_fetcher import FetchResult

        fake_pdf = tmp_data_dir / "auth.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4 auth wall page capture unique content 999")

        update = MagicMock()
        update.effective_user.id = 200
        update.effective_user.username = "authtest"
        update.message.text = "Invoice: https://portal.example.com/invoice/456"
        update.message.reply_text = AsyncMock()

        context = MagicMock()
        context.bot_data = {"data_dir": str(tmp_data_dir)}

        with patch("backend.ingestion.telegram._is_authorized", return_value=True), \
             patch("backend.ingestion.telegram.triage_telegram_urls", new_callable=AsyncMock,
                   return_value=["https://portal.example.com/invoice/456"]), \
             patch("backend.ingestion.telegram.fetch_url", new_callable=AsyncMock,
                   return_value=FetchResult(
                       file_path=str(fake_pdf),
                       content_type="application/pdf",
                       original_url="https://portal.example.com/invoice/456",
                       auth_wall=True,
                       method="playwright_capture",
                   )):
            await handle_text(update, context)

        with get_connection() as conn:
            doc = conn.execute("SELECT * FROM documents ORDER BY id DESC LIMIT 1").fetchone()

        assert doc["status"] == "needs_review"
        assert doc["source_url"] == "https://portal.example.com/invoice/456"
        assert "auth" in (doc["user_notes"] or "").lower()
