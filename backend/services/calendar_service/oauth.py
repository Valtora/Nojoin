"""OAuth authorisation, token exchange/refresh, identity, and access tokens.

Handles the provider-side OAuth 2.1 + PKCE dance and per-connection access-token
lifecycle. ``HTTPException`` is imported lazily because the Celery worker image
ships no web framework; it is raised only from the API-facing authorisation
entry point, never from the worker sync path.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta
from http import HTTPStatus
from typing import Any
from urllib.parse import urlencode

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.encryption import encrypt_secret
from backend.core.redis import REDIS_URL
from backend.models.calendar import CalendarConnection, CalendarProvider
from backend.models.user import User

from .config import _load_connection_token_bundle, get_provider_runtime_config
from .constants import (
    GOOGLE_AUTH_URL,
    GOOGLE_SCOPE,
    GOOGLE_TOKEN_URL,
    GOOGLE_USERINFO_URL,
    MICROSOFT_COMMON_TENANT,
    MICROSOFT_GRAPH_URL,
    MICROSOFT_SCOPE,
    OAUTH_STATE_TTL_SECONDS,
    PROVIDER_DISPLAY_NAMES,
)
from .models_dto import ProviderIdentity, ProviderRuntimeConfig, TokenBundle
from .text_utils import _request_json, _utc_now
from .urls import _build_redirect_uri

# HTTPException is raised only from this module's API-facing helpers, never from
# the sync/reconcile path the Celery worker runs. The worker image ships no web
# framework, so import it lazily and tolerate its absence there.
try:
    from fastapi import HTTPException
except ModuleNotFoundError:  # pragma: no cover - worker image has no fastapi
    HTTPException = None  # type: ignore[assignment, misc]

_oauth_state_fallback: dict[str, tuple[datetime, dict[str, Any]]] = {}


def _build_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


async def _save_oauth_state(state: str, payload: dict[str, Any]) -> None:
    expires_at = _utc_now() + timedelta(seconds=OAUTH_STATE_TTL_SECONDS)
    try:
        client = redis.from_url(REDIS_URL)
        try:
            await client.set(
                f"nojoin:calendar:oauth:{state}",
                json.dumps(payload),
                ex=OAUTH_STATE_TTL_SECONDS,
            )
        finally:
            # from_url allocates a fresh connection pool per call, so close it
            # even when set() raises, otherwise a Redis outage on the connect
            # path leaks a pool per authorisation attempt.
            await client.close()
        return
    except Exception:  # noqa: BLE001
        pass

    _oauth_state_fallback[state] = (expires_at, payload)


async def _pop_oauth_state(state: str) -> dict[str, Any] | None:
    try:
        client = redis.from_url(REDIS_URL)
        try:
            key = f"nojoin:calendar:oauth:{state}"
            stored = await client.get(key)
            if stored is not None:
                await client.delete(key)
                return json.loads(stored)
        finally:
            # Close the pool even when get()/delete() raise (see _save_oauth_state).
            await client.close()
    except Exception:  # noqa: BLE001
        pass

    expires_at, payload = _oauth_state_fallback.pop(state, (None, None))
    if expires_at and expires_at > _utc_now() and payload is not None:
        return payload
    return None


async def start_authorisation(db: AsyncSession, provider: str, user: User) -> str:
    runtime_config = await get_provider_runtime_config(db, provider)
    if not runtime_config.configured:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST,
            detail=f"{PROVIDER_DISPLAY_NAMES[provider]} calendar integration is not configured",
        )

    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(72)
    await _save_oauth_state(
        state,
        {
            "user_id": user.id,
            "provider": provider,
            "code_verifier": code_verifier,
        },
    )

    redirect_uri = _build_redirect_uri(provider)
    common_params = {
        "client_id": runtime_config.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
        "code_challenge": _build_code_challenge(code_verifier),
        "code_challenge_method": "S256",
    }

    if provider == CalendarProvider.GOOGLE.value:
        params = {
            **common_params,
            "scope": GOOGLE_SCOPE,
            "access_type": "offline",
            "prompt": "select_account consent",
            "include_granted_scopes": "true",
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    tenant_id = runtime_config.tenant_id or MICROSOFT_COMMON_TENANT
    params = {
        **common_params,
        "scope": MICROSOFT_SCOPE,
        "response_mode": "query",
        "prompt": "select_account",
    }
    auth_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize"
    return f"{auth_url}?{urlencode(params)}"


async def _exchange_google_code(
    runtime_config: ProviderRuntimeConfig, code: str, code_verifier: str
) -> TokenBundle:
    token_data = await _request_json(
        "POST",
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": runtime_config.client_id,
            "client_secret": runtime_config.client_secret,
            "redirect_uri": _build_redirect_uri(CalendarProvider.GOOGLE.value),
            "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        },
    )
    expires_in = token_data.get("expires_in")
    scopes = str(token_data.get("scope", GOOGLE_SCOPE)).split()
    return TokenBundle(
        access_token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token"),
        expires_at=_utc_now() + timedelta(seconds=int(expires_in))
        if expires_in
        else None,
        scopes=scopes,
    )


async def _exchange_microsoft_code(
    runtime_config: ProviderRuntimeConfig, code: str, code_verifier: str
) -> TokenBundle:
    tenant_id = runtime_config.tenant_id or MICROSOFT_COMMON_TENANT
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    token_data = await _request_json(
        "POST",
        token_url,
        data={
            "client_id": runtime_config.client_id,
            "client_secret": runtime_config.client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _build_redirect_uri(CalendarProvider.MICROSOFT.value),
            "scope": MICROSOFT_SCOPE,
            "code_verifier": code_verifier,
        },
    )
    expires_in = token_data.get("expires_in")
    scopes = str(token_data.get("scope", MICROSOFT_SCOPE)).split()
    return TokenBundle(
        access_token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token"),
        expires_at=_utc_now() + timedelta(seconds=int(expires_in))
        if expires_in
        else None,
        scopes=scopes,
    )


async def _refresh_google_access_token(
    runtime_config: ProviderRuntimeConfig, refresh_token: str
) -> TokenBundle:
    token_data = await _request_json(
        "POST",
        GOOGLE_TOKEN_URL,
        data={
            "client_id": runtime_config.client_id,
            "client_secret": runtime_config.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
    )
    expires_in = token_data.get("expires_in")
    scopes = str(token_data.get("scope", GOOGLE_SCOPE)).split()
    return TokenBundle(
        access_token=token_data["access_token"],
        refresh_token=refresh_token,
        expires_at=_utc_now() + timedelta(seconds=int(expires_in))
        if expires_in
        else None,
        scopes=scopes,
    )


async def _refresh_microsoft_access_token(
    runtime_config: ProviderRuntimeConfig, refresh_token: str
) -> TokenBundle:
    tenant_id = runtime_config.tenant_id or MICROSOFT_COMMON_TENANT
    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    token_data = await _request_json(
        "POST",
        token_url,
        data={
            "client_id": runtime_config.client_id,
            "client_secret": runtime_config.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": MICROSOFT_SCOPE,
        },
    )
    expires_in = token_data.get("expires_in")
    scopes = str(token_data.get("scope", MICROSOFT_SCOPE)).split()
    return TokenBundle(
        access_token=token_data["access_token"],
        refresh_token=token_data.get("refresh_token") or refresh_token,
        expires_at=_utc_now() + timedelta(seconds=int(expires_in))
        if expires_in
        else None,
        scopes=scopes,
    )


async def _fetch_google_identity(access_token: str) -> ProviderIdentity:
    identity_data = await _request_json(
        "GET",
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    return ProviderIdentity(
        account_id=str(identity_data["sub"]),
        email=identity_data.get("email"),
        display_name=identity_data.get("name") or identity_data.get("email"),
    )


async def _fetch_microsoft_identity(access_token: str) -> ProviderIdentity:
    identity_data = await _request_json(
        "GET",
        f"{MICROSOFT_GRAPH_URL}/me",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        params={"$select": "id,displayName,mail,userPrincipalName"},
    )
    return ProviderIdentity(
        account_id=str(identity_data["id"]),
        email=identity_data.get("mail") or identity_data.get("userPrincipalName"),
        display_name=identity_data.get("displayName") or identity_data.get("mail"),
    )


async def _get_access_token_for_connection(
    db: AsyncSession,
    connection: CalendarConnection,
    runtime_config: ProviderRuntimeConfig,
) -> str:
    access_token, refresh_token = _load_connection_token_bundle(connection)
    now = _utc_now()
    if (
        access_token
        and connection.token_expires_at
        and connection.token_expires_at > now + timedelta(minutes=2)
    ):
        return access_token
    if access_token and connection.token_expires_at is None:
        return access_token
    if not refresh_token:
        raise ValueError("Calendar connection requires reauthorisation")

    if connection.provider == CalendarProvider.GOOGLE.value:
        refreshed = await _refresh_google_access_token(runtime_config, refresh_token)
    else:
        refreshed = await _refresh_microsoft_access_token(runtime_config, refresh_token)

    connection.access_token_encrypted = encrypt_secret(refreshed.access_token)
    connection.refresh_token_encrypted = (
        encrypt_secret(refreshed.refresh_token)
        if refreshed.refresh_token
        else connection.refresh_token_encrypted
    )
    connection.token_expires_at = refreshed.expires_at
    connection.granted_scopes = refreshed.scopes
    db.add(connection)
    await db.commit()
    return refreshed.access_token
