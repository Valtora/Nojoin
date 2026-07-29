"""ChatGPT (Codex) subscription support for CLI OAuth.

Connect is DRIVEN via the codex CLI itself (`codex login --device-auth` under a
pty in worker-io — see :mod:`backend.processing.cli.codex_login` and
``codex_device_login_task``), because OpenAI's device flow is an undocumented,
header-gated protocol a bare httpx client cannot reproduce. This module holds:

- the Redis "login state" channel the worker publishes to and the API reads, so
  the browser can show the verification URL + code and learn when it connects;
- a token-refresh helper against the confirmed ``oauth/token`` endpoint, kept as
  a fallback (the CLI normally refreshes ``auth.json`` in place during inference).
"""

from __future__ import annotations

import json
from typing import Optional

import httpx

from backend.core.redis import get_redis_url
from backend.services.cli_oauth.oauth import CliOAuthExchangeError, OAuthTokens

# Public Codex CLI OAuth client (constant baked into the CLI; not a secret).
OPENAI_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
TOKEN_URL = "https://auth.openai.com/oauth/token"  # refresh (confirmed in binary)

_STATE_TTL_SECONDS = 1000  # a little over the code's ~15-minute lifetime
_REQUEST_TIMEOUT_SECONDS = 30.0

# Device-login state values (worker publishes, API reads).
STATUS_AWAITING = "awaiting"
STATUS_CONNECTED = "connected"
STATUS_FAILED = "failed"


async def refresh_tokens(refresh_token: str) -> OAuthTokens:
    """Exchange a refresh token for a fresh access/refresh pair.

    Fallback path — the CLI normally refreshes ``auth.json`` itself during
    inference; the manager's codex branch does not call this.
    """
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": OPENAI_CLIENT_ID,
    }
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError as exc:
        raise CliOAuthExchangeError(f"Token endpoint request failed: {exc}") from exc
    if response.status_code != 200:
        raise CliOAuthExchangeError(
            f"Token refresh returned HTTP {response.status_code}"
        )
    payload = response.json()
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


# --- device-login state channel (Redis) ---
# The worker publishes progress (awaiting -> connected/failed); the API reads it
# so the browser can show the verification URL + code and detect completion.


def _login_key(user_id: int) -> str:
    return f"nojoin:cli_oauth:codex:login:{user_id}"


def publish_login_state(user_id: int, state: dict) -> None:
    """Worker side (sync): set the current device-login state."""
    import redis as sync_redis

    client = sync_redis.from_url(get_redis_url(), decode_responses=True)
    try:
        client.set(_login_key(user_id), json.dumps(state), ex=_STATE_TTL_SECONDS)
    finally:
        client.close()


async def read_login_state(user_id: int) -> Optional[dict]:
    """API side (async): the latest device-login state, or None."""
    import redis.asyncio as redis

    client = redis.from_url(get_redis_url(), decode_responses=True)
    try:
        raw = await client.get(_login_key(user_id))
    finally:
        await client.aclose()
    return json.loads(raw) if raw else None


async def clear_login_state(user_id: int) -> None:
    import redis.asyncio as redis

    client = redis.from_url(get_redis_url(), decode_responses=True)
    try:
        await client.delete(_login_key(user_id))
    finally:
        await client.aclose()


# --- Codex model catalogue cache (Redis) ---
# `codex debug models` (run in worker-io) is cached here; the API serves it to
# the model picker. Refreshed on a cache miss + a long TTL, since the catalogue
# only changes when the codex binary is updated.

_CATALOG_KEY = "nojoin:cli_oauth:codex:models"
_CATALOG_TTL_SECONDS = 21600  # 6 hours


def publish_model_catalog(models: list) -> None:
    """Worker side (sync): cache the fetched model catalogue."""
    import redis as sync_redis

    client = sync_redis.from_url(get_redis_url(), decode_responses=True)
    try:
        client.set(_CATALOG_KEY, json.dumps(models), ex=_CATALOG_TTL_SECONDS)
    finally:
        client.close()


async def read_model_catalog() -> Optional[list]:
    """API side (async): the cached model catalogue, or None."""
    import redis.asyncio as redis

    client = redis.from_url(get_redis_url(), decode_responses=True)
    try:
        raw = await client.get(_CATALOG_KEY)
    finally:
        await client.aclose()
    return json.loads(raw) if raw else None


async def clear_model_catalog() -> None:
    """API side (async): drop the cached catalogue so the next read is live.

    Used by the manual refresh. Without the delete, a stale-but-present cache
    would keep being served for up to six hours and the button would look like
    it did nothing.
    """
    import redis.asyncio as redis

    client = redis.from_url(get_redis_url(), decode_responses=True)
    try:
        await client.delete(_CATALOG_KEY)
    finally:
        await client.aclose()
