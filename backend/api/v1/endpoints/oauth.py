"""OAuth 2.1 endpoints for the Nojoin MCP connector.

Route layout:

- ``well_known_router`` is mounted at the application root (RFC 8414 /
  RFC 9728 discovery documents must live under ``/.well-known/``).
- The remaining routes live under ``/api/v1/oauth`` and are advertised to
  clients through the discovery documents, so their exact paths are an
  implementation detail.
- The interactive ``/oauth/authorize`` page itself is a web-client route;
  it calls ``GET /api/v1/oauth/authorize/info`` to validate the request and
  ``POST /api/v1/oauth/authorize/decision`` (session-authenticated) to
  approve or deny.
"""

import logging
from typing import Annotated, Any, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user, get_db
from backend.api.services import oauth_service
from backend.api.services.oauth_service import OAuthError
from backend.models.user import User
from backend.utils.config_manager import is_mcp_enabled
from backend.utils.rate_limit import enforce_rate_limit

logger = logging.getLogger(__name__)


async def require_mcp_enabled() -> None:
    """Hide the whole connector surface when MCP_ENABLED is off."""
    if not is_mcp_enabled():
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(dependencies=[Depends(require_mcp_enabled)])
well_known_router = APIRouter(dependencies=[Depends(require_mcp_enabled)])

REGISTRATION_RATE_LIMIT = 10
REGISTRATION_RATE_LIMIT_WINDOW_SECONDS = 10 * 60
TOKEN_RATE_LIMIT = 60
TOKEN_RATE_LIMIT_WINDOW_SECONDS = 10 * 60

MAX_STATE_LENGTH = 2048


def _oauth_error_response(exc: OAuthError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error, "error_description": exc.description},
        headers={"Cache-Control": "no-store"},
    )


@well_known_router.get("/.well-known/oauth-protected-resource")
@well_known_router.get("/.well-known/oauth-protected-resource/mcp")
async def protected_resource_metadata() -> dict[str, Any]:
    return oauth_service.build_protected_resource_metadata()


@well_known_router.get("/.well-known/oauth-authorization-server")
@well_known_router.get("/.well-known/oauth-authorization-server/mcp")
async def authorization_server_metadata() -> dict[str, Any]:
    return oauth_service.build_authorization_server_metadata()


@router.post("/register", status_code=201)
async def register_client(
    request: Request,
    metadata: dict[str, Any],
    db: AsyncSession = Depends(get_db),
):
    """Dynamic Client Registration (RFC 7591), open to public PKCE clients."""
    await enforce_rate_limit(
        request,
        namespace="oauth-register",
        limit=REGISTRATION_RATE_LIMIT,
        window_seconds=REGISTRATION_RATE_LIMIT_WINDOW_SECONDS,
        detail="Too many client registrations. Please try again later.",
    )
    try:
        return await oauth_service.register_client(db, metadata)
    except OAuthError as exc:
        return _oauth_error_response(exc)


class AuthorizeInfoRead(BaseModel):
    client_name: str
    scope: str
    scope_items: list[str]
    redirect_uri: str


class AuthorizeInfoQuery(BaseModel):
    client_id: str
    redirect_uri: str
    response_type: str = "code"
    scope: Optional[str] = None
    code_challenge: Optional[str] = None
    code_challenge_method: Optional[str] = None


@router.get("/authorize/info", response_model=AuthorizeInfoRead)
async def authorize_info(
    params: Annotated[AuthorizeInfoQuery, Depends()],
    db: AsyncSession = Depends(get_db),
):
    """Validate an authorization request so the consent page can render it."""
    client = await oauth_service.get_client(db, params.client_id)
    if client is None:
        return _oauth_error_response(OAuthError("invalid_client", "Unknown client."))
    try:
        normalised_scope = oauth_service.validate_authorization_request(
            oauth_service.AuthorizationRequest(
                client=client,
                redirect_uri=params.redirect_uri,
                response_type=params.response_type,
                scope=params.scope,
                code_challenge=params.code_challenge,
                code_challenge_method=params.code_challenge_method,
            )
        )
    except OAuthError as exc:
        return _oauth_error_response(exc)

    return AuthorizeInfoRead(
        client_name=client.client_name or "Unnamed client",
        scope=normalised_scope,
        scope_items=normalised_scope.split(),
        redirect_uri=params.redirect_uri,
    )


class AuthorizeDecision(BaseModel):
    approve: bool
    client_id: str
    redirect_uri: str
    response_type: str = "code"
    scope: Optional[str] = None
    state: Optional[str] = Field(default=None, max_length=MAX_STATE_LENGTH)
    code_challenge: Optional[str] = None
    code_challenge_method: Optional[str] = None
    resource: Optional[str] = None


class AuthorizeDecisionRead(BaseModel):
    redirect_to: str


def _append_query(redirect_uri: str, params: dict[str, str]) -> str:
    separator = "&" if "?" in redirect_uri else "?"
    return f"{redirect_uri}{separator}{urlencode(params)}"


@router.post("/authorize/decision", response_model=AuthorizeDecisionRead)
async def authorize_decision(
    decision: AuthorizeDecision,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Record the signed-in user's consent decision and mint the code.

    Browser-cookie authentication (with the trusted-origin check) is
    enforced by ``get_current_user``; this endpoint is the only place an
    authorization code can be created.
    """
    client = await oauth_service.get_client(db, decision.client_id)
    if client is None:
        return _oauth_error_response(OAuthError("invalid_client", "Unknown client."))

    authorization_request = oauth_service.AuthorizationRequest(
        client=client,
        redirect_uri=decision.redirect_uri,
        response_type=decision.response_type,
        scope=decision.scope,
        code_challenge=decision.code_challenge,
        code_challenge_method=decision.code_challenge_method,
        resource=decision.resource,
    )
    try:
        normalised_scope = oauth_service.validate_authorization_request(
            authorization_request
        )
    except OAuthError as exc:
        return _oauth_error_response(exc)

    if not decision.approve:
        params = {"error": "access_denied"}
        if decision.state:
            params["state"] = decision.state
        return AuthorizeDecisionRead(
            redirect_to=_append_query(decision.redirect_uri, params)
        )

    code = await oauth_service.create_authorization_code(
        db,
        request=authorization_request,
        user=current_user,
        scope=normalised_scope,
    )
    params = {"code": code}
    if decision.state:
        params["state"] = decision.state
    logger.info(
        "Issued authorization code for client %s to user %s.",
        client.client_id,
        current_user.id,
    )
    return AuthorizeDecisionRead(
        redirect_to=_append_query(decision.redirect_uri, params)
    )


class TokenRequestForm(BaseModel):
    grant_type: str
    client_id: str
    code: Optional[str] = None
    redirect_uri: Optional[str] = None
    code_verifier: Optional[str] = None
    refresh_token: Optional[str] = None


@router.post("/token")
async def token_endpoint(
    request: Request,
    form: Annotated[TokenRequestForm, Form()],
    db: AsyncSession = Depends(get_db),
):
    """Token endpoint: authorization_code and refresh_token grants."""
    await enforce_rate_limit(
        request,
        namespace="oauth-token",
        limit=TOKEN_RATE_LIMIT,
        window_seconds=TOKEN_RATE_LIMIT_WINDOW_SECONDS,
        detail="Too many token requests. Please try again later.",
    )
    try:
        if form.grant_type == "authorization_code":
            if not form.code or not form.redirect_uri:
                raise OAuthError(
                    "invalid_request",
                    "code and redirect_uri are required for authorization_code.",
                )
            payload = await oauth_service.exchange_authorization_code(
                db,
                code=form.code,
                client_id=form.client_id,
                redirect_uri=form.redirect_uri,
                code_verifier=form.code_verifier or "",
            )
        elif form.grant_type == "refresh_token":
            if not form.refresh_token:
                raise OAuthError(
                    "invalid_request",
                    "refresh_token is required for the refresh_token grant.",
                )
            payload = await oauth_service.refresh_access_token(
                db,
                refresh_token=form.refresh_token,
                client_id=form.client_id,
            )
        else:
            raise OAuthError(
                "unsupported_grant_type",
                "Only authorization_code and refresh_token are supported.",
            )
    except OAuthError as exc:
        return _oauth_error_response(exc)

    return JSONResponse(content=payload, headers={"Cache-Control": "no-store"})


class ConnectedAppRead(BaseModel):
    grant_id: str
    client_name: str
    scope: str
    created_at: Any
    last_used_at: Any


@router.get("/grants", response_model=list[ConnectedAppRead])
async def list_grants(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List the current user's active connector grants."""
    return await oauth_service.list_active_grants(db, user_id=current_user.id)


@router.delete("/grants/{grant_id}", status_code=204)
async def revoke_grant(
    grant_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Revoke one of the current user's connector grants."""
    removed = await oauth_service.revoke_grant_for_user(
        db, grant_id=grant_id, user_id=current_user.id
    )
    if not removed:
        return JSONResponse(status_code=404, content={"detail": "Grant not found"})
    return None
