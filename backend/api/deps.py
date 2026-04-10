from typing import Any, AsyncGenerator, Optional
from fastapi import Depends, HTTPException, status, Request, WebSocket
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.core.db import get_session
from backend.core.security import (
    ALGORITHM,
    API_ACCESS_SCOPE,
    API_TOKEN_TYPE,
    COMPANION_BOOTSTRAP_SCOPE,
    COMPANION_RECORDING_SCOPE,
    COMPANION_TOKEN_TYPE,
    SECRET_KEY,
    SESSION_TOKEN_TYPE,
    WEB_SESSION_SCOPE,
)
from backend.models.user import User

reusable_oauth2 = OAuth2PasswordBearer(
    tokenUrl="/api/v1/login/access-token",
    auto_error=False
)

STANDARD_USER_TOKEN_TYPES = {SESSION_TOKEN_TYPE, API_TOKEN_TYPE}
STANDARD_USER_SCOPE_REQUIREMENTS = {
    SESSION_TOKEN_TYPE: {WEB_SESSION_SCOPE},
    API_TOKEN_TYPE: {API_ACCESS_SCOPE},
}
RECORDING_CLIENT_TOKEN_TYPES = STANDARD_USER_TOKEN_TYPES | {COMPANION_TOKEN_TYPE}
RECORDING_CLIENT_INIT_SCOPE_REQUIREMENTS = {
    **STANDARD_USER_SCOPE_REQUIREMENTS,
    COMPANION_TOKEN_TYPE: {COMPANION_BOOTSTRAP_SCOPE},
}
RECORDING_CLIENT_OPERATION_SCOPE_REQUIREMENTS = {
    **STANDARD_USER_SCOPE_REQUIREMENTS,
    COMPANION_TOKEN_TYPE: {COMPANION_RECORDING_SCOPE},
}
PASSWORD_CHANGE_EXEMPT_ROUTES = {
    ("/api/v1/users/me", "GET"),
    ("/api/v1/users/me/password", "PUT"),
    ("/api/v1/login/logout", "POST"),
}

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_session():
        yield session


def _get_bearer_token(authorization_header: Optional[str]) -> Optional[str]:
    if not authorization_header:
        return None

    scheme, _, token = authorization_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None

    return token.strip()


def _extract_token_scopes(raw_scopes: object) -> set[str]:
    if isinstance(raw_scopes, str):
        return {raw_scopes}
    if isinstance(raw_scopes, (list, tuple, set)):
        return {scope for scope in raw_scopes if isinstance(scope, str)}
    return set()


def enforce_password_change_policy(user: User, *, path: str, method: str) -> None:
    if not user.force_password_change:
        return

    if (path, method.upper()) in PASSWORD_CHANGE_EXEMPT_ROUTES:
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Password change required",
    )


def _validate_companion_recording_claim(payload: dict[str, Any], recording_id: int) -> None:
    if payload.get("token_type") != COMPANION_TOKEN_TYPE:
        return

    raw_recording_id = payload.get("recording_id")
    try:
        token_recording_id = int(raw_recording_id)
    except (TypeError, ValueError):
        token_recording_id = None

    if token_recording_id != recording_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token does not grant access to this recording",
        )


async def get_authenticated_token_details(
    db: AsyncSession,
    actual_token: str,
    *,
    allowed_token_types: set[str],
    required_scopes_by_type: Optional[dict[str, set[str]]] = None,
) -> tuple[User, dict[str, Any]]:
    try:
        payload = jwt.decode(actual_token, SECRET_KEY, algorithms=[ALGORITHM])
        token_data = payload.get("sub")
        token_type = payload.get("token_type")

        if token_data is None or token_type not in allowed_token_types:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token_scopes = _extract_token_scopes(payload.get("scopes"))
        required_scopes = (required_scopes_by_type or {}).get(token_type, set())
        if required_scopes and not required_scopes.issubset(token_scopes):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Token does not have the required scope",
            )
    except (JWTError, ValidationError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    query = select(User).where(User.username == token_data)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")

    return user, payload


async def get_authenticated_user_from_token(
    db: AsyncSession,
    actual_token: str,
    *,
    allowed_token_types: set[str],
    required_scopes_by_type: Optional[dict[str, set[str]]] = None,
) -> User:
    user, _ = await get_authenticated_token_details(
        db,
        actual_token,
        allowed_token_types=allowed_token_types,
        required_scopes_by_type=required_scopes_by_type,
    )
    return user

async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: Optional[str] = Depends(reusable_oauth2)
) -> User:
    actual_token = token or request.cookies.get("access_token")
    
    if not actual_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await get_authenticated_user_from_token(
        db,
        actual_token,
        allowed_token_types=STANDARD_USER_TOKEN_TYPES,
        required_scopes_by_type=STANDARD_USER_SCOPE_REQUIREMENTS,
    )
    enforce_password_change_policy(user, path=request.url.path, method=request.method)
    return user

async def get_current_user_stream(
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: Optional[str] = Depends(reusable_oauth2)
) -> User:
    actual_token = token or request.cookies.get("access_token")
    
    if not actual_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await get_authenticated_user_from_token(
        db,
        actual_token,
        allowed_token_types=STANDARD_USER_TOKEN_TYPES,
        required_scopes_by_type=STANDARD_USER_SCOPE_REQUIREMENTS,
    )
    enforce_password_change_policy(user, path=request.url.path, method=request.method)
    return user


async def get_current_recording_client_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: Optional[str] = Depends(reusable_oauth2),
    recording_id: Optional[int] = None,
) -> User:
    actual_token = token or request.cookies.get("access_token")

    if not actual_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    required_scopes = (
        RECORDING_CLIENT_OPERATION_SCOPE_REQUIREMENTS
        if recording_id is not None
        else RECORDING_CLIENT_INIT_SCOPE_REQUIREMENTS
    )
    user, payload = await get_authenticated_token_details(
        db,
        actual_token,
        allowed_token_types=RECORDING_CLIENT_TOKEN_TYPES,
        required_scopes_by_type=required_scopes,
    )
    if recording_id is not None:
        _validate_companion_recording_claim(payload, recording_id)

    enforce_password_change_policy(user, path=request.url.path, method=request.method)
    return user

async def get_current_active_superuser(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=400, detail="The user doesn't have enough privileges"
        )
    return current_user

async def get_current_admin_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Allow access if user is OWNER, ADMIN, or a SUPERUSER.
    """
    allowed_roles = ["owner", "admin"]
    if current_user.role not in allowed_roles and not current_user.is_superuser:
         raise HTTPException(
            status_code=403, detail="Not authorized"
        )
    return current_user

async def get_current_user_ws(
    websocket: WebSocket,
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Dedicated dependency for WebSocket authentication.
    Bypasses OAuth2PasswordBearer which fails in WS context.
    """
    actual_token = _get_bearer_token(websocket.headers.get("authorization")) or websocket.cookies.get("access_token")
    if not actual_token:
        # For WebSockets, we can't easily raise HTTPException with headers.
        # We'll just raise a standard 401/403 which FastAPI WS handles by closing connection usually,
        # or handle gracefully in the endpoint.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )

    user = await get_authenticated_user_from_token(
        db,
        actual_token,
        allowed_token_types=STANDARD_USER_TOKEN_TYPES,
        required_scopes_by_type=STANDARD_USER_SCOPE_REQUIREMENTS,
    )
    if user.force_password_change:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password change required"
        )
    return user

async def get_current_active_superuser_ws(
    current_user: User = Depends(get_current_user_ws),
) -> User:
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=400, detail="The user doesn't have enough privileges"
        )
    return current_user
