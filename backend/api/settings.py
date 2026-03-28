from fastapi import APIRouter, Depends, HTTPException

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
