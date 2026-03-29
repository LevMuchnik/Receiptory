import asyncio
import logging
from backend.config import get_setting
from backend.notifications.templates import (
    format_ingested, format_processed, format_failed,
    format_needs_review, format_backup_ok, format_backup_failed,
)

logger = logging.getLogger(__name__)

FORMATTERS = {
    "ingested": format_ingested,
    "processed": format_processed,
    "failed": format_failed,
    "needs_review": format_needs_review,
    "backup_ok": format_backup_ok,
    "backup_failed": format_backup_failed,
}

# Events that include a document photo in Telegram
PHOTO_EVENTS = {"ingested", "processed"}


def notify(event_type: str, payload: dict) -> None:
    """Send notifications for an event. Fire-and-forget, never raises."""
    try:
        formatter = FORMATTERS.get(event_type)
        if not formatter:
            logger.warning(f"Unknown notification event: {event_type}")
            return

        base_url = get_setting("base_url") or ""

        # Format the notification
        if event_type.startswith("backup"):
            content = formatter(payload)
        else:
            content = formatter(payload, base_url)

        # Get thumbnail for photo events
        image_bytes = None
        if event_type in PHOTO_EVENTS and payload.get("id"):
            image_bytes = _get_thumbnail(payload)

        # Check Telegram
        tg_key = f"notify_telegram_{event_type}"
        if get_setting(tg_key):
            _send_telegram(content["caption"], image_bytes if event_type in PHOTO_EVENTS else None)

        # Check Email
        email_key = f"notify_email_{event_type}"
        if get_setting(email_key):
            _send_email(content["subject"], content["html"], image_bytes)

    except Exception as e:
        logger.error(f"Notification dispatch failed for {event_type}: {e}")


def _get_thumbnail(doc: dict) -> bytes | None:
    """Try to render the first page of a document as PNG."""
    try:
        import os
        from backend.storage import render_page, get_file_path

        # Find the PDF path
        file_hash = doc.get("file_hash", "")
        original_filename = doc.get("original_filename", "")
        ext = os.path.splitext(original_filename)[1].lower() or ".pdf"

        # Try to get data_dir from environment
        data_dir = os.environ.get("RECEIPTORY_DATA_DIR", "data")

        converted_path = os.path.join(data_dir, "storage", "converted", f"{file_hash}.pdf")
        if os.path.exists(converted_path):
            pdf_path = converted_path
        else:
            pdf_path = get_file_path("original", file_hash, ext, data_dir)

        if not os.path.exists(pdf_path):
            return None

        return render_page(pdf_path, page_num=0, dpi=150)
    except Exception as e:
        logger.debug(f"Could not render thumbnail for notification: {e}")
        return None


def _send_telegram(caption: str, image_bytes: bytes | None) -> None:
    """Send Telegram notification, handling async from sync context."""
    try:
        from backend.notifications.telegram_notify import send_telegram_notification
        try:
            loop = asyncio.get_running_loop()
            # We're inside a running event loop — schedule as a task
            asyncio.ensure_future(send_telegram_notification(caption, image_bytes))
        except RuntimeError:
            # No running event loop — run synchronously
            asyncio.run(send_telegram_notification(caption, image_bytes))
    except Exception as e:
        logger.error(f"Telegram notification error: {e}")


def _send_email(subject: str, html_body: str, image_bytes: bytes | None) -> None:
    """Send email notification."""
    try:
        from backend.notifications.email_notify import send_email_notification
        send_email_notification(subject, html_body, image_bytes)
    except Exception as e:
        logger.error(f"Email notification error: {e}")
