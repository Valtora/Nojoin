"""Nojoin-driven Claude subscription OAuth (PKCE), server-side.

Reimplements the Claude Code ``setup-token`` authorize+exchange (scope
``user:inference``) so Nojoin can drive a Grant -> paste-code UX without holding
a stateful CLI subprocess. The ephemeral PKCE verifier + CSRF state live in
Redis with a short TTL; the resulting tokens are stored encrypted in
``CliOAuthCredential``.

Endpoint/params captured from the Claude Code CLI (public client, no secret).
The exchange yields an ~8h ``access_token`` plus a rotating ``refresh_token`` —
see ``refresh_tokens`` — not the ~1y ``setup-token``. The token endpoint is
slow (tens of seconds), hence the generous timeout.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from backend.core.redis import get_redis_url

# Public Claude Code OAuth client (constant baked into the CLI; not a secret).
ANTHROPIC_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
AUTHORIZE_URL = "https://claude.com/cai/oauth/authorize"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
REDIRECT_URI = "https://platform.claude.com/oauth/code/callback"
SCOPE = "user:inference"

_PENDING_TTL_SECONDS = 600
_EXCHANGE_TIMEOUT_SECONDS = 120.0


class CliOAuthExchangeError(RuntimeError):
    """Raised when the authorize-code or refresh-token exchange fails."""


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: Optional[str]
    expires_in: Optional[int]
    scope: Optional[str]


def generate_pkce() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` for the S256 PKCE method."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def generate_state() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(24)).rstrip(b"=").decode()


def build_authorize_url(challenge: str, state: str) -> str:
    params = {
        # code=true selects headless/manual-code-entry: the callback page shows
        # the code for the user to copy rather than round-tripping to a server.
        "code": "true",
        "client_id": ANTHROPIC_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def parse_pasted_code(raw: str) -> tuple[str, Optional[str]]:
    """Extract ``(code, state)`` from whatever the user pastes.

    Tolerates a bare code, a ``code#state`` string, or a full callback URL /
    query string, so the modal is forgiving about exactly what was copied.
    """
    raw = (raw or "").strip()
    if not raw:
        return "", None
    if "code=" in raw:
        query = urlparse(raw).query or raw.split("?", 1)[-1]
        parsed = parse_qs(query)
        code = (parsed.get("code") or [""])[0]
        state = (parsed.get("state") or [None])[0]
        return code, state
    if "#" in raw:
        code, _, state = raw.partition("#")
        return code, state or None
    return raw, None


def _tokens_from_response(payload: dict) -> OAuthTokens:
    access_token = payload.get("access_token")
    if not access_token:
        raise CliOAuthExchangeError("Token response did not include an access_token")
    expires_in = payload.get("expires_in")
    return OAuthTokens(
        access_token=access_token,
        refresh_token=payload.get("refresh_token"),
        expires_in=int(expires_in) if expires_in is not None else None,
        scope=payload.get("scope"),
    )


async def _post_token(data: dict[str, str]) -> OAuthTokens:
    try:
        async with httpx.AsyncClient(timeout=_EXCHANGE_TIMEOUT_SECONDS) as client:
            response = await client.post(
                TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError as exc:
        raise CliOAuthExchangeError(f"Token endpoint request failed: {exc}") from exc

    if response.status_code != 200:
        # Surface the OAuth error slug (e.g. invalid_grant) without the body's
        # noise; the code is single-use and expires within ~60s.
        detail = ""
        try:
            body = response.json()
            detail = str(body.get("error") or body.get("error_description") or body)
        except Exception:  # noqa: BLE001 -- boundary: non-JSON error body
            detail = response.text[:200]
        raise CliOAuthExchangeError(
            f"Token exchange returned HTTP {response.status_code}: {detail}"
        )
    return _tokens_from_response(response.json())


async def exchange_code(code: str, verifier: str, state: str) -> OAuthTokens:
    """Exchange an authorization code + PKCE verifier for tokens."""
    return await _post_token(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": ANTHROPIC_CLIENT_ID,
            "code_verifier": verifier,
            "state": state,
        }
    )


async def refresh_tokens(refresh_token: str) -> OAuthTokens:
    """Exchange a refresh token for a fresh access/refresh pair (rotating)."""
    return await _post_token(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": ANTHROPIC_CLIENT_ID,
        }
    )


# --- ephemeral PKCE pending state (Redis, short TTL) ---


def _pending_key(user_id: int) -> str:
    return f"nojoin:cli_oauth:pkce:{user_id}"


async def store_pending_pkce(user_id: int, verifier: str, state: str) -> None:
    import redis.asyncio as redis

    client = redis.from_url(get_redis_url())
    try:
        await client.set(
            _pending_key(user_id),
            json.dumps({"verifier": verifier, "state": state}),
            ex=_PENDING_TTL_SECONDS,
        )
    finally:
        await client.aclose()


async def pop_pending_pkce(user_id: int) -> Optional[dict]:
    """Fetch and delete the pending PKCE state (single use)."""
    import redis.asyncio as redis

    client = redis.from_url(get_redis_url())
    try:
        key = _pending_key(user_id)
        raw = await client.get(key)
        await client.delete(key)
    finally:
        await client.aclose()
    if not raw:
        return None
    return json.loads(raw)
