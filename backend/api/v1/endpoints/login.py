import asyncio
import json
from datetime import datetime, timedelta
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.api.deps import (
    enforce_trusted_browser_origin,
    get_db,
    get_current_pairing_management_user,
    get_current_user,
)
from backend.core import security
from backend.models.companion_pairing_request import CompanionPairingRequestStatus
from backend.models.user import User
from backend.services.companion_frontend_events import (
    COMPANION_EXPLICIT_DISCONNECT_EVENT,
    companion_frontend_events,
)
from backend.services.companion_pairing_service import (
    cancel_companion_pairing_request,
    cancel_pending_companion_pairings,
    complete_companion_pairing_request,
    CompanionPairingStateError,
    create_companion_pairing_request,
    exchange_companion_credential,
    get_active_companion_pairing_auth,
    get_companion_pairing_request_status,
    mark_companion_pairing_request_opened,
    normalize_origin,
    reject_companion_pairing_request,
    revoke_companion_pairings,
)
from backend.services.jwt_revocation_service import revoke_jwt_by_payload
from backend.utils.rate_limit import enforce_rate_limit

router = APIRouter()

LOGIN_RATE_LIMIT = 10
LOGIN_RATE_LIMIT_WINDOW_SECONDS = 10 * 60

# Companion credential exchange is a public endpoint that accepts a
# pairing_session_id + companion_credential_secret. We rate-limit it twice:
#   * per (IP, pairing_session_id) to bound brute force against a single
#     credential secret;
#   * per IP to bound an attacker rotating fabricated session ids.
COMPANION_EXCHANGE_PER_SESSION_RATE_LIMIT = 10
COMPANION_EXCHANGE_PER_SESSION_WINDOW_SECONDS = 10 * 60
COMPANION_EXCHANGE_PER_IP_RATE_LIMIT = 30
COMPANION_EXCHANGE_PER_IP_WINDOW_SECONDS = 10 * 60

# Local-control token minting is authenticated and origin-pinned but still
# protected against runaway clients or compromised paired-origin scripts.
COMPANION_LOCAL_TOKEN_RATE_LIMIT = 60
COMPANION_LOCAL_TOKEN_WINDOW_SECONDS = 60

# Pairing preparation drives the pairing state machine; throttle per user.
COMPANION_PAIRING_PREPARE_RATE_LIMIT = 20
COMPANION_PAIRING_PREPARE_WINDOW_SECONDS = 10 * 60

# Pairing mutation endpoints (revoke / disconnect / cancel-pending) share a
# single bucket so that an attacker cannot fan out across them.
COMPANION_PAIRING_MUTATION_RATE_LIMIT = 30
COMPANION_PAIRING_MUTATION_WINDOW_SECONDS = 10 * 60

# Companion SSE connection setup. Bounds reconnect storms; established streams
# are unaffected once the handshake passes.
COMPANION_EVENTS_CONNECT_RATE_LIMIT = 30
COMPANION_EVENTS_CONNECT_WINDOW_SECONDS = 60

COMPANION_PAIRING_REQUEST_TRANSITION_PER_REQUEST_RATE_LIMIT = 20
COMPANION_PAIRING_REQUEST_TRANSITION_PER_REQUEST_WINDOW_SECONDS = 10 * 60
COMPANION_PAIRING_REQUEST_TRANSITION_PER_IP_RATE_LIMIT = 60
COMPANION_PAIRING_REQUEST_TRANSITION_PER_IP_WINDOW_SECONDS = 10 * 60


class CompanionPairingRequestCreateResponse(BaseModel):
    request_id: str
    launch_url: str
    status: str
    expires_at: datetime
    backend_origin: str
    replacement: bool


class CompanionPairingRequestStatusResponse(BaseModel):
    request_id: str
    status: str
    expires_at: datetime
    opened_at: datetime | None = None
    completed_at: datetime | None = None
    detail: str | None = None
    backend_origin: str
    replacement: bool


class CompanionPairingRequestTransition(BaseModel):
    request_id: str
    request_secret: str


class CompanionPairingRequestRejectRequest(CompanionPairingRequestTransition):
    status: str
    detail: str | None = None
    failure_reason: str | None = None


class CompanionPairingRequestCompleteRequest(CompanionPairingRequestTransition):
    tls_fingerprint: str


class CompanionPairingRequestCompleteResponse(BaseModel):
    api_protocol: str
    api_host: str
    api_port: int
    paired_web_origin: str
    companion_credential_secret: str
    local_control_secret: str
    local_control_secret_version: int
    backend_pairing_id: str
    backend_identity_key_id: str
    backend_identity_public_key: str


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
    await enforce_rate_limit(
        request,
        namespace="companion-local-token",
        limit=COMPANION_LOCAL_TOKEN_RATE_LIMIT,
        window_seconds=COMPANION_LOCAL_TOKEN_WINDOW_SECONDS,
        discriminator=current_user.username,
        detail="Too many local control token requests. Please try again later.",
    )

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
    form_data: OAuth2PasswordRequestForm = Depends()
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
        except Exception:
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
    response_model=CompanionPairingRequestCreateResponse,
)
async def create_browser_companion_pairing_request(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CompanionPairingRequestCreateResponse:
    await enforce_rate_limit(
        request,
        namespace="companion-pairing-prepare",
        limit=COMPANION_PAIRING_PREPARE_RATE_LIMIT,
        window_seconds=COMPANION_PAIRING_PREPARE_WINDOW_SECONDS,
        discriminator=current_user.username,
        detail="Too many companion pairing attempts. Please try again later.",
    )

    try:
        pairing_request = await create_companion_pairing_request(
            db,
            current_user=current_user,
            paired_web_origin=request.headers.get("origin"),
        )
    except CompanionPairingStateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return CompanionPairingRequestCreateResponse(**pairing_request.__dict__)


@router.get(
    "/login/companion-pairing/requests/{request_id}",
    response_model=CompanionPairingRequestStatusResponse,
)
async def get_browser_companion_pairing_request_status(
    request_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CompanionPairingRequestStatusResponse:
    try:
        request_status = await get_companion_pairing_request_status(
            db,
            current_user=current_user,
            request_id=request_id,
        )
    except CompanionPairingStateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return CompanionPairingRequestStatusResponse(**request_status.__dict__)


@router.delete(
    "/login/companion-pairing/requests/{request_id}",
    response_model=CompanionPairingCancelResponse,
)
async def cancel_browser_companion_pairing_request(
    request_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_pairing_management_user),
) -> CompanionPairingCancelResponse:
    await enforce_rate_limit(
        request,
        namespace="companion-pairing-mutation",
        limit=COMPANION_PAIRING_MUTATION_RATE_LIMIT,
        window_seconds=COMPANION_PAIRING_MUTATION_WINDOW_SECONDS,
        discriminator=current_user.username,
        detail="Too many companion pairing changes. Please try again later.",
    )

    try:
        cancelled_count = await cancel_companion_pairing_request(
            db,
            current_user=current_user,
            request_id=request_id,
        )
    except CompanionPairingStateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return CompanionPairingCancelResponse(
        cancelled=cancelled_count > 0,
        cancelled_count=cancelled_count,
    )


@router.post(
    "/login/companion-pairing/request/opened",
    response_model=CompanionPairingRequestStatusResponse,
)
async def mark_companion_pairing_request_as_opened(
    payload: CompanionPairingRequestTransition,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> CompanionPairingRequestStatusResponse:
    await enforce_rate_limit(
        request,
        namespace="companion-pairing-request-transition-ip",
        limit=COMPANION_PAIRING_REQUEST_TRANSITION_PER_IP_RATE_LIMIT,
        window_seconds=COMPANION_PAIRING_REQUEST_TRANSITION_PER_IP_WINDOW_SECONDS,
        detail="Too many companion pairing requests. Please try again later.",
    )
    await enforce_rate_limit(
        request,
        namespace="companion-pairing-request-transition-request",
        limit=COMPANION_PAIRING_REQUEST_TRANSITION_PER_REQUEST_RATE_LIMIT,
        window_seconds=COMPANION_PAIRING_REQUEST_TRANSITION_PER_REQUEST_WINDOW_SECONDS,
        discriminator=payload.request_id.strip(),
        detail="Too many companion pairing requests. Please try again later.",
    )

    try:
        request_status = await mark_companion_pairing_request_opened(
            db,
            request_id=payload.request_id,
            request_secret=payload.request_secret,
        )
    except CompanionPairingStateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return CompanionPairingRequestStatusResponse(**request_status.__dict__)


@router.post(
    "/login/companion-pairing/request/reject",
    response_model=CompanionPairingRequestStatusResponse,
)
async def reject_browser_companion_pairing_request(
    payload: CompanionPairingRequestRejectRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> CompanionPairingRequestStatusResponse:
    await enforce_rate_limit(
        request,
        namespace="companion-pairing-request-transition-ip",
        limit=COMPANION_PAIRING_REQUEST_TRANSITION_PER_IP_RATE_LIMIT,
        window_seconds=COMPANION_PAIRING_REQUEST_TRANSITION_PER_IP_WINDOW_SECONDS,
        detail="Too many companion pairing requests. Please try again later.",
    )
    await enforce_rate_limit(
        request,
        namespace="companion-pairing-request-transition-request",
        limit=COMPANION_PAIRING_REQUEST_TRANSITION_PER_REQUEST_RATE_LIMIT,
        window_seconds=COMPANION_PAIRING_REQUEST_TRANSITION_PER_REQUEST_WINDOW_SECONDS,
        discriminator=payload.request_id.strip(),
        detail="Too many companion pairing requests. Please try again later.",
    )

    status_value = payload.status.strip().lower()
    if status_value == CompanionPairingRequestStatus.DECLINED.value:
        target_status = CompanionPairingRequestStatus.DECLINED
        detail = payload.detail or "Pairing was declined in the Companion app."
        failure_reason = payload.failure_reason or "user_declined"
    elif status_value == CompanionPairingRequestStatus.FAILED.value:
        target_status = CompanionPairingRequestStatus.FAILED
        detail = payload.detail or "The Companion app could not complete this pairing request."
        failure_reason = payload.failure_reason or "companion_failed"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Companion pairing rejections must use status 'declined' or 'failed'.",
        )

    try:
        request_status = await reject_companion_pairing_request(
            db,
            request_id=payload.request_id,
            request_secret=payload.request_secret,
            status=target_status,
            detail=detail,
            failure_reason=failure_reason,
        )
    except CompanionPairingStateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return CompanionPairingRequestStatusResponse(**request_status.__dict__)


@router.post(
    "/login/companion-pairing/request/complete",
    response_model=CompanionPairingRequestCompleteResponse,
)
async def complete_browser_companion_pairing_request(
    payload: CompanionPairingRequestCompleteRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> CompanionPairingRequestCompleteResponse:
    await enforce_rate_limit(
        request,
        namespace="companion-pairing-request-transition-ip",
        limit=COMPANION_PAIRING_REQUEST_TRANSITION_PER_IP_RATE_LIMIT,
        window_seconds=COMPANION_PAIRING_REQUEST_TRANSITION_PER_IP_WINDOW_SECONDS,
        detail="Too many companion pairing requests. Please try again later.",
    )
    await enforce_rate_limit(
        request,
        namespace="companion-pairing-request-transition-request",
        limit=COMPANION_PAIRING_REQUEST_TRANSITION_PER_REQUEST_RATE_LIMIT,
        window_seconds=COMPANION_PAIRING_REQUEST_TRANSITION_PER_REQUEST_WINDOW_SECONDS,
        discriminator=payload.request_id.strip(),
        detail="Too many companion pairing requests. Please try again later.",
    )

    try:
        completion = await complete_companion_pairing_request(
            db,
            request_id=payload.request_id,
            request_secret=payload.request_secret,
            tls_fingerprint=payload.tls_fingerprint,
        )
    except CompanionPairingStateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return CompanionPairingRequestCompleteResponse(**completion.__dict__)


@router.delete(
    "/login/companion-pairing",
    response_model=CompanionPairingRevokeResponse,
)
async def revoke_companion_pairing(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_pairing_management_user),
) -> CompanionPairingRevokeResponse:
    await enforce_rate_limit(
        request,
        namespace="companion-pairing-mutation",
        limit=COMPANION_PAIRING_MUTATION_RATE_LIMIT,
        window_seconds=COMPANION_PAIRING_MUTATION_WINDOW_SECONDS,
        discriminator=current_user.username,
        detail="Too many companion pairing changes. Please try again later.",
    )

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
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_pairing_management_user),
) -> CompanionPairingDisconnectResponse:
    await enforce_rate_limit(
        request,
        namespace="companion-pairing-mutation",
        limit=COMPANION_PAIRING_MUTATION_RATE_LIMIT,
        window_seconds=COMPANION_PAIRING_MUTATION_WINDOW_SECONDS,
        discriminator=current_user.username,
        detail="Too many companion pairing changes. Please try again later.",
    )

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
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_pairing_management_user),
) -> CompanionPairingCancelResponse:
    await enforce_rate_limit(
        request,
        namespace="companion-pairing-mutation",
        limit=COMPANION_PAIRING_MUTATION_RATE_LIMIT,
        window_seconds=COMPANION_PAIRING_MUTATION_WINDOW_SECONDS,
        discriminator=current_user.username,
        detail="Too many companion pairing changes. Please try again later.",
    )

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
    await enforce_rate_limit(
        request,
        namespace="companion-events-connect",
        limit=COMPANION_EVENTS_CONNECT_RATE_LIMIT,
        window_seconds=COMPANION_EVENTS_CONNECT_WINDOW_SECONDS,
        discriminator=current_user.username,
        detail="Too many companion event stream reconnects. Please try again later.",
    )

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
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> CompanionAccessTokenResponse:
    pairing_session_id = payload.pairing_session_id.strip()
    companion_credential_secret = payload.companion_credential_secret.strip()

    if not pairing_session_id or not companion_credential_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pairing session id and companion credential secret are required.",
        )

    # IP-only bucket bounds attackers rotating fabricated session ids.
    await enforce_rate_limit(
        request,
        namespace="companion-exchange-ip",
        limit=COMPANION_EXCHANGE_PER_IP_RATE_LIMIT,
        window_seconds=COMPANION_EXCHANGE_PER_IP_WINDOW_SECONDS,
        detail="Too many companion token exchange requests. Please try again later.",
    )
    # Per-session bucket bounds brute force against a single credential secret.
    await enforce_rate_limit(
        request,
        namespace="companion-exchange-session",
        limit=COMPANION_EXCHANGE_PER_SESSION_RATE_LIMIT,
        window_seconds=COMPANION_EXCHANGE_PER_SESSION_WINDOW_SECONDS,
        discriminator=pairing_session_id,
        detail="Too many companion token exchange requests. Please try again later.",
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
