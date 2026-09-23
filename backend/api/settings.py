import os

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.auth import require_auth
from backend.config import (
    get_all_settings_masked, set_setting, get_setting, env_overridden_keys,
    resolve_llm_api_key, list_llm_api_keys, mutate_llm_api_keys, provider_of,
)
from backend.models import SettingsUpdate, ApiKeyCreate, SelectedKeyUpdate

router = APIRouter()


@router.get("/settings")
def get_settings(username: str = Depends(require_auth)):
    return get_all_settings_masked()


def _api_keys_payload():
    """Shared response for the key-manager: masked key list, selection, model
    provider, and whether the legacy env fallback is present. Never returns key
    material — only name + last4 (issue #25)."""
    model = get_setting("llm_model")
    ref = get_setting("llm_api_key_ref")
    keys = [
        {"name": e.get("name", ""), "last4": (e.get("key") or "")[-4:]}
        for e in list_llm_api_keys()
        if isinstance(e, dict)
    ]
    return {
        "keys": keys,
        "selected": ref if isinstance(ref, str) else "",
        "legacy_env_key_set": bool(os.environ.get("RECEIPTORY_LLM_API_KEY")),
        "model": model,
        "model_provider": provider_of(model),
    }


@router.get("/settings/llm-api-keys")
def llm_api_keys(username: str = Depends(require_auth)):
    """The DB-managed LLM API keys for the Settings key manager (issue #25).
    Returns each key's name + last-4 (never the full secret), which one is
    selected, the model, and its provider so the user can pick a matching key."""
    return _api_keys_payload()


@router.post("/settings/llm-api-keys")
def add_llm_api_key(body: ApiKeyCreate, username: str = Depends(require_auth)):
    """Add or replace a named key. Case-insensitive name match, so 'OpenAI'
    updates 'openai' instead of duplicating it (the newest casing wins)."""
    name = body.name.strip()
    key = body.key.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Key name is required")
    if not key:
        raise HTTPException(status_code=400, detail="Key value is required")
    if "/" in name:
        # The name is the DELETE path segment; a slash would make the entry
        # unreachable by that route (undeletable from the UI).
        raise HTTPException(status_code=400, detail="Key name cannot contain '/'")

    def _apply(entries):
        kept = [e for e in entries if isinstance(e, dict)
                and e.get("name", "").lower() != name.lower()]
        kept.append({"name": name, "key": key})
        return kept

    mutate_llm_api_keys(_apply)
    return _api_keys_payload()


@router.delete("/settings/llm-api-keys/{name}")
def delete_llm_api_key(name: str, username: str = Depends(require_auth)):
    """Remove a named key (case-insensitive). If it was the active selection,
    clear the ref so resolution falls back to the legacy env key."""
    def _apply(entries):
        return [e for e in entries if isinstance(e, dict)
                and e.get("name", "").lower() != name.lower()]

    mutate_llm_api_keys(_apply)
    ref = get_setting("llm_api_key_ref")
    if isinstance(ref, str) and ref.lower() == name.lower():
        set_setting("llm_api_key_ref", "")
    return _api_keys_payload()


@router.put("/settings/llm-api-keys/selected")
def set_selected_llm_api_key(body: SelectedKeyUpdate, username: str = Depends(require_auth)):
    """Set the active key by name (empty string clears). Validates membership so
    the ref can't point at a nonexistent entry."""
    name = body.name.strip()
    if name:
        names = {e.get("name", "").lower() for e in list_llm_api_keys() if isinstance(e, dict)}
        if name.lower() not in names:
            raise HTTPException(status_code=400, detail=f"No API key named {name!r}")
    set_setting("llm_api_key_ref", name)
    return _api_keys_payload()


@router.get("/settings/env-overrides")
def settings_env_overrides(username: str = Depends(require_auth)):
    """Keys currently pinned by an env var (env > db precedence). The UI shows
    these fields as read-only so edits aren't silently discarded (#13)."""
    return {"keys": env_overridden_keys()}


@router.patch("/settings")
def patch_settings(body: SettingsUpdate, username: str = Depends(require_auth)):
    from backend.config import DEFAULTS
    for key, value in body.settings.items():
        if key not in DEFAULTS:
            continue  # Reject unknown setting keys
        if key == "auth_password_hash":
            import bcrypt
            value = bcrypt.hashpw(value.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        set_setting(key, value)
    return {"message": "Settings updated"}


# Both calls on the Settings "Test LLM" button. Without one, litellm waits up
# to its 600s default on an endpoint that hangs, holding a worker thread.
_TEST_LLM_TIMEOUT_S = 60


def _redact_key(text: str, api_key: str) -> str:
    """Provider error text with the API key masked. Gemini AI Studio carries the
    key in the request URL (?key=...), and some litellm/httpx errors quote that
    URL; everywhere else this API only ever shows a key's last 4 characters."""
    return text.replace(api_key, "***" + api_key[-4:]) if api_key else text


@router.post("/settings/test-llm")
def test_llm(username: str = Depends(require_auth)):
    """Test LLM connectivity by sending a minimal request, then probe the extraction schema."""
    import litellm

    model = get_setting("llm_model")
    api_key = resolve_llm_api_key()
    if not api_key:
        raise HTTPException(status_code=400, detail="No API key configured")

    try:
        response = litellm.completion(
            model=model,
            api_key=api_key,
            messages=[{"role": "user", "content": "Reply with exactly: Hello from <your model name>"}],
            max_tokens=50,
            timeout=_TEST_LLM_TIMEOUT_S,
        )
        reply = response.choices[0].message.content
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM test failed: {_redact_key(str(e), api_key)}")
    return {
        "status": "ok",
        "model": model,
        "response": reply,
        **_probe_extraction_schema(model, api_key),
    }


def _probe_extraction_schema(model: str, api_key: str) -> dict[str, str]:
    """Whether `model` accepts the request extraction will send it (issue #67).

    `schema` is one of: "supported"; "unsupported" (litellm does not list the
    model as schema-capable, so extraction uses JSON mode); "off"
    (llm_json_mode is off, so extraction requests no structured output at
    all); "rejected" (the provider refused the schema, so extraction falls back
    to JSON mode); "error" (the check itself failed for another reason).

    Sends the exact response_format extraction sends (the schema with the
    current category enum, from the helper extraction itself uses) with a
    minimal prompt: the documented way a schema fails is a large category enum,
    which a toy schema would not reproduce. Providers
    validate the schema before generating, so a tiny max_tokens still surfaces
    a rejection. reasoning_effort is deliberately NOT sent: a thinking budget
    larger than this probe's output cap would 400 on its own and be misread as
    a rejected schema. Never raises: a schema problem is not fatal (extraction
    falls back), and connectivity already succeeded. Without this probe, the
    fallback shows up only as an ERROR line in the container log.
    """
    import litellm
    from backend.processing.extract import extraction_format_kwargs, is_schema_rejection
    from backend.processing.pipeline import extraction_categories

    try:
        if not get_setting("llm_json_mode"):
            return {"schema": "off", "schema_detail": "JSON mode is off: extraction requests no structured output."}
        kwargs = extraction_format_kwargs(model, *extraction_categories())
        if kwargs["response_format"]["type"] != "json_schema":
            return {"schema": "unsupported", "schema_detail": "Structured output is not listed for this model; extraction uses JSON mode."}
        litellm.completion(
            model=model,
            api_key=api_key,
            messages=[{"role": "user", "content": "Reply with an empty extraction: every field null or empty."}],
            max_tokens=50,
            timeout=_TEST_LLM_TIMEOUT_S,
            **kwargs,
        )
    except Exception as e:
        detail = _redact_key(str(e), api_key)
        if is_schema_rejection(e):
            return {"schema": "rejected", "schema_detail": f"The model rejected the extraction schema; extraction falls back to JSON mode. {detail}"}
        return {"schema": "error", "schema_detail": f"Could not check structured output: {detail}"}
    return {"schema": "supported", "schema_detail": "Structured output accepted."}


@router.get("/settings/model-info")
def model_info(model: str | None = None, username: str = Depends(require_auth)):
    """Economics + reasoning support for a single model, from litellm's registry
    (issue #13). Drives the Settings LLM-Engine price line and the reasoning
    control's enabled/disabled state. `model` defaults to the configured
    llm_model. Prices are per 1M tokens for display; `in_registry` is false for
    self-hosted / brand-new ids litellm can't resolve (the free-text case)."""
    import litellm

    model = model or get_setting("llm_model")
    result = {
        "model": model,
        "in_registry": False,
        "provider": None,
        "supports_reasoning": False,
        "input_price_per_1m": None,
        "output_price_per_1m": None,
        "max_output_tokens": None,
    }
    if not model:
        return result

    # get_model_info does proper provider resolution and raises for a model it
    # can't map — safer than model_cost.get + prefix strip, which can land on a
    # different-priced registry row (e.g. azure/deepseek-v4-pro -> deepseek-v4-pro).
    try:
        info = litellm.get_model_info(model)
    except Exception:
        info = None
    if info:
        result["in_registry"] = True
        result["provider"] = info.get("litellm_provider")
        result["max_output_tokens"] = info.get("max_output_tokens")
        in_rate = info.get("input_cost_per_token")
        out_rate = info.get("output_cost_per_token")
        if in_rate is not None:
            result["input_price_per_1m"] = round(in_rate * 1_000_000, 4)
        if out_rate is not None:
            result["output_price_per_1m"] = round(out_rate * 1_000_000, 4)

    # supports_reasoning resolves even for some ids missing a model_cost row, so
    # probe it independently of the price lookup.
    try:
        result["supports_reasoning"] = bool(litellm.supports_reasoning(model=model))
    except Exception:
        result["supports_reasoning"] = False

    return result


_llm_models_cache: list[dict] | None = None


def _build_llm_models() -> list[dict]:
    """Shape litellm's registry into the vision-capable chat models the picker
    offers (issue #13, PR3). Receipts are page images, so a usable extraction
    model must support vision; mode=="chat" drops embedding/rerank/image-gen
    rows. Cached process-wide — the registry is a static JSON bundled with the
    installed litellm, so it never changes at runtime."""
    import litellm

    models = []
    for mid, info in litellm.model_cost.items():
        if not isinstance(info, dict):
            continue
        if info.get("mode") != "chat" or not info.get("supports_vision"):
            continue
        in_rate = info.get("input_cost_per_token")
        out_rate = info.get("output_cost_per_token")
        models.append({
            "id": mid,
            "provider": info.get("litellm_provider"),
            "input_price_per_1m": round(in_rate * 1_000_000, 4) if in_rate is not None else None,
            "output_price_per_1m": round(out_rate * 1_000_000, 4) if out_rate is not None else None,
            "supports_reasoning": bool(info.get("supports_reasoning")),
            "max_output_tokens": info.get("max_output_tokens"),
        })
    models.sort(key=lambda m: m["id"])
    return models


@router.get("/settings/llm-models")
def llm_models(username: str = Depends(require_auth)):
    """The vision-capable chat models the model picker filters over, with prices
    (issue #13, PR3). Cached in memory after the first call."""
    global _llm_models_cache
    if _llm_models_cache is None:
        _llm_models_cache = _build_llm_models()
    return {"models": _llm_models_cache}


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
