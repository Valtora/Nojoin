from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.api.deps import (
    enforce_trusted_browser_origin,
    get_db,
)
from backend.core import security
from backend.models.user import User
from backend.services.jwt_revocation_service import revoke_jwt_by_payload
from backend.utils.rate_limit import enforce_rate_limit

router = APIRouter()

LOGIN_RATE_LIMIT = 10
LOGIN_RATE_LIMIT_WINDOW_SECONDS = 10 * 60


def _build_login_metadata(user: User) -> dict[str, Any]:
    return {
        "force_password_change": user.force_password_change,
        "is_superuser": user.is_superuser,
        "username": user.username,
    }


async def _authenticate_user_credentials(
    db: AsyncSession,
    form_data: OAuth2PasswordRequestForm,
) -> User:
    query = select(User).where(User.username == form_data.username)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user or not security.verify_password(
        form_data.password, user.hashed_password
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect username or password",
        )
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")

    return user


@router.post("/login/access-token")
async def login_access_token(
    request: Request,
    db: AsyncSession = Depends(get_db),
    form_data: OAuth2PasswordRequestForm = Depends(),
) -> Any:
    """
    OAuth2-compatible bearer token login for explicit API clients.
    """
    await enforce_rate_limit(
        request,
        namespace="login",
        limit=LOGIN_RATE_LIMIT,
        window_seconds=LOGIN_RATE_LIMIT_WINDOW_SECONDS,
        detail="Too many login attempts. Please try again later.",
    )

    user = await _authenticate_user_credentials(db, form_data)

    access_token_expires = timedelta(minutes=security.API_TOKEN_EXPIRE_MINUTES)
    token = security.create_access_token(
        user.username,
        token_type=security.API_TOKEN_TYPE,
        scopes=[security.API_ACCESS_SCOPE],
        expires_delta=access_token_expires,
        token_version=user.token_version,
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": security.API_TOKEN_EXPIRE_MINUTES * 60,
        **_build_login_metadata(user),
    }


@router.post("/login/session")
async def login_session(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    form_data: OAuth2PasswordRequestForm = Depends(),
) -> Any:
    """
    Browser session login. Sets a secure HttpOnly cookie and returns UI metadata only.
    """
    enforce_trusted_browser_origin(request)

    await enforce_rate_limit(
        request,
        namespace="login",
        limit=LOGIN_RATE_LIMIT,
        window_seconds=LOGIN_RATE_LIMIT_WINDOW_SECONDS,
        detail="Too many login attempts. Please try again later.",
    )

    user = await _authenticate_user_credentials(db, form_data)

    session_expires = timedelta(minutes=security.SESSION_TOKEN_EXPIRE_MINUTES)
    token = security.create_access_token(
        user.username,
        token_type=security.SESSION_TOKEN_TYPE,
        scopes=[security.WEB_SESSION_SCOPE],
        expires_delta=session_expires,
        token_version=user.token_version,
    )

    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=security.SESSION_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )

    return _build_login_metadata(user)


@router.post("/login/logout")
async def logout_user(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Endpoint to clear the HttpOnly access token on logout.

    The captured JWT itself is added to the revocation denylist so that any
    copy outside the browser cookie also stops verifying immediately.
    """
    enforce_trusted_browser_origin(request)

    cookie_token = request.cookies.get("access_token")
    if cookie_token:
        try:
            payload = security.decode_access_token(cookie_token)
        except Exception:  # noqa: BLE001
            payload = None
        if payload and payload.get("token_type") == security.SESSION_TOKEN_TYPE:
            username = payload.get("sub")
            if isinstance(username, str) and username:
                user_result = await db.execute(
                    select(User).where(User.username == username)
                )
                user = user_result.scalar_one_or_none()
                if user is not None:
                    await revoke_jwt_by_payload(
                        db,
                        payload,
                        user,
                        reason="logout",
                    )

    response.delete_cookie(
        key="access_token",
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return {"message": "Logged out successfully"}
