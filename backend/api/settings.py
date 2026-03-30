from fastapi import APIRouter, Depends, HTTPException, Request

from backend.auth import require_auth
from backend.config import get_all_settings_masked, set_setting, get_setting
from backend.models import SettingsUpdate

router = APIRouter()


@router.get("/settings")
def get_settings(username: str = Depends(require_auth)):
    return get_all_settings_masked()


@router.patch("/settings")
def patch_settings(body: SettingsUpdate, username: str = Depends(require_auth)):
    for key, value in body.settings.items():
        if key == "auth_password_hash":
            # Password changes should be handled specially
            import bcrypt
            value = bcrypt.hashpw(value.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        set_setting(key, value)
    return {"message": "Settings updated"}


@router.post("/settings/test-llm")
def test_llm(username: str = Depends(require_auth)):
    """Test LLM connectivity by sending a minimal request."""
    import litellm

    model = get_setting("llm_model")
    api_key = get_setting("llm_api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="No API key configured")

    try:
        response = litellm.completion(
            model=model,
            api_key=api_key,
            messages=[{"role": "user", "content": "Reply with exactly: Hello from <your model name>"}],
            max_tokens=50,
        )
        return {
            "status": "ok",
            "model": model,
            "response": response.choices[0].message.content,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM test failed: {e}")


@router.get("/settings/telegram-status")
async def telegram_status(username: str = Depends(require_auth)):
    """Check Telegram bot connection status."""
    from backend.ingestion.telegram import _app

    token = get_setting("telegram_bot_token")
    if not token:
        return {"status": "not_configured", "message": "No bot token set"}

    if _app is None:
        return {"status": "stopped", "message": "Bot not running. Restart the server after setting the token."}

    try:
        bot_info = await _app.bot.get_me()
        return {
            "status": "running",
            "bot_username": f"@{bot_info.username}",
            "bot_name": bot_info.first_name,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/settings/gmail-status")
def gmail_status(username: str = Depends(require_auth)):
    """Check Gmail IMAP connection status."""
    from backend.ingestion.gmail import test_connection
    return test_connection()


@router.post("/settings/gmail-poll-now")
def gmail_poll_now(request: Request, username: str = Depends(require_auth)):
    """Trigger an immediate Gmail poll."""
    from backend.ingestion.gmail import poll_gmail
    data_dir = request.app.state.data_dir
    results = poll_gmail(data_dir)
    return {"polled": len(results), "results": results}


@router.post("/settings/test-notification")
def test_notification(username: str = Depends(require_auth)):
    """Send a test notification via ALL channels, ignoring toggle settings."""
    from backend.notifications.notifier import _send_telegram, _send_email
    from backend.notifications.templates import format_processed

    payload = {
        "id": 0,
        "original_filename": "test_notification.pdf",
        "vendor_name": "Test Vendor",
        "receipt_date": "2026-01-01",
        "total_amount": 42.00,
        "currency": "ILS",
        "category_name": "test",
        "extraction_confidence": 0.99,
        "submission_channel": "web_upload",
        "sender_identifier": None,
    }
    base_url = get_setting("base_url") or ""
    content = format_processed(payload, base_url)

    results = {}

    # Always try Telegram
    try:
        _send_telegram(content["caption"], None)
        results["telegram"] = "sent"
    except Exception as e:
        results["telegram"] = f"failed: {e}"

    # Always try Email
    try:
        _send_email(content["subject"], content["html"], None)
        results["email"] = "sent"
    except Exception as e:
        results["email"] = f"failed: {e}"

    return {"message": "Test notification sent", "results": results}
