import asyncio
import json
from datetime import timedelta
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.api.deps import (
    get_db,
    get_current_pairing_management_user,
    get_current_user,
)
from backend.api.v1.endpoints.system import resolve_tls_fingerprint
from backend.core import security
from backend.models.user import User
from backend.services.companion_frontend_events import (
    COMPANION_EXPLICIT_DISCONNECT_EVENT,
    companion_frontend_events,
)
from backend.services.companion_pairing_service import (
    cancel_pending_companion_pairings,
    CompanionPairingStateError,
    exchange_companion_credential,
    finalize_companion_pairing,
    get_active_companion_pairing_auth,
    normalize_origin,
    prepare_companion_pairing,
    revoke_companion_pairings,
)
from backend.utils.rate_limit import enforce_rate_limit

router = APIRouter()

LOGIN_RATE_LIMIT = 10
LOGIN_RATE_LIMIT_WINDOW_SECONDS = 10 * 60


class CompanionPairingPrepareRequest(BaseModel):
    pairing_code: str


class CompanionPairingPrepareResponse(BaseModel):
    pairing_code: str
    companion_credential_secret: str
    api_protocol: str
    api_host: str
    api_port: int
    tls_fingerprint: str | None = None
    local_control_secret: str
    local_control_secret_version: int
    backend_pairing_id: str


class CompanionCredentialExchangeRequest(BaseModel):
    pairing_session_id: str
    companion_credential_secret: str


class CompanionAccessTokenResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int


class CompanionPairingRevokeResponse(BaseModel):
    revoked: bool
    revoked_count: int


class CompanionPairingCancelResponse(BaseModel):
    cancelled: bool
    cancelled_count: int


class CompanionPairingDisconnectResponse(BaseModel):
    disconnected: bool
    revoked_count: int
    signal_type: str


class CompanionLocalControlTokenResponse(BaseModel):
    token: str
    expires_in: int


class CompanionLocalControlTokenRequest(BaseModel):
    actions: list[str]


def _parse_local_control_actions(raw_actions: str | list[str]) -> list[str]:
    raw_values = raw_actions.split(",") if isinstance(raw_actions, str) else raw_actions
    actions = sorted(
        {
            action.strip()
            for action in raw_values
            if isinstance(action, str) and action.strip()
        }
    )
    if not actions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one local control action is required.",
        )

    invalid_actions = [
        action for action in actions if action not in security.LOCAL_CONTROL_ALLOWED_ACTIONS
    ]
    if invalid_actions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Unsupported local control action(s): " + ", ".join(invalid_actions)
            ),
        )

    return actions


async def _issue_companion_local_control_token(
    request: Request,
    *,
    requested_actions: list[str],
    db: AsyncSession,
    current_user: User,
) -> CompanionLocalControlTokenResponse:
    try:
        pairing_auth = await get_active_companion_pairing_auth(
            db,
            current_user=current_user,
        )
    except CompanionPairingStateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    request_origin = normalize_origin(request.headers.get("origin"))
    if request_origin != pairing_auth.paired_web_origin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Local control tokens may only be issued to the paired web origin.",
        )

    token = security.create_local_control_token(
        secret_key=pairing_auth.local_control_secret,
        subject=current_user.username,
        user_id=current_user.id,
        username=current_user.username,
        origin=pairing_auth.paired_web_origin,
        actions=requested_actions,
        pairing_session_id=pairing_auth.pairing_session_id,
        secret_version=pairing_auth.local_control_secret_version,
    )

    return CompanionLocalControlTokenResponse(
        token=token,
        expires_in=security.COMPANION_LOCAL_CONTROL_TOKEN_EXPIRE_SECONDS,
    )


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

@router.get(
    "/login/companion-local-token",
    response_model=CompanionLocalControlTokenResponse,
)
async def get_companion_local_control_token(
    request: Request,
    actions: str = Query(..., description="Comma-separated local control actions"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CompanionLocalControlTokenResponse:
    return await _issue_companion_local_control_token(
        request,
        requested_actions=_parse_local_control_actions(actions),
        db=db,
        current_user=current_user,
    )


@router.post(
    "/login/companion-local-token",
    response_model=CompanionLocalControlTokenResponse,
)
async def create_companion_local_control_token(
    payload: CompanionLocalControlTokenRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CompanionLocalControlTokenResponse:
    return await _issue_companion_local_control_token(
        request,
        requested_actions=_parse_local_control_actions(payload.actions),
        db=db,
        current_user=current_user,
    )


@router.post(
    "/login/companion-pairing",
    response_model=CompanionPairingPrepareResponse,
)
async def prepare_companion_pairing_payload(
    payload: CompanionPairingPrepareRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CompanionPairingPrepareResponse:
    pairing_code = payload.pairing_code.strip().upper()
    if not pairing_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pairing code is required",
        )

    try:
        prepared = await prepare_companion_pairing(
            db,
            current_user=current_user,
            pairing_code=pairing_code,
            paired_web_origin=request.headers.get("origin"),
            tls_fingerprint=resolve_tls_fingerprint(),
        )
    except CompanionPairingStateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return CompanionPairingPrepareResponse(**prepared.__dict__)


@router.delete(
    "/login/companion-pairing",
    response_model=CompanionPairingRevokeResponse,
)
async def revoke_companion_pairing(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_pairing_management_user),
) -> CompanionPairingRevokeResponse:
    revoked_count = await revoke_companion_pairings(
        db,
        current_user=current_user,
    )
    return CompanionPairingRevokeResponse(
        revoked=revoked_count > 0,
        revoked_count=revoked_count,
    )


@router.post(
    "/login/companion-pairing/disconnect",
    response_model=CompanionPairingDisconnectResponse,
)
async def disconnect_companion_pairing(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_pairing_management_user),
) -> CompanionPairingDisconnectResponse:
    revoked_count = await revoke_companion_pairings(
        db,
        current_user=current_user,
    )
    await companion_frontend_events.publish_explicit_disconnect(current_user.id)
    return CompanionPairingDisconnectResponse(
        disconnected=True,
        revoked_count=revoked_count,
        signal_type=COMPANION_EXPLICIT_DISCONNECT_EVENT,
    )


@router.delete(
    "/login/companion-pairing/pending",
    response_model=CompanionPairingCancelResponse,
)
async def cancel_pending_companion_pairing(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_pairing_management_user),
) -> CompanionPairingCancelResponse:
    cancelled_count = await cancel_pending_companion_pairings(
        db,
        current_user=current_user,
    )
    return CompanionPairingCancelResponse(
        cancelled=cancelled_count > 0,
        cancelled_count=cancelled_count,
    )


@router.get("/login/companion-events")
async def stream_companion_events(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    async def event_generator():
        queue = await companion_frontend_events.subscribe(current_user.id)
        try:
            yield "event: ready\ndata: {}\n\n"
            while True:
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
                    continue

                yield f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
        finally:
            await companion_frontend_events.unsubscribe(current_user.id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/login/companion-token/exchange",
    response_model=CompanionAccessTokenResponse,
)
async def exchange_companion_token(
    payload: CompanionCredentialExchangeRequest,
    db: AsyncSession = Depends(get_db),
) -> CompanionAccessTokenResponse:
    pairing_session_id = payload.pairing_session_id.strip()
    companion_credential_secret = payload.companion_credential_secret.strip()

    if not pairing_session_id or not companion_credential_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pairing session id and companion credential secret are required.",
        )

    try:
        exchange_result = await exchange_companion_credential(
            db,
            pairing_session_id=pairing_session_id,
            companion_credential_secret=companion_credential_secret,
        )
    except CompanionPairingStateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    access_token = security.create_access_token(
        exchange_result.user.username,
        token_type=security.COMPANION_TOKEN_TYPE,
        scopes=[security.COMPANION_BOOTSTRAP_SCOPE],
        expires_delta=timedelta(seconds=security.COMPANION_ACCESS_TOKEN_EXPIRE_SECONDS),
        extra_claims={
            security.COMPANION_PAIRING_ID_CLAIM: exchange_result.pairing_session_id,
        },
    )
    return CompanionAccessTokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=security.COMPANION_ACCESS_TOKEN_EXPIRE_SECONDS,
    )
