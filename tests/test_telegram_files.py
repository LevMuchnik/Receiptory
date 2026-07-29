import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.ingestion.service import IngestionResult


def _telegram_file_context(tmp_data_dir, username="worker"):
    update = MagicMock()
    update.effective_user.id = 123
    update.effective_user.username = username
    update.message.reply_text = AsyncMock()

    downloaded = MagicMock()
    downloaded.download_to_drive = AsyncMock()
    context = MagicMock()
    context.bot_data = {"data_dir": str(tmp_data_dir)}
    context.bot.get_file = AsyncMock(return_value=downloaded)
    return update, context


@pytest.mark.asyncio
async def test_telegram_file_uses_shared_ingestion_service(db_path, tmp_data_dir):
    from backend.ingestion.telegram import _ingest_file

    update, context = _telegram_file_context(tmp_data_dir)
    result = IngestionResult("accepted", 41, "abc123")

    with patch(
        "backend.ingestion.telegram.ingest_local_file", return_value=result
    ) as ingest:
        await _ingest_file(update, context, "telegram-file-id", "receipt.jpg")

    call = ingest.call_args
    assert call.kwargs["filename"] == "receipt.jpg"
    assert call.kwargs["submission_channel"] == "telegram"
    assert call.kwargs["sender_identifier"] == "telegram:@worker"
    assert call.kwargs["data_dir"] == str(tmp_data_dir)
    assert not os.path.exists(call.args[0])
    update.message.reply_text.assert_awaited_once_with(
        "Received! Document #41 queued for processing."
    )


@pytest.mark.asyncio
async def test_telegram_file_reports_shared_service_duplicate(db_path, tmp_data_dir):
    from backend.ingestion.telegram import _ingest_file

    update, context = _telegram_file_context(tmp_data_dir, username=None)
    result = IngestionResult("duplicate", 9, "abc123")

    with patch(
        "backend.ingestion.telegram.ingest_local_file", return_value=result
    ):
        await _ingest_file(update, context, "telegram-file-id", "receipt.pdf")

    update.message.reply_text.assert_awaited_once_with(
        "Duplicate file — already exists as document #9."
    )
