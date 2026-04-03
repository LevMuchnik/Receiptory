"""Tests for Telegram bot text message handler (URL ingestion)."""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from backend.ingestion.url_fetcher import FetchResult


def _make_update(text="", user_id=123, username="testuser"):
    """Create a mock Telegram Update with a text message."""
    update = AsyncMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = username
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


def _make_context(data_dir):
    """Create a mock Telegram context with bot_data."""
    context = MagicMock()
    context.bot_data = {"data_dir": str(data_dir)}
    return context


@pytest.mark.asyncio
async def test_text_no_urls(db_path, tmp_data_dir):
    """Text without URLs should reply with 'No document or URL found'."""
    from backend.ingestion.telegram import handle_text

    update = _make_update(text="Hello, this is just a message.")
    context = _make_context(tmp_data_dir)

    with patch("backend.ingestion.telegram._is_authorized", return_value=True):
        await handle_text(update, context)

    update.message.reply_text.assert_called_once_with(
        "No document or URL found in your message."
    )


@pytest.mark.asyncio
async def test_text_unauthorized(db_path, tmp_data_dir):
    """Unauthorized user should be rejected."""
    from backend.ingestion.telegram import handle_text

    update = _make_update(text="https://example.com/receipt.pdf")
    context = _make_context(tmp_data_dir)

    with patch("backend.ingestion.telegram._is_authorized", return_value=False):
        await handle_text(update, context)

    update.message.reply_text.assert_called_once_with(
        "You are not authorized to use this bot."
    )


@pytest.mark.asyncio
async def test_text_valid_url(db_path, tmp_data_dir):
    """Valid URL should be triaged, fetched, and create a document in DB."""
    from backend.ingestion.telegram import handle_text
    from backend.database import get_connection

    url = "https://example.com/invoice.pdf"
    update = _make_update(text=f"Here is my receipt: {url}")
    context = _make_context(tmp_data_dir)

    # Create a temp file to simulate a fetched document
    tmp_dir = os.path.join(str(tmp_data_dir), "storage", "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    fake_file = os.path.join(tmp_dir, "fakefile.pdf")
    with open(fake_file, "wb") as f:
        f.write(b"%PDF-1.4 fake content for testing")

    fetch_result = FetchResult(
        file_path=fake_file,
        content_type="application/pdf",
        original_url=url,
        auth_wall=False,
        method="direct",
    )

    with (
        patch("backend.ingestion.telegram._is_authorized", return_value=True),
        patch(
            "backend.ingestion.telegram.triage_telegram_urls",
            new_callable=AsyncMock,
            return_value=[url],
        ),
        patch(
            "backend.ingestion.telegram.fetch_url",
            new_callable=AsyncMock,
            return_value=fetch_result,
        ),
        patch("backend.ingestion.telegram.notify", create=True),
    ):
        await handle_text(update, context)

    # Check that a document was created in the DB
    with get_connection() as conn:
        doc = conn.execute(
            "SELECT * FROM documents WHERE source_url = ?", (url,)
        ).fetchone()

    assert doc is not None
    assert doc["status"] == "pending"
    assert doc["submission_channel"] == "telegram"
    assert doc["source_url"] == url
    assert doc["sender_identifier"] == "telegram:@testuser"

    # Check reply mentions document ID
    reply_text = update.message.reply_text.call_args_list[-1][0][0]
    assert f"#{doc['id']}" in reply_text


@pytest.mark.asyncio
async def test_text_auth_wall_url(db_path, tmp_data_dir):
    """Auth-gated URL should create document with status='needs_review'."""
    from backend.ingestion.telegram import handle_text
    from backend.database import get_connection

    url = "https://portal.example.com/invoice/123"
    update = _make_update(text=f"Invoice here: {url}")
    context = _make_context(tmp_data_dir)

    tmp_dir = os.path.join(str(tmp_data_dir), "storage", "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    fake_file = os.path.join(tmp_dir, "authwall.pdf")
    with open(fake_file, "wb") as f:
        f.write(b"%PDF-1.4 auth wall capture")

    fetch_result = FetchResult(
        file_path=fake_file,
        content_type="application/pdf",
        original_url=url,
        auth_wall=True,
        method="playwright_capture",
    )

    with (
        patch("backend.ingestion.telegram._is_authorized", return_value=True),
        patch(
            "backend.ingestion.telegram.triage_telegram_urls",
            new_callable=AsyncMock,
            return_value=[url],
        ),
        patch(
            "backend.ingestion.telegram.fetch_url",
            new_callable=AsyncMock,
            return_value=fetch_result,
        ),
        patch("backend.ingestion.telegram.notify", create=True),
    ):
        await handle_text(update, context)

    with get_connection() as conn:
        doc = conn.execute(
            "SELECT * FROM documents WHERE source_url = ?", (url,)
        ).fetchone()

    assert doc is not None
    assert doc["status"] == "needs_review"
    assert "Auth-gated URL" in doc["user_notes"]
    assert doc["source_url"] == url


@pytest.mark.asyncio
async def test_text_triage_filters_all(db_path, tmp_data_dir):
    """When triage returns empty, reply with 'No receipt/invoice URLs identified'."""
    from backend.ingestion.telegram import handle_text

    update = _make_update(text="Check out https://twitter.com/someone")
    context = _make_context(tmp_data_dir)

    with (
        patch("backend.ingestion.telegram._is_authorized", return_value=True),
        patch(
            "backend.ingestion.telegram.triage_telegram_urls",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        await handle_text(update, context)

    update.message.reply_text.assert_called_once_with(
        "No receipt/invoice URLs identified in your message."
    )


@pytest.mark.asyncio
async def test_text_fetch_returns_none(db_path, tmp_data_dir):
    """When fetch_url returns None, reply with error and no document created."""
    from backend.ingestion.telegram import handle_text
    from backend.database import get_connection

    url = "https://example.com/broken.pdf"
    update = _make_update(text=f"Receipt: {url}")
    context = _make_context(tmp_data_dir)

    with (
        patch("backend.ingestion.telegram._is_authorized", return_value=True),
        patch(
            "backend.ingestion.telegram.triage_telegram_urls",
            new_callable=AsyncMock,
            return_value=[url],
        ),
        patch(
            "backend.ingestion.telegram.fetch_url",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        await handle_text(update, context)

    # Should report the fetch failure
    calls = [call[0][0] for call in update.message.reply_text.call_args_list]
    assert any("Failed to fetch URL" in c for c in calls)

    # No document should be created
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert count == 0
