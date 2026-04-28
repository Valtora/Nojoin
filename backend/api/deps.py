from typing import Any, AsyncGenerator, Optional
from urllib.parse import urlparse

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
from backend.utils.config_manager import LOCAL_WEB_ORIGINS, get_trusted_web_origin

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
PAIRING_MANAGEMENT_TOKEN_TYPES = STANDARD_USER_TOKEN_TYPES | {COMPANION_TOKEN_TYPE}
PAIRING_MANAGEMENT_SCOPE_REQUIREMENTS = {
    **STANDARD_USER_SCOPE_REQUIREMENTS,
    COMPANION_TOKEN_TYPE: {COMPANION_BOOTSTRAP_SCOPE},
}
PASSWORD_CHANGE_EXEMPT_ROUTES = {
    ("/api/v1/users/me", "GET"),
    ("/api/v1/users/me/password", "PUT"),
    ("/api/v1/login/logout", "POST"),
}
BROWSER_SESSION_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
BROWSER_SESSION_TRUST_ERROR_DETAIL = (
    "Browser session requests must originate from the trusted Nojoin web origin."
)
LOCAL_BROWSER_SESSION_HOSTNAMES = {"localhost", "127.0.0.1", "::1", "test", "testserver"}

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


def _normalise_request_origin(value: Optional[str]) -> Optional[str]:
    if not value:
        return None

    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None

    default_port = 443 if parsed.scheme == "https" else 80
    if parsed.port and parsed.port != default_port:
        return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
    return f"{parsed.scheme}://{parsed.hostname}"


def _get_browser_session_trusted_origins() -> set[str]:
    configured_origin = get_trusted_web_origin()
    trusted_origins = {configured_origin}
    configured_host = urlparse(configured_origin).hostname

    if configured_host and configured_host.lower() in LOCAL_BROWSER_SESSION_HOSTNAMES:
        for origin in LOCAL_WEB_ORIGINS:
            normalised = _normalise_request_origin(origin)
            if normalised:
                trusted_origins.add(normalised)

    return trusted_origins


def _get_request_source_origin(request: Request) -> Optional[str]:
    origin = _normalise_request_origin(request.headers.get("origin"))
    if origin:
        return origin

    return _normalise_request_origin(request.headers.get("referer"))


def enforce_trusted_browser_origin(request: Request) -> None:
    if request.method.upper() in BROWSER_SESSION_SAFE_METHODS:
        return

    request_origin = _get_request_source_origin(request)
    if request_origin and request_origin in _get_browser_session_trusted_origins():
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=BROWSER_SESSION_TRUST_ERROR_DETAIL,
    )


def _resolve_request_token(
    request: Request,
    token: Optional[str],
) -> tuple[Optional[str], bool]:
    cookie_token = request.cookies.get("access_token")
    actual_token = token or cookie_token
    used_cookie_auth = token is None and cookie_token is not None
    return actual_token, used_cookie_auth


def enforce_password_change_policy(user: User, *, path: str, method: str) -> None:
    if not user.force_password_change:
        return

    if (path, method.upper()) in PASSWORD_CHANGE_EXEMPT_ROUTES:
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Password change required",
    )


def _validate_companion_recording_claim(payload: dict[str, Any], recording_id: str) -> None:
    if payload.get("token_type") != COMPANION_TOKEN_TYPE:
        return

    token_recording_id = payload.get("recording_public_id")

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
    actual_token, used_cookie_auth = _resolve_request_token(request, token)
    
    if not actual_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if used_cookie_auth:
        enforce_trusted_browser_origin(request)

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
    actual_token, used_cookie_auth = _resolve_request_token(request, token)
    
    if not actual_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if used_cookie_auth:
        enforce_trusted_browser_origin(request)

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
    recording_id: Optional[str] = None,
) -> User:
    actual_token, used_cookie_auth = _resolve_request_token(request, token)

    if not actual_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if used_cookie_auth:
        enforce_trusted_browser_origin(request)

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


async def get_current_companion_bootstrap_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: Optional[str] = Depends(reusable_oauth2),
) -> User:
    user, _ = await get_current_companion_bootstrap_details(
        request,
        db,
        token,
    )
    return user


async def get_current_companion_bootstrap_details(
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: Optional[str] = Depends(reusable_oauth2),
) -> tuple[User, dict[str, Any]]:
    actual_token, used_cookie_auth = _resolve_request_token(request, token)

    if not actual_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if used_cookie_auth:
        enforce_trusted_browser_origin(request)

    user, payload = await get_authenticated_token_details(
        db,
        actual_token,
        allowed_token_types={COMPANION_TOKEN_TYPE},
        required_scopes_by_type={
            COMPANION_TOKEN_TYPE: {COMPANION_BOOTSTRAP_SCOPE},
        },
    )
    enforce_password_change_policy(user, path=request.url.path, method=request.method)
    return user, payload


async def get_current_pairing_management_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    token: Optional[str] = Depends(reusable_oauth2),
) -> User:
    actual_token, used_cookie_auth = _resolve_request_token(request, token)

    if not actual_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if used_cookie_auth:
        enforce_trusted_browser_origin(request)

    user = await get_authenticated_user_from_token(
        db,
        actual_token,
        allowed_token_types=PAIRING_MANAGEMENT_TOKEN_TYPES,
        required_scopes_by_type=PAIRING_MANAGEMENT_SCOPE_REQUIREMENTS,
    )
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
