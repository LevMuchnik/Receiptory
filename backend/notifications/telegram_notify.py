import logging
from backend.config import get_setting

logger = logging.getLogger(__name__)


async def send_telegram_notification(caption: str, image_bytes: bytes | None = None) -> None:
    """Send a notification via the Telegram bot to all authorized users."""
    from backend.ingestion.telegram import _app
    if _app is None:
        logger.warning("Telegram notification: bot not running")
        return

    authorized = get_setting("telegram_authorized_users")
    if not authorized:
        logger.warning("Telegram notification: no authorized users configured")
        return

    for user_id in authorized:
        try:
            if image_bytes:
                await _app.bot.send_photo(
                    chat_id=int(user_id),
                    photo=image_bytes,
                    caption=caption[:1024],  # Telegram caption limit
                    parse_mode="HTML",
                )
            else:
                await _app.bot.send_message(
                    chat_id=int(user_id),
                    text=caption,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
        except Exception as e:
            logger.error(f"Telegram notification failed for user {user_id}: {e}")
