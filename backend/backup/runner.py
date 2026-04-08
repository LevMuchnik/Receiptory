import json
import os
import shutil
import logging
import tempfile
from datetime import datetime, timezone

from backend.database import get_connection
from backend.config import get_all_settings

logger = logging.getLogger(__name__)


def build_backup(data_dir: str) -> str:
    """Assemble backup contents into a temporary directory. Returns path."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = os.path.join(tempfile.gettempdir(), f"receiptory_backup_{timestamp}")
    os.makedirs(backup_dir, exist_ok=True)

    # Copy SQLite database
    db_path = os.path.join(data_dir, "receiptory.db")
    if os.path.exists(db_path):
        shutil.copy2(db_path, os.path.join(backup_dir, "receiptory.db"))

    # Copy storage files
    storage_dir = os.path.join(data_dir, "storage")
    if os.path.exists(storage_dir):
        shutil.copytree(storage_dir, os.path.join(backup_dir, "storage"), dirs_exist_ok=True)

    # Copy logs
    logs_dir = os.path.join(data_dir, "logs")
    if os.path.exists(logs_dir):
        shutil.copytree(logs_dir, os.path.join(backup_dir, "logs"), dirs_exist_ok=True)

    # Export JSONL metadata
    _export_jsonl(os.path.join(backup_dir, "metadata.jsonl"))

    # Export settings (with sensitive values masked)
    from backend.config import get_all_settings_masked
    settings = get_all_settings_masked()
    with open(os.path.join(backup_dir, "settings.json"), "w") as f:
        json.dump(settings, f, indent=2, default=str)

    logger.info(f"Backup assembled at {backup_dir}")
    return backup_dir


def _export_jsonl(output_path: str) -> None:
    """Export all document metadata as JSONL."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT d.*, c.name as category_name, c.section as category_section
               FROM documents d
               LEFT JOIN categories c ON d.category_id = c.id"""
        ).fetchall()

    with open(output_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(dict(row), default=str, ensure_ascii=False) + "\n")
