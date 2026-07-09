"""Nojoin-driven ChatGPT (Codex) subscription OAuth — RFC 8628 device grant.

The Codex counterpart to :mod:`backend.services.cli_oauth.oauth` (Claude). Unlike
Claude Code, the Codex CLI supports a device-code sign-in (``codex login
--device-auth``, confirmed in the CX-0 spike), so Nojoin uses the standard device
authorization grant rather than the paste-back-code workaround: ``/start`` returns
a verification URL + user code, the user approves in a browser, and Nojoin polls
the token endpoint until it yields tokens.

The ephemeral device code lives in Redis with a short TTL (polled, not single
use); the resulting tokens are stored encrypted in ``CliOAuthCredential`` under
provider ``codex``.

Endpoints/params are the public Codex OAuth client (no secret). VERIFY the two
URLs against the live Codex flow before release — the device flow is the one part
of the connector proven by CX-0 desk research, not yet an end-to-end run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

import httpx

from backend.core.redis import get_redis_url
from backend.services.cli_oauth.oauth import CliOAuthExchangeError, OAuthTokens

# Public Codex CLI OAuth client (constant baked into the CLI; not a secret).
OPENAI_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
# Endpoints extracted from the codex binary. OpenAI's device flow is a CUSTOM
# protocol under /deviceauth/* (NOT RFC 8628), gated by the CLI's originator /
# User-Agent headers. NOT YET WORKING end-to-end: a bare POST to
# /deviceauth/usercode returns 405, so the exact start method/params are still
# unconfirmed. Completing this most likely means driving `codex login
# --device-auth` in worker-io (letting the CLI own the protocol) rather than
# reimplementing it here. See the connector notes.
DEVICE_AUTH_URL = "https://auth.openai.com/deviceauth/usercode"  # VERIFY (405 on POST)
DEVICE_TOKEN_URL = "https://auth.openai.com/deviceauth/token"  # VERIFY
TOKEN_URL = "https://auth.openai.com/oauth/token"  # refresh (confirmed in binary)
SCOPE = "openid profile email offline_access"
DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"

# The device endpoints reject requests without the CLI's identity headers (the
# original bare request 403'd). Best-effort match of the codex CLI.
_CLI_HEADERS = {
    "User-Agent": "codex_cli_rs",
    "originator": "codex_cli_rs",
}

_PENDING_TTL_SECONDS = 900  # device codes typically live ~15 minutes
_REQUEST_TIMEOUT_SECONDS = 30.0


class CliOAuthAuthorizationPending(CliOAuthExchangeError):
    """The user has not yet approved the device code.

    RFC 8628 ``authorization_pending``/``slow_down`` — a non-terminal signal that
    the caller should keep polling, distinct from a real exchange failure.
    """


@dataclass(frozen=True)
class DeviceCodeGrant:
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: Optional[str]
    expires_in: Optional[int]
    interval: Optional[int]


def _as_int_or_none(value: object) -> Optional[int]:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


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


async def request_device_code() -> DeviceCodeGrant:
    """Begin the device flow: ask OpenAI for a device + user code."""
    data = {"client_id": OPENAI_CLIENT_ID, "scope": SCOPE}
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                DEVICE_AUTH_URL,
                data=data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    **_CLI_HEADERS,
                },
            )
    except httpx.HTTPError as exc:
        raise CliOAuthExchangeError(
            f"Device authorization request failed: {exc}"
        ) from exc
    if response.status_code != 200:
        raise CliOAuthExchangeError(
            f"Device authorization returned HTTP {response.status_code}"
        )
    payload = response.json()
    device_code = payload.get("device_code")
    user_code = payload.get("user_code")
    verification_uri = payload.get("verification_uri") or payload.get(
        "verification_url"
    )
    if not (device_code and user_code and verification_uri):
        raise CliOAuthExchangeError("Device authorization response was incomplete")
    return DeviceCodeGrant(
        device_code=device_code,
        user_code=user_code,
        verification_uri=verification_uri,
        verification_uri_complete=payload.get("verification_uri_complete"),
        expires_in=_as_int_or_none(payload.get("expires_in")),
        interval=_as_int_or_none(payload.get("interval")),
    )


async def _post_token(data: dict[str, str], url: str) -> OAuthTokens:
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                url,
                data=data,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    **_CLI_HEADERS,
                },
            )
    except httpx.HTTPError as exc:
        raise CliOAuthExchangeError(f"Token endpoint request failed: {exc}") from exc

    if response.status_code == 200:
        return _tokens_from_response(response.json())

    # RFC 8628 returns the pending/slow_down signals as HTTP 400 with an error
    # slug; treat those as non-terminal so the caller keeps polling.
    error_slug = ""
    try:
        error_slug = str(response.json().get("error") or "")
    except Exception:  # noqa: BLE001 -- boundary: non-JSON error body
        error_slug = response.text[:200]
    if error_slug in ("authorization_pending", "slow_down"):
        raise CliOAuthAuthorizationPending(error_slug)
    raise CliOAuthExchangeError(
        f"Token exchange returned HTTP {response.status_code}: {error_slug}"
    )


async def poll_device_token(device_code: str) -> OAuthTokens:
    """Poll once for tokens; raises CliOAuthAuthorizationPending until approved."""
    return await _post_token(
        {
            "grant_type": DEVICE_GRANT_TYPE,
            "device_code": device_code,
            "client_id": OPENAI_CLIENT_ID,
        },
        DEVICE_TOKEN_URL,
    )


async def refresh_tokens(refresh_token: str) -> OAuthTokens:
    """Exchange a refresh token for a fresh access/refresh pair (rotating)."""
    return await _post_token(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": OPENAI_CLIENT_ID,
        },
        TOKEN_URL,
    )


# --- ephemeral device-code pending state (Redis, short TTL) ---


def _pending_key(user_id: int) -> str:
    return f"nojoin:cli_oauth:codex:device:{user_id}"


async def store_pending_device(user_id: int, grant: DeviceCodeGrant) -> None:
    import redis.asyncio as redis

    client = redis.from_url(get_redis_url())
    try:
        await client.set(
            _pending_key(user_id),
            json.dumps({"device_code": grant.device_code}),
            ex=_PENDING_TTL_SECONDS,
        )
    finally:
        await client.aclose()


async def get_pending_device(user_id: int) -> Optional[dict]:
    """Fetch the pending device state (kept until connected/expired — polled)."""
    import redis.asyncio as redis

    client = redis.from_url(get_redis_url())
    try:
        raw = await client.get(_pending_key(user_id))
    finally:
        await client.aclose()
    return json.loads(raw) if raw else None


async def clear_pending_device(user_id: int) -> None:
    import redis.asyncio as redis

    client = redis.from_url(get_redis_url())
    try:
        await client.delete(_pending_key(user_id))
    finally:
        await client.aclose()
