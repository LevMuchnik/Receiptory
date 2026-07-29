import logging
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from backend.database import get_connection
from backend.storage import compute_file_hash, save_original

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestionResult:
    status: Literal["accepted", "duplicate"]
    document_id: int
    file_hash: str


def ingest_local_file(
    file_path: str,
    *,
    filename: str,
    data_dir: str,
    submission_channel: str,
    sender_identifier: str | None = None,
    source_url: str | None = None,
    status: str = "pending",
    user_notes: str | None = None,
) -> IngestionResult:
    """Persist one local file as a document, idempotently by SHA-256."""
    file_hash = compute_file_hash(file_path)
    file_size = os.path.getsize(file_path)
    safe_filename = os.path.basename(filename) or "document"
    extension = Path(safe_filename).suffix.lower() or ".bin"

    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM documents WHERE file_hash = ?", (file_hash,)
        ).fetchone()
    if existing:
        return IngestionResult("duplicate", existing["id"], file_hash)

    save_original(file_path, file_hash, extension, data_dir)

    try:
        with get_connection() as conn:
            conn.execute(
                """INSERT INTO documents
                   (original_filename, file_hash, file_size_bytes, status,
                    submission_channel, sender_identifier, source_url, user_notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    safe_filename,
                    file_hash,
                    file_size,
                    status,
                    submission_channel,
                    sender_identifier,
                    source_url,
                    user_notes,
                ),
            )
            document_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    except sqlite3.IntegrityError:
        # A concurrent source may have inserted the same content after our
        # initial lookup. The content-addressed original is safe to share.
        with get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM documents WHERE file_hash = ?", (file_hash,)
            ).fetchone()
        if existing:
            return IngestionResult("duplicate", existing["id"], file_hash)
        raise

    try:
        from backend.notifications.notifier import notify

        notify(
            "ingested",
            {
                "id": document_id,
                "original_filename": safe_filename,
                "file_hash": file_hash,
                "submission_channel": submission_channel,
                "sender_identifier": sender_identifier,
                "source_url": source_url,
            },
        )
    except Exception:
        logger.exception("Ingestion notification failed for document #%s", document_id)

    return IngestionResult("accepted", document_id, file_hash)
