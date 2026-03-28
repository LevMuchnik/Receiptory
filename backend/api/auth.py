from fastapi import APIRouter, Response, HTTPException, Depends

from backend.auth import (
    verify_password,
    create_session,
    require_auth,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
)
from backend.config import get_setting
from backend.models import LoginRequest, LoginResponse, AuthMeResponse

router = APIRouter()


@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest, response: Response):
    expected_username = get_setting("auth_username")
    if req.username != expected_username or not verify_password(req.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_session(req.username)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_MAX_AGE,
    )
    return LoginResponse(message="Login successful", username=req.username)


@router.get("/me", response_model=AuthMeResponse)
def me(username: str = Depends(require_auth)):
    return AuthMeResponse(username=username)
