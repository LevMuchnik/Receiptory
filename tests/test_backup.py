import json
import os
import pytest
from backend.backup.runner import build_backup
from backend.backup.scheduler import determine_backup_type
from backend.database import get_connection
from backend.config import init_settings
from datetime import date


def test_determine_backup_type_daily():
    # 2026-03-10 is a Tuesday (not Sunday, not 1st)
    assert determine_backup_type(date(2026, 3, 10)) == "daily"


def test_determine_backup_type_weekly():
    # 2026-03-22 is a Sunday
    assert determine_backup_type(date(2026, 3, 22)) == "weekly"


def test_determine_backup_type_monthly():
    assert determine_backup_type(date(2026, 3, 1)) == "monthly"


def test_determine_backup_type_quarterly():
    assert determine_backup_type(date(2026, 4, 1)) == "quarterly"


def test_build_backup_creates_archive(db_path, tmp_data_dir):
    """build_backup produces a directory with DB copy, JSONL, and settings."""
    init_settings()
    data_dir = str(tmp_data_dir)

    # Insert a document
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO documents (original_filename, file_hash, file_size_bytes, status, submission_channel, vendor_name)
               VALUES ('test.pdf', 'h1', 100, 'processed', 'web_upload', 'Test Vendor')"""
        )

    backup_dir = build_backup(data_dir)
    assert os.path.exists(os.path.join(backup_dir, "receiptory.db"))
    assert os.path.exists(os.path.join(backup_dir, "metadata.jsonl"))
    assert os.path.exists(os.path.join(backup_dir, "settings.json"))

    # Check JSONL content
    with open(os.path.join(backup_dir, "metadata.jsonl")) as f:
        lines = f.readlines()
    assert len(lines) == 1
    doc = json.loads(lines[0])
    assert doc["vendor_name"] == "Test Vendor"
