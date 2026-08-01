"""Bearer-token authentication for the mounted MCP endpoint.

Pure ASGI middleware so it can wrap the MCP SDK's Starlette app without
FastAPI dependency plumbing. Validation reuses
:func:`backend.api.deps.get_authenticated_token_details`, so ``mcp``-type
JWTs honour the same ``token_version`` and ``jti``-denylist containment as
browser sessions.

An unauthenticated request receives the RFC 9728 challenge pointing at the
protected-resource metadata, which is what lets MCP clients bootstrap the
whole OAuth flow from a bare URL.

Some clients (Codex Desktop) never start that flow from a 401, so when
``MCP_ANONYMOUS_DISCOVERY`` is enabled the middleware lets a fixed
allowlist of protocol-bootstrap JSON-RPC methods through anonymously —
``tools/call`` included, but only so the tool gate in
:mod:`backend.mcp_server.server` can answer it with an in-band
authentication challenge instead of an HTTP error. Everything else, and
every request the allowlist check cannot positively classify, still gets
the strict 401.
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
from backend.utils.config_manager import is_mcp_anonymous_discovery_enabled

logger = logging.getLogger(__name__)

# Set per-request by the middleware; read by tool implementations. Child
# tasks spawned while handling the request inherit the value.
current_mcp_user: ContextVar[Optional[User]] = ContextVar(
    "current_mcp_user", default=None
)

# The scopes carried by the request's access token. The endpoint gate is
# mcp:read; write tools check mcp:write themselves so read-only grants
# issued before the write scope existed keep working for every read tool.
current_mcp_scopes: ContextVar[frozenset[str]] = ContextVar(
    "current_mcp_scopes", default=frozenset()
)


def get_current_mcp_user() -> User:
    user = current_mcp_user.get()
    if user is None:
        raise RuntimeError("MCP tool invoked outside an authenticated request.")
    return user


def get_current_mcp_scopes() -> frozenset[str]:
    return current_mcp_scopes.get()


# JSON-RPC methods an anonymous request may reach when anonymous discovery
# is enabled. The first four are pure protocol bootstrap; tools/call passes
# only so the tool gate in server.py can answer it with an in-band
# challenge — no tool executes without an authenticated user.
_ANONYMOUS_JSONRPC_METHODS = frozenset(
    {"initialize", "notifications/initialized", "ping", "tools/list", "tools/call"}
)

# An anonymous body larger than this cannot be a legitimate handshake or
# discovery call; refuse to buffer it and fall back to the 401.
_ANONYMOUS_BODY_LIMIT_BYTES = 64 * 1024

_ANONYMOUS_RATE_LIMIT = 30
_ANONYMOUS_RATE_LIMIT_WINDOW_SECONDS = 60


def _challenge_header() -> dict[str, str]:
    from backend.api.services.oauth_service import DEFAULT_SCOPE, canonical_origin

    metadata_url = f"{canonical_origin()}/.well-known/oauth-protected-resource/mcp"
    challenge = f'Bearer resource_metadata="{metadata_url}"'
    if is_mcp_anonymous_discovery_enabled():
        # RFC 6750 scope hint; clients that key their consent request off
        # the challenge learn both scopes exist.
        challenge += f', scope="{DEFAULT_SCOPE}"'
    return {"WWW-Authenticate": challenge}


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


async def _read_bounded_body(receive: Receive) -> Optional[bytes]:
    """Buffer a request body up to the anonymous size cap.

    Returns None — meaning "cannot be classified, fail closed to the 401" —
    for oversized bodies and for anything other than a plain request stream.
    """
    chunks: list[bytes] = []
    received = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            return None
        chunk = message.get("body", b"")
        received += len(chunk)
        if received > _ANONYMOUS_BODY_LIMIT_BYTES:
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


class MCPAuthMiddleware:
    """Authenticate every HTTP request to the MCP mount."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def _dispatch_anonymous(
        self, scope: Scope, receive: Receive, send: Send
    ) -> bool:
        """Serve an allowlisted anonymous JSON-RPC request, or decline.

        Returns True when a response has been (or will be) sent — either the
        request was replayed downstream anonymously or it was rate-limited.
        Returns False in every case that cannot be positively classified as
        an allowlisted single JSON-RPC call (malformed JSON, batches,
        oversized bodies, disconnects), so the caller falls back to the 401
        challenge: the mode fails closed.
        """
        body = await _read_bounded_body(receive)
        if body is None:
            return False

        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        if not isinstance(payload, dict):
            return False
        if payload.get("method") not in _ANONYMOUS_JSONRPC_METHODS:
            return False

        # Imported lazily like the token path below, so importing this
        # module never drags Redis into contexts that only need the
        # contextvars.
        from starlette.requests import Request

        from backend.utils.rate_limit import enforce_rate_limit

        try:
            await enforce_rate_limit(
                Request(scope),
                namespace="mcp-anonymous",
                limit=_ANONYMOUS_RATE_LIMIT,
                window_seconds=_ANONYMOUS_RATE_LIMIT_WINDOW_SECONDS,
                detail="Too many anonymous MCP requests. Please try again later.",
            )
        except HTTPException as exc:
            await _send_json_error(
                send,
                status_code=exc.status_code,
                detail=str(exc.detail),
                headers={**_challenge_header(), **(exc.headers or {})},
            )
            return True

        sent_body = False

        async def replay() -> dict:
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        # Explicitly pin the anonymous identity for the downstream call so a
        # reused task can never leak a previous request's user into it.
        context_token = current_mcp_user.set(None)
        scopes_token = current_mcp_scopes.set(frozenset())
        try:
            await self.app(scope, replay, send)
        finally:
            current_mcp_scopes.reset(scopes_token)
            current_mcp_user.reset(context_token)
        return True

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
            # Anonymous compatibility path (Codex Desktop cannot start OAuth
            # from a 401): allow only the bootstrap allowlist through, and
            # only when the operator has not switched the mode off. GET (the
            # SSE stream) and everything unclassifiable stays a strict 401.
            if (
                is_mcp_anonymous_discovery_enabled()
                and scope.get("method") == "POST"
                and await self._dispatch_anonymous(scope, receive, send)
            ):
                return
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
            # The challenge must win the header merge: deps attaches a bare
            # "WWW-Authenticate: Bearer" which would otherwise replace the
            # resource_metadata pointer an expired token needs to recover.
            await _send_json_error(
                send,
                status_code=exc.status_code,
                detail=str(exc.detail),
                headers={**(exc.headers or {}), **_challenge_header()},
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

        token_scopes = payload.get("scopes")
        granted_scopes = frozenset(
            item for item in (token_scopes or []) if isinstance(item, str)
        )

        context_token = current_mcp_user.set(user)
        scopes_token = current_mcp_scopes.set(granted_scopes)
        try:
            await self.app(scope, receive, send)
        finally:
            current_mcp_scopes.reset(scopes_token)
            current_mcp_user.reset(context_token)
