import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from backend.database import get_connection
from backend.config import init_settings, set_setting
from backend.processing.queue import (get_next_pending, reset_stuck_processing, get_queue_status)

@pytest.fixture
def setup_queue(db_path, tmp_data_dir):
    init_settings()
    return str(tmp_data_dir)

def _insert_doc(status="pending", **kwargs):
    with get_connection() as conn:
        conn.execute("INSERT INTO documents (original_filename, file_hash, file_size_bytes, status, submission_channel) VALUES (?, ?, ?, ?, 'web_upload')", (kwargs.get("filename", "test.pdf"), kwargs.get("hash", "abc123"), 100, status))
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

def test_get_next_pending_returns_oldest(setup_queue):
    id1 = _insert_doc(hash="hash1")
    id2 = _insert_doc(hash="hash2")
    result = get_next_pending()
    assert result["id"] == id1

def test_get_next_pending_skips_non_pending(setup_queue):
    _insert_doc(status="processed", hash="hash1")
    _insert_doc(status="failed", hash="hash2")
    id3 = _insert_doc(status="pending", hash="hash3")
    result = get_next_pending()
    assert result["id"] == id3

def test_get_next_pending_none(setup_queue):
    assert get_next_pending() is None

def test_reset_stuck_processing(setup_queue):
    doc_id = _insert_doc(status="processing", hash="hash1")
    count = reset_stuck_processing()
    assert count == 1
    with get_connection() as conn:
        doc = conn.execute("SELECT status FROM documents WHERE id = ?", (doc_id,)).fetchone()
    assert doc["status"] == "pending"

def test_queue_status(setup_queue):
    _insert_doc(status="pending", hash="h1")
    _insert_doc(status="pending", hash="h2")
    _insert_doc(status="processing", hash="h3")
    _insert_doc(status="processed", hash="h4")
    _insert_doc(status="failed", hash="h5")
    status = get_queue_status()
    assert status["pending"] == 2
    assert status["processing"] == 1
    assert status["recent_completed"] >= 1
    assert status["recent_failed"] >= 1
