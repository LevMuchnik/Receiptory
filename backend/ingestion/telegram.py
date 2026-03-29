import os
import tempfile
import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from backend.config import get_setting
from backend.database import get_connection
from backend.storage import compute_file_hash, save_original

logger = logging.getLogger(__name__)

_app: Application | None = None


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    user = update.effective_user
    if not _is_authorized(user.id):
        await update.message.reply_text("You are not authorized to use this bot.")
        logger.warning(f"Unauthorized /start from user {user.id} ({user.username})")
        return
    await update.message.reply_text(
        "Welcome to Receiptory! Send me photos or documents (PDF, images) and I'll process them."
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle received documents (PDF, etc.)."""
    user = update.effective_user
    if not _is_authorized(user.id):
        await update.message.reply_text("You are not authorized to use this bot.")
        logger.warning(f"Unauthorized document from user {user.id} ({user.username})")
        return

    document = update.message.document
    if not document:
        await update.message.reply_text("No document found in the message.")
        return

    await _ingest_file(update, context, document.file_id, document.file_name or "document")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle received photos (picks the largest resolution)."""
    user = update.effective_user
    if not _is_authorized(user.id):
        await update.message.reply_text("You are not authorized to use this bot.")
        logger.warning(f"Unauthorized photo from user {user.id} ({user.username})")
        return

    photo = update.message.photo[-1]  # Largest resolution
    await _ingest_file(update, context, photo.file_id, f"photo_{photo.file_unique_id}.jpg")


async def _ingest_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    file_id: str,
    filename: str,
) -> None:
    """Download a file from Telegram and create a pending document."""
    data_dir = context.bot_data.get("data_dir", "data")
    user = update.effective_user
    sender = f"telegram:{user.id}"
    if user.username:
        sender = f"telegram:@{user.username}"

    try:
        tg_file = await context.bot.get_file(file_id)

        ext = os.path.splitext(filename)[1].lower() or ".bin"
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            await tg_file.download_to_drive(tmp.name)
            tmp_path = tmp.name

        file_hash = compute_file_hash(tmp_path)
        file_size = os.path.getsize(tmp_path)

        # Check duplicate
        with get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM documents WHERE file_hash = ?", (file_hash,)
            ).fetchone()

        if existing:
            await update.message.reply_text(
                f"Duplicate file — already exists as document #{existing['id']}."
            )
            os.unlink(tmp_path)
            return

        # Save original
        save_original(tmp_path, file_hash, ext, data_dir)

        # Create document record
        with get_connection() as conn:
            conn.execute(
                """INSERT INTO documents
                   (original_filename, file_hash, file_size_bytes, status, submission_channel, sender_identifier)
                   VALUES (?, ?, ?, 'pending', 'telegram', ?)""",
                (filename, file_hash, file_size, sender),
            )
            doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        os.unlink(tmp_path)
        await update.message.reply_text(f"Received! Document #{doc_id} queued for processing.")
        logger.info(f"Telegram: ingested {filename} as document #{doc_id} from {sender}")
        try:
            from backend.notifications.notifier import notify
            notify("ingested", {
                "id": doc_id,
                "original_filename": filename,
                "file_hash": file_hash,
                "submission_channel": "telegram",
                "sender_identifier": sender,
            })
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Telegram ingestion failed: {e}")
        await update.message.reply_text(f"Failed to process file: {e}")


def _is_authorized(user_id: int) -> bool:
    """Check if a Telegram user ID is in the authorized list."""
    authorized = get_setting("telegram_authorized_users")
    if not authorized:
        return True  # If no authorized users configured, allow all
    return str(user_id) in [str(u) for u in authorized]


async def start_telegram_bot(data_dir: str) -> None:
    """Start the Telegram bot (non-blocking, runs via polling)."""
    global _app

    token = get_setting("telegram_bot_token")
    if not token:
        logger.info("Telegram bot: no token configured, skipping")
        return

    logger.info("Starting Telegram bot...")
    _app = Application.builder().token(token).build()
    _app.bot_data["data_dir"] = data_dir

    _app.add_handler(CommandHandler("start", start_command))
    _app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    _app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    await _app.initialize()
    await _app.start()
    await _app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot started")


async def stop_telegram_bot() -> None:
    """Stop the Telegram bot gracefully."""
    global _app
    if _app is None:
        return
    logger.info("Stopping Telegram bot...")
    await _app.updater.stop()
    await _app.stop()
    await _app.shutdown()
    _app = None
    logger.info("Telegram bot stopped")
