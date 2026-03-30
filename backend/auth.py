import os
import bcrypt
import logging
from fastapi import Request, Response, HTTPException
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from backend.config import get_setting

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "receiptory_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days
_SECRET_KEY = os.environ.get("RECEIPTORY_SECRET_KEY", "receiptory-default-secret-change-me")


def _get_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_SECRET_KEY)


def verify_password(password: str) -> bool:
    # Support plain-text password via RECEIPTORY_AUTH_PASSWORD env var
    env_password = os.environ.get("RECEIPTORY_AUTH_PASSWORD")
    if env_password:
        return password == env_password
    stored_hash = get_setting("auth_password_hash")
    if not stored_hash:
        return False
    return bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))


def create_session(username: str) -> str:
    s = _get_serializer()
    return s.dumps({"username": username})


def validate_session(token: str) -> str | None:
    s = _get_serializer()
    try:
        data = s.loads(token, max_age=SESSION_MAX_AGE)
        return data.get("username")
    except (BadSignature, SignatureExpired):
        return None


async def require_auth(request: Request) -> str:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    username = validate_session(token)
    if username is None:
        raise HTTPException(status_code=401, detail="Session expired")
    return username
