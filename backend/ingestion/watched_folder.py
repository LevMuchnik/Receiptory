import asyncio
import logging
import os
import shutil

from backend.config import get_setting
from backend.database import get_connection
from backend.storage import compute_file_hash, save_original

logger = logging.getLogger(__name__)


def _ingest_file(file_path: str, data_dir: str) -> dict:
    """Ingest a single file from the watched folder."""
    filename = os.path.basename(file_path)
    ext = os.path.splitext(filename)[1].lower() or ".bin"

    file_hash = compute_file_hash(file_path)
    file_size = os.path.getsize(file_path)

    # Check duplicate
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM documents WHERE file_hash = ?", (file_hash,)
        ).fetchone()

    if existing:
        return {"filename": filename, "status": "duplicate", "existing_id": existing["id"]}

    # Save original
    save_original(file_path, file_hash, ext, data_dir)

    # Create document record
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO documents
               (original_filename, file_hash, file_size_bytes, status, submission_channel)
               VALUES (?, ?, ?, 'pending', 'watched_folder')""",
            (filename, file_hash, file_size),
        )
        doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    logger.info(f"Watched folder: ingested {filename} as document #{doc_id}")
    return {"filename": filename, "status": "ingested", "doc_id": doc_id}


def poll_folder(data_dir: str) -> list[dict]:
    """Check the watched folder for new files and ingest them."""
    folder_path = get_setting("watched_folder_path")
    if not folder_path or not os.path.isdir(folder_path):
        return []

    processed_dir = os.path.join(folder_path, "processed")
    os.makedirs(processed_dir, exist_ok=True)

    results = []
    for entry in os.listdir(folder_path):
        if entry == "processed":
            continue
        file_path = os.path.join(folder_path, entry)
        if not os.path.isfile(file_path):
            continue

        try:
            result = _ingest_file(file_path, data_dir)
            results.append(result)
            # Move to processed subfolder
            dest = os.path.join(processed_dir, entry)
            if os.path.exists(dest):
                # Avoid collision: append hash
                base, ext = os.path.splitext(entry)
                dest = os.path.join(processed_dir, f"{base}_{os.urandom(4).hex()}{ext}")
            shutil.move(file_path, dest)
        except Exception as e:
            logger.error(f"Watched folder: failed to ingest {entry}: {e}")
            results.append({"filename": entry, "status": "error", "error": str(e)})

    return results


async def run_watched_folder(data_dir: str) -> None:
    """Background loop that polls the watched folder."""
    logger.info("Watched folder poller started")

    while True:
        try:
            folder_path = get_setting("watched_folder_path")
            if not folder_path:
                await asyncio.sleep(30)
                continue

            poll_interval = get_setting("watched_folder_poll_interval")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, poll_folder, data_dir)
            await asyncio.sleep(poll_interval)

        except asyncio.CancelledError:
            logger.info("Watched folder poller shutting down")
            break
        except Exception as e:
            logger.error(f"Watched folder poller error: {e}")
            await asyncio.sleep(30)
