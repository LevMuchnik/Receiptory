import asyncio
import logging

from backend.database import get_connection
from backend.config import get_setting
from backend.processing.pipeline import process_document

logger = logging.getLogger(__name__)


def get_next_pending():
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM documents WHERE status = 'pending' ORDER BY created_at ASC LIMIT 1").fetchone()
    return row


def set_status(doc_id: int, status: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE documents SET status = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?", (status, doc_id))


def reset_stuck_processing() -> int:
    with get_connection() as conn:
        conn.execute("UPDATE documents SET status = 'pending' WHERE status = 'processing'")
        count = conn.execute("SELECT changes()").fetchone()[0]
    if count > 0:
        logger.warning(f"Reset {count} stuck document(s) from 'processing' to 'pending'")
    return count


def get_queue_status() -> dict:
    with get_connection() as conn:
        pending = conn.execute("SELECT COUNT(*) as c FROM documents WHERE status = 'pending'").fetchone()["c"]
        processing = conn.execute("SELECT COUNT(*) as c FROM documents WHERE status = 'processing'").fetchone()["c"]
        recent_completed = conn.execute("SELECT COUNT(*) as c FROM documents WHERE status = 'processed' AND updated_at > datetime('now', '-1 hour')").fetchone()["c"]
        recent_failed = conn.execute("SELECT COUNT(*) as c FROM documents WHERE status = 'failed' AND updated_at > datetime('now', '-1 hour')").fetchone()["c"]
        current = conn.execute("SELECT id, original_filename FROM documents WHERE status = 'processing' LIMIT 1").fetchone()
    return {"pending": pending, "processing": processing, "recent_completed": recent_completed, "recent_failed": recent_failed, "current_document": dict(current) if current else None}


async def run_queue_loop(data_dir: str) -> None:
    logger.info("Processing queue started")
    reset_stuck_processing()
    while True:
        try:
            doc = get_next_pending()
            if doc is None:
                await asyncio.sleep(2.0)
                continue
            api_key = get_setting("llm_api_key")
            if not api_key:
                logger.warning("No LLM API key configured, skipping processing")
                await asyncio.sleep(10.0)
                continue
            doc_id = doc["id"]
            logger.info(f"Processing document {doc_id}: {doc['original_filename']}")
            set_status(doc_id, "processing")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, process_document, doc_id, data_dir)
            sleep_interval = get_setting("llm_sleep_interval")
            if sleep_interval > 0:
                await asyncio.sleep(sleep_interval)
        except asyncio.CancelledError:
            logger.info("Processing queue shutting down")
            break
        except Exception as e:
            logger.error(f"Queue loop error: {e}")
            await asyncio.sleep(5.0)
