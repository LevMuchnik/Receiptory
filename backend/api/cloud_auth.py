"""API endpoints for cloud storage OAuth (Google Drive, OneDrive)."""

import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from backend.auth import require_auth
from backend.config import get_setting, set_setting
from backend.backup.cloud_auth import (
    PROVIDERS,
    get_authorize_url,
    exchange_code,
    get_user_email,
    create_rclone_remote,
    remove_rclone_remote,
    test_remote,
    list_folders,
    sync_token_from_rclone,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _update_backup_destinations() -> None:
    """Build backup_destination from all connected cloud providers."""
    destinations = []
    for provider, cfg in PROVIDERS.items():
        prefix = cfg["setting_prefix"]
        token = get_setting(f"cloud_auth_{prefix}_token")
        if token:
            folder = (get_setting(f"cloud_auth_{prefix}_folder") or "Receiptory").replace("\\", "/")
            destinations.append(f"receiptory_{provider}:{folder}")
    # Join multiple destinations with comma
    set_setting("backup_destination", ",".join(destinations))


@router.get("/cloud-auth/providers")
def get_providers(username: str = Depends(require_auth)):
    """Return connection status for each cloud provider."""
    result = {}
    for provider, cfg in PROVIDERS.items():
        prefix = cfg["setting_prefix"]
        token = get_setting(f"cloud_auth_{prefix}_token")
        result[provider] = {
            "connected": bool(token),
            "email": get_setting(f"cloud_auth_{prefix}_email") or None,
            "folder": get_setting(f"cloud_auth_{prefix}_folder") or None,
            "client_id_set": bool(get_setting(f"{prefix}_client_id")),
        }
    return result


@router.post("/cloud-auth/{provider}/start")
def start_auth(provider: str, request: Request, username: str = Depends(require_auth)):
    """Generate OAuth authorization URL."""
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    # Auto-set base_url from request if not configured
    base_url = get_setting("base_url")
    if not base_url:
        origin = f"{request.url.scheme}://{request.url.netloc}"
        set_setting("base_url", origin)

    try:
        url = get_authorize_url(provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"authorize_url": url}


@router.get("/cloud-auth/callback/{provider}")
def oauth_callback(provider: str, code: str = "", state: str = "", error: str = ""):
    """Handle OAuth redirect from Google/Microsoft."""
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    if error:
        logger.error(f"OAuth error for {provider}: {error}")
        return RedirectResponse(url=f"/settings?tab=backup&cloud_auth=error&message={error}")

    # Validate state
    stored_state = get_setting("cloud_auth_state")
    if not stored_state or state != stored_state:
        logger.error(f"OAuth state mismatch for {provider}")
        return RedirectResponse(url="/settings?tab=backup&cloud_auth=error&message=state_mismatch")

    # Clear state
    set_setting("cloud_auth_state", "")

    try:
        # Exchange code for tokens
        token = exchange_code(provider, code)

        # Get user info
        email = get_user_email(provider, token["access_token"])

        # For OneDrive, fetch drive_id via Graph API
        drive_id = ""
        if provider == "onedrive":
            from backend.backup.cloud_auth import get_onedrive_drive_id
            drive_id = get_onedrive_drive_id(token["access_token"])

        # Create rclone remote
        create_rclone_remote(provider, token, drive_id=drive_id)

        # Store token and metadata
        prefix = PROVIDERS[provider]["setting_prefix"]
        set_setting(f"cloud_auth_{prefix}_token", token)
        set_setting(f"cloud_auth_{prefix}_email", email)
        if drive_id:
            set_setting(f"cloud_auth_{prefix}_drive_id", drive_id)

        # Set default folder if not already set
        folder = (get_setting(f"cloud_auth_{prefix}_folder") or "Receiptory").replace("\\", "/")
        set_setting(f"cloud_auth_{prefix}_folder", folder)

        # Update backup destination to include all connected providers
        _update_backup_destinations()

        # Create the folder on the remote
        remote_name = f"receiptory_{provider}"
        import subprocess
        from backend.backup.cloud_auth import _rclone_env
        subprocess.run(
            ["rclone", "mkdir", f"{remote_name}:{folder}"],
            env=_rclone_env(), capture_output=True, timeout=30,
        )

        logger.info(f"OAuth complete for {provider}, email={email}")
        return RedirectResponse(url="/settings?tab=backup&cloud_auth=success")

    except Exception as e:
        logger.error(f"OAuth callback failed for {provider}: {e}")
        return RedirectResponse(url=f"/settings?tab=backup&cloud_auth=error&message={e}")


@router.post("/cloud-auth/{provider}/set-folder")
def set_folder(provider: str, body: dict, username: str = Depends(require_auth)):
    """Update the backup folder path on the cloud provider."""
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    folder = body.get("folder", "").strip().replace("\\", "/")
    if not folder:
        raise HTTPException(status_code=400, detail="Folder path is required")

    prefix = PROVIDERS[provider]["setting_prefix"]
    token = get_setting(f"cloud_auth_{prefix}_token")
    if not token:
        raise HTTPException(status_code=400, detail=f"{provider} is not connected")

    set_setting(f"cloud_auth_{prefix}_folder", folder)
    _update_backup_destinations()

    # Create folder if needed
    remote_name = f"receiptory_{provider}"
    import subprocess
    from backend.backup.cloud_auth import _rclone_env
    subprocess.run(
        ["rclone", "mkdir", f"{remote_name}:{folder}"],
        env=_rclone_env(), capture_output=True, timeout=30,
    )

    return {"message": "Folder updated", "destination": get_setting("backup_destination")}


@router.get("/cloud-auth/{provider}/folders")
def browse_folders(
    provider: str,
    path: str = "",
    username: str = Depends(require_auth),
):
    """List folders on the cloud provider for browsing."""
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")
    folders = list_folders(provider, path)
    return {"path": path, "folders": folders}


@router.post("/cloud-auth/{provider}/disconnect")
def disconnect(provider: str, username: str = Depends(require_auth)):
    """Remove cloud provider connection."""
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")
    remove_rclone_remote(provider)
    _update_backup_destinations()
    return {"message": f"{provider} disconnected"}


@router.post("/cloud-auth/{provider}/test")
def test_connection(provider: str, username: str = Depends(require_auth)):
    """Test cloud storage connection."""
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")
    result = test_remote(provider)
    if result["status"] != "ok":
        raise HTTPException(status_code=500, detail=result["message"])
    return result
