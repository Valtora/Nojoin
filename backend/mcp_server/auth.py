"""Bearer-token authentication for the mounted MCP endpoint.

Pure ASGI middleware so it can wrap the MCP SDK's Starlette app without
FastAPI dependency plumbing. Validation reuses
:func:`backend.api.deps.get_authenticated_token_details`, so ``mcp``-type
JWTs honour the same ``token_version`` and ``jti``-denylist containment as
browser sessions.

An unauthenticated request receives the RFC 9728 challenge pointing at the
protected-resource metadata, which is what lets MCP clients bootstrap the
whole OAuth flow from a bare URL.
"""

import json
import logging
from contextvars import ContextVar
from typing import Optional

from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from backend.core.security import MCP_READ_SCOPE, MCP_TOKEN_TYPE
from backend.models.user import User

logger = logging.getLogger(__name__)

# Set per-request by the middleware; read by tool implementations. Child
# tasks spawned while handling the request inherit the value.
current_mcp_user: ContextVar[Optional[User]] = ContextVar(
    "current_mcp_user", default=None
)


def get_current_mcp_user() -> User:
    user = current_mcp_user.get()
    if user is None:
        raise RuntimeError("MCP tool invoked outside an authenticated request.")
    return user


def _challenge_header() -> dict[str, str]:
    from backend.api.services.oauth_service import canonical_origin

    metadata_url = f"{canonical_origin()}/.well-known/oauth-protected-resource/mcp"
    return {"WWW-Authenticate": f'Bearer resource_metadata="{metadata_url}"'}


async def _send_json_error(
    send: Send, *, status_code: int, detail: str, headers: dict[str, str]
) -> None:
    body = json.dumps({"detail": detail}).encode("utf-8")
    raw_headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    raw_headers.extend(
        (key.lower().encode("ascii"), value.encode("latin-1"))
        for key, value in headers.items()
    )
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": raw_headers,
        }
    )
    await send({"type": "http.response.body", "body": body})


class MCPAuthMiddleware:
    """Authenticate every HTTP request to the MCP mount."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        authorization = headers.get("authorization")
        token = None
        if authorization:
            scheme, _, candidate = authorization.partition(" ")
            if scheme.lower() == "bearer" and candidate.strip():
                token = candidate.strip()

        if not token:
            await _send_json_error(
                send,
                status_code=401,
                detail="Not authenticated",
                headers=_challenge_header(),
            )
            return

        # Imported lazily so importing this module never drags the DB layer
        # into contexts (tests, tooling) that only need the contextvar.
        from backend.api.deps import get_authenticated_token_details
        from backend.api.services.oauth_service import mcp_resource_url
        from backend.core.db import async_session_maker

        try:
            async with async_session_maker() as db:
                user, payload = await get_authenticated_token_details(
                    db,
                    token,
                    allowed_token_types={MCP_TOKEN_TYPE},
                    required_scopes_by_type={MCP_TOKEN_TYPE: {MCP_READ_SCOPE}},
                )
        except HTTPException as exc:
            await _send_json_error(
                send,
                status_code=exc.status_code,
                detail=str(exc.detail),
                headers={**_challenge_header(), **(exc.headers or {})},
            )
            return

        token_resource = payload.get("res")
        if token_resource != mcp_resource_url():
            await _send_json_error(
                send,
                status_code=401,
                detail="Token was not issued for this resource.",
                headers=_challenge_header(),
            )
            return

        context_token = current_mcp_user.set(user)
        try:
            await self.app(scope, receive, send)
        finally:
            current_mcp_user.reset(context_token)
