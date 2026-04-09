from datetime import timedelta
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.api.deps import get_db, get_current_user
from backend.core import security
from backend.models.user import User
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

    if not user or not security.verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect username or password"
        )
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")

    return user

@router.post("/login/access-token")
async def login_access_token(
    request: Request,
    db: AsyncSession = Depends(get_db),
    form_data: OAuth2PasswordRequestForm = Depends()
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
    form_data: OAuth2PasswordRequestForm = Depends()
) -> Any:
    """
    Browser session login. Sets a secure HttpOnly cookie and returns UI metadata only.
    """
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
async def logout_user(response: Response) -> Any:
    """
    Endpoint to clear the HttpOnly access token on logout.
    """
    response.delete_cookie(
        key="access_token",
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return {"message": "Logged out successfully"}

@router.get("/login/companion-token")
async def get_companion_token(
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Issues a scoped JWT for companion recording operations.
    """
    access_token_expires = timedelta(minutes=security.COMPANION_TOKEN_EXPIRE_MINUTES)
    token = security.create_access_token(
        current_user.username,
        token_type=security.COMPANION_TOKEN_TYPE,
        scopes=[security.COMPANION_RECORDING_SCOPE],
        expires_delta=access_token_expires,
    )
    return {
        "token": token,
        "expires_in": security.COMPANION_TOKEN_EXPIRE_MINUTES * 60,
    }
