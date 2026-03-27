import pytest
import bcrypt
from backend.auth import verify_password, create_session, validate_session, SESSION_COOKIE_NAME
from backend.config import set_setting

def test_verify_password_correct(db_path):
    pw_hash = bcrypt.hashpw(b"secret123", bcrypt.gensalt()).decode()
    set_setting("auth_password_hash", pw_hash)
    assert verify_password("secret123") is True

def test_verify_password_wrong(db_path):
    pw_hash = bcrypt.hashpw(b"secret123", bcrypt.gensalt()).decode()
    set_setting("auth_password_hash", pw_hash)
    assert verify_password("wrong") is False

def test_session_roundtrip():
    token = create_session("admin")
    username = validate_session(token)
    assert username == "admin"

def test_invalid_session():
    assert validate_session("garbage-token") is None

def test_expired_session():
    from backend.auth import _get_serializer
    s = _get_serializer()
    assert validate_session("invalid.token.here") is None
