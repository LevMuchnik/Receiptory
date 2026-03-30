"""OAuth flow for Google Drive and OneDrive, creating rclone remotes."""

import json
import logging
import os
import subprocess
import time

import httpx

from backend.config import get_setting, set_setting

logger = logging.getLogger(__name__)

PROVIDERS = {
    "gdrive": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://www.googleapis.com/oauth2/v2/userinfo",
        "scopes": "https://www.googleapis.com/auth/drive.file",
        "rclone_type": "drive",
        "rclone_scope": "drive.file",
        "extra_auth_params": {"access_type": "offline", "prompt": "consent"},
        "setting_prefix": "gdrive",
    },
    "onedrive": {
        "auth_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "userinfo_url": "https://graph.microsoft.com/v1.0/me",
        "scopes": "Files.ReadWrite.All offline_access User.Read",
        "rclone_type": "onedrive",
        "rclone_scope": "",
        "extra_auth_params": {},
        "setting_prefix": "onedrive",
    },
}


def rclone_config_path() -> str:
    data_dir = os.environ.get("RECEIPTORY_DATA_DIR", "/app/data")
    return os.path.join(data_dir, "rclone.conf")


def _rclone_env() -> dict:
    return {**os.environ, "RCLONE_CONFIG": rclone_config_path()}


def get_callback_url(provider: str) -> str:
    base = get_setting("base_url") or ""
    base = base.rstrip("/")
    return f"{base}/api/cloud-auth/callback/{provider}"


def get_authorize_url(provider: str) -> str:
    cfg = PROVIDERS[provider]
    prefix = cfg["setting_prefix"]
    client_id = get_setting(f"{prefix}_client_id")
    if not client_id:
        raise ValueError(f"No client ID configured for {provider}")

    # Generate state token
    state = f"{provider}:{int(time.time())}"
    set_setting("cloud_auth_state", state)

    params = {
        "client_id": client_id,
        "redirect_uri": get_callback_url(provider),
        "response_type": "code",
        "scope": cfg["scopes"],
        "state": state,
        **cfg["extra_auth_params"],
    }
    from urllib.parse import urlencode
    return f"{cfg['auth_url']}?{urlencode(params)}"


def exchange_code(provider: str, code: str) -> dict:
    cfg = PROVIDERS[provider]
    prefix = cfg["setting_prefix"]
    client_id = get_setting(f"{prefix}_client_id")
    client_secret = get_setting(f"{prefix}_client_secret")

    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": get_callback_url(provider),
        "grant_type": "authorization_code",
    }

    resp = httpx.post(cfg["token_url"], data=data, timeout=30)
    if resp.status_code != 200:
        logger.error(f"Token exchange failed: {resp.text}")
        raise RuntimeError(f"Token exchange failed: {resp.text}")

    token_data = resp.json()
    # Build rclone-format token
    rclone_token = {
        "access_token": token_data["access_token"],
        "token_type": token_data.get("token_type", "Bearer"),
        "refresh_token": token_data.get("refresh_token", ""),
        "expiry": "",
    }
    if "expires_in" in token_data:
        from datetime import datetime, timezone, timedelta
        expiry = datetime.now(timezone.utc) + timedelta(seconds=token_data["expires_in"])
        rclone_token["expiry"] = expiry.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")

    return rclone_token


def get_user_email(provider: str, access_token: str) -> str:
    cfg = PROVIDERS[provider]
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        resp = httpx.get(cfg["userinfo_url"], headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("email") or data.get("mail") or data.get("userPrincipalName", "")
    except Exception as e:
        logger.warning(f"Failed to get user email for {provider}: {e}")
    return ""


def get_onedrive_drive_id(access_token: str) -> str:
    """Fetch the user's default OneDrive drive ID via Microsoft Graph API."""
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = httpx.get("https://graph.microsoft.com/v1.0/me/drive", headers=headers, timeout=15)
    if resp.status_code != 200:
        logger.error(f"Failed to get OneDrive drive ID: {resp.text}")
        raise RuntimeError(f"Failed to get OneDrive drive ID: {resp.text}")
    data = resp.json()
    return data["id"]


def create_rclone_remote(provider: str, token: dict, drive_id: str = "") -> None:
    """Write rclone config directly to avoid rclone's interactive OAuth flow."""
    import configparser

    cfg = PROVIDERS[provider]
    prefix = cfg["setting_prefix"]
    remote_name = f"receiptory_{provider}"
    client_id = get_setting(f"{prefix}_client_id")
    client_secret = get_setting(f"{prefix}_client_secret")
    token_json = json.dumps(token)

    conf_path = rclone_config_path()
    config = configparser.ConfigParser()
    if os.path.exists(conf_path):
        config.read(conf_path)

    # Remove existing section
    if remote_name in config:
        config.remove_section(remote_name)

    config.add_section(remote_name)

    if provider == "gdrive":
        config.set(remote_name, "type", "drive")
        config.set(remote_name, "client_id", client_id)
        config.set(remote_name, "client_secret", client_secret)
        config.set(remote_name, "scope", cfg["rclone_scope"])
        config.set(remote_name, "token", token_json)
    elif provider == "onedrive":
        config.set(remote_name, "type", "onedrive")
        config.set(remote_name, "client_id", client_id)
        config.set(remote_name, "client_secret", client_secret)
        config.set(remote_name, "token", token_json)
        config.set(remote_name, "drive_type", "personal")
        if drive_id:
            config.set(remote_name, "drive_id", drive_id)
    else:
        raise ValueError(f"Unknown provider: {provider}")

    os.makedirs(os.path.dirname(conf_path), exist_ok=True)
    with open(conf_path, "w") as f:
        config.write(f)

    logger.info(f"Created rclone remote: {remote_name}")


def remove_rclone_remote(provider: str) -> None:
    import configparser
    remote_name = f"receiptory_{provider}"
    conf_path = rclone_config_path()
    if os.path.exists(conf_path):
        config = configparser.ConfigParser()
        config.read(conf_path)
        if remote_name in config:
            config.remove_section(remote_name)
            with open(conf_path, "w") as f:
                config.write(f)
    prefix = PROVIDERS[provider]["setting_prefix"]
    set_setting(f"cloud_auth_{prefix}_token", "")
    set_setting(f"cloud_auth_{prefix}_email", "")
    set_setting(f"cloud_auth_{prefix}_folder", "")
    set_setting(f"cloud_auth_{prefix}_drive_id", "")

    logger.info(f"Removed rclone remote: {remote_name}")


def test_remote(provider: str) -> dict:
    remote_name = f"receiptory_{provider}"
    result = subprocess.run(
        ["rclone", "about", f"{remote_name}:"],
        env=_rclone_env(), capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        return {"status": "error", "message": result.stderr.strip()}
    return {"status": "ok", "message": "Connected successfully"}


def list_folders(provider: str, path: str = "") -> list[str]:
    remote_name = f"receiptory_{provider}"
    remote_path = f"{remote_name}:{path}"
    result = subprocess.run(
        ["rclone", "lsf", remote_path, "--dirs-only"],
        env=_rclone_env(), capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        return []
    return [d.rstrip("/") for d in result.stdout.strip().split("\n") if d.strip()]


def restore_rclone_config() -> None:
    """Regenerate rclone.conf from stored tokens on startup."""
    for provider, cfg in PROVIDERS.items():
        prefix = cfg["setting_prefix"]
        token_str = get_setting(f"cloud_auth_{prefix}_token")
        if token_str:
            try:
                token = json.loads(token_str) if isinstance(token_str, str) else token_str
                drive_id = get_setting(f"cloud_auth_{prefix}_drive_id") or ""
                create_rclone_remote(provider, token, drive_id=drive_id)
                logger.info(f"Restored rclone remote for {provider}")
            except Exception as e:
                logger.warning(f"Failed to restore rclone remote for {provider}: {e}")


def sync_token_from_rclone(provider: str) -> None:
    """Read token back from rclone.conf and save to DB (after rclone refreshes it)."""
    import configparser
    conf_path = rclone_config_path()
    if not os.path.exists(conf_path):
        return
    config = configparser.ConfigParser()
    config.read(conf_path)
    remote_name = f"receiptory_{provider}"
    if remote_name not in config:
        return
    section = config[remote_name]
    token_str = section.get("token", "")
    if token_str:
        prefix = PROVIDERS[provider]["setting_prefix"]
        set_setting(f"cloud_auth_{prefix}_token", token_str)
