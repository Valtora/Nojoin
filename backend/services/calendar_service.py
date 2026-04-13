from __future__ import annotations

import base64
import calendar as month_calendar
import html
import hashlib
import json
import logging
import os
import re
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import quote, urlencode, urlparse

import httpx
import redis.asyncio as redis
import sqlalchemy as sa
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import delete, select

from backend.core.db import async_session_maker
from backend.core.encryption import decrypt_secret, encrypt_secret
from backend.models.calendar import (
    CalendarConnection,
    CalendarConnectionRead,
    CalendarDashboardDayCountRead,
    CalendarDashboardEventRead,
    CalendarDashboardState,
    CalendarDashboardSummaryRead,
    CalendarOverviewRead,
    CalendarProvider,
    CalendarProviderAvailabilityRead,
    CalendarProviderConfig,
    CalendarProviderStatusRead,
    CalendarSelectionUpdate,
    CalendarSource,
    CalendarSourceColourUpdate,
    CalendarSourceRead,
    CalendarSyncStatus,
    CalendarEvent,
)
from backend.models.user import User
from backend.utils.config_manager import get_trusted_web_origin
from backend.utils.time import utc_now
from backend.utils.timezones import (
    get_timezone,
    get_user_timezone_name,
    today_in_timezone,
    utc_naive_to_aware,
    utc_naive_to_timezone,
)


logger = logging.getLogger(__name__)

GOOGLE_SCOPE = "openid email profile https://www.googleapis.com/auth/calendar.readonly"
MICROSOFT_SCOPE = "openid profile email offline_access User.Read Calendars.Read"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_CALENDAR_LIST_URL = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
GOOGLE_EVENTS_URL_TEMPLATE = "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
MICROSOFT_GRAPH_URL = "https://graph.microsoft.com/v1.0"
MICROSOFT_COMMON_TENANT = "common"
OAUTH_STATE_TTL_SECONDS = 10 * 60
SYNC_WINDOW_MONTHS_BACK = 12
SYNC_WINDOW_MONTHS_FORWARD = 12
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

PROVIDER_DISPLAY_NAMES = {
    CalendarProvider.GOOGLE.value: "Google",
    CalendarProvider.MICROSOFT.value: "Microsoft",
}

PROVIDER_ENV_KEYS = {
    CalendarProvider.GOOGLE.value: {
        "client_id": "GOOGLE_OAUTH_CLIENT_ID",
        "client_secret": "GOOGLE_OAUTH_CLIENT_SECRET",
        "tenant_id": None,
    },
    CalendarProvider.MICROSOFT.value: {
        "client_id": "MICROSOFT_OAUTH_CLIENT_ID",
        "client_secret": "MICROSOFT_OAUTH_CLIENT_SECRET",
        "tenant_id": "MICROSOFT_OAUTH_TENANT_ID",
    },
}

_oauth_state_fallback: dict[str, tuple[datetime, dict[str, Any]]] = {}

TRAILING_URL_PUNCTUATION = ".,);]>"
HREF_URL_PATTERN = re.compile(r'href=["\'](https?://[^"\']+)["\']', re.IGNORECASE)
PLAIN_URL_PATTERN = re.compile(r'https?://[^\s<>"\']+')
MEETING_URL_HOST_PRIORITY = (
    "teams.microsoft.com",
    "meet.google.com",
    ".zoom.us",
    ".webex.com",
    ".gotomeeting.com",
    ".bluejeans.com",
    ".whereby.com",
    ".ringcentral.com",
    "meet.jit.si",
)
TRUSTED_MEETING_HOSTS = {
    "meet.google.com",
    "teams.microsoft.com",
    "teams.live.com",
    "zoom.us",
}
TRUSTED_MEETING_HOST_SUFFIXES = (
    ".zoom.us",
)
MICROSOFT_EVENT_SELECT = ",".join(
    [
        "id",
        "subject",
        "type",
        "seriesMasterId",
        "isAllDay",
        "isCancelled",
        "start",
        "end",
        "lastModifiedDateTime",
        "webLink",
        "location",
        "locations",
        "body",
        "bodyPreview",
        "onlineMeeting",
        "onlineMeetingUrl",
    ]
)


@dataclass
class ProviderRuntimeConfig:
    provider: str
    client_id: str | None
    client_secret: str | None
    tenant_id: str | None
    enabled: bool
    source: str

    @property
    def configured(self) -> bool:
        return self.enabled and bool(self.client_id and self.client_secret)


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    scopes: list[str]


@dataclass
class ProviderIdentity:
    account_id: str
    email: str | None
    display_name: str | None


@dataclass
class ProviderCalendarRecord:
    remote_id: str
    name: str
    description: str | None
    time_zone: str | None
    colour: str | None
    is_primary: bool
    is_read_only: bool


@dataclass
class ProviderEventRecord:
    remote_id: str
    title: str
    status: str
    is_all_day: bool
    starts_at: datetime | None
    ends_at: datetime | None
    start_date: date | None
    end_date: date | None
    source_url: str | None = None
    location_text: str | None = None
    meeting_url: str | None = None
    external_updated_at: datetime | None = None


@dataclass
class ProviderEventSyncResult:
    events: list[ProviderEventRecord]
    deleted_remote_ids: list[str]
    cursor: str | None


class IncrementalSyncResetRequired(Exception):
    pass


def _utc_now() -> datetime:
    return utc_now()


def _parse_iso_datetime(value: str | None, *, default_tz: timezone | None = timezone.utc) -> datetime | None:
    if not value:
        return None
    normalised = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalised)
    if parsed.tzinfo is None:
        if default_tz is None:
            return parsed
        parsed = parsed.replace(tzinfo=default_tz)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _clean_error_text(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = " ".join(str(value).split())
    return cleaned[:500] if cleaned else None


def _normalise_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(html.unescape(str(value)).replace("\xa0", " ").split())
    return cleaned or None


def _normalise_colour_value(value: str | None) -> str | None:
    cleaned = _normalise_text(value)
    if not cleaned:
        return None
    return cleaned.lower()


def _clean_url(value: str | None) -> str | None:
    cleaned = _normalise_text(value)
    if not cleaned:
        return None
    while cleaned and cleaned[-1] in TRAILING_URL_PUNCTUATION:
        cleaned = cleaned[:-1]
    if not cleaned:
        return None
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return cleaned
    return None


def _extract_urls_from_text(value: str | None) -> list[str]:
    if not value:
        return []

    text = html.unescape(str(value))
    seen: set[str] = set()
    urls: list[str] = []
    for candidate in [*HREF_URL_PATTERN.findall(text), *PLAIN_URL_PATTERN.findall(text)]:
        cleaned = _clean_url(candidate)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            urls.append(cleaned)
    return urls


def _meeting_url_rank(url: str) -> int:
    hostname = (urlparse(url).hostname or "").lower()
    if not hostname:
        return len(MEETING_URL_HOST_PRIORITY) + 1

    for index, candidate in enumerate(MEETING_URL_HOST_PRIORITY):
        suffix = candidate.lstrip(".")
        if hostname == suffix or hostname.endswith(candidate):
            return index
    return len(MEETING_URL_HOST_PRIORITY)


def _get_meeting_url_host(url: str | None) -> str | None:
    if not url:
        return None

    hostname = urlparse(url).hostname
    if not hostname:
        return None

    return hostname.lower()


def _is_trusted_meeting_url(url: str | None) -> bool:
    hostname = _get_meeting_url_host(url)
    if not hostname:
        return False

    return hostname in TRUSTED_MEETING_HOSTS or any(
        hostname.endswith(suffix)
        for suffix in TRUSTED_MEETING_HOST_SUFFIXES
    )


def _pick_preferred_meeting_url(*values: str | None) -> str | None:
    seen: set[str] = set()
    urls: list[str] = []

    for value in values:
        direct_url = _clean_url(value)
        if direct_url and direct_url not in seen:
            seen.add(direct_url)
            urls.append(direct_url)

        for extracted_url in _extract_urls_from_text(value):
            if extracted_url not in seen:
                seen.add(extracted_url)
                urls.append(extracted_url)

    if not urls:
        return None

    return min(
        enumerate(urls),
        key=lambda item: (_meeting_url_rank(item[1]), item[0]),
    )[1]


def _get_microsoft_location_text(item: dict[str, Any]) -> str | None:
    location = item.get("location")
    if isinstance(location, dict):
        cleaned_location = _normalise_text(location.get("displayName"))
        if cleaned_location:
            return cleaned_location

    for location_item in item.get("locations") or []:
        if not isinstance(location_item, dict):
            continue
        cleaned_location = _normalise_text(location_item.get("displayName"))
        if cleaned_location:
            return cleaned_location

    return None


def _is_partial_microsoft_occurrence(item: dict[str, Any]) -> bool:
    if item.get("type") != "occurrence" or not item.get("seriesMasterId"):
        return False

    return not any(
        [
            item.get("subject"),
            item.get("webLink"),
            item.get("bodyPreview"),
            item.get("onlineMeetingUrl"),
            item.get("onlineMeeting"),
            item.get("location"),
            item.get("locations"),
        ]
    )


def _extract_error_text_from_payload(payload: Any) -> str | None:
    if isinstance(payload, str):
        return _clean_error_text(payload)
    if isinstance(payload, dict):
        direct_message = payload.get("error_description") or payload.get("message") or payload.get("detail")
        cleaned_direct_message = _clean_error_text(direct_message)
        if cleaned_direct_message:
            return cleaned_direct_message

        nested_error = payload.get("error")
        if isinstance(nested_error, str):
            return _clean_error_text(nested_error)
        if isinstance(nested_error, dict):
            nested_message = nested_error.get("message") or nested_error.get("description") or nested_error.get("detail")
            cleaned_nested_message = _clean_error_text(nested_message)
            if cleaned_nested_message:
                return cleaned_nested_message
    return None


def _extract_http_error_text(exc: httpx.HTTPStatusError) -> str:
    response = exc.response
    try:
        payload = response.json()
    except ValueError:
        payload = None

    payload_message = _extract_error_text_from_payload(payload)
    if payload_message:
        return payload_message

    response_text = _clean_error_text(response.text)
    if response_text:
        return response_text

    return f"Provider returned HTTP {response.status_code}"


def _classify_sync_failure(exc: Exception) -> tuple[str, str]:
    default_message = _clean_error_text(str(exc)) or "Calendar sync failed"
    lower_message = default_message.lower()

    if isinstance(exc, ValueError) and (
        "reauthorisation" in lower_message or "reauthorization" in lower_message
    ):
        return (
            CalendarSyncStatus.REAUTHORISATION_REQUIRED.value,
            "This calendar account needs to be reconnected before it can sync again.",
        )

    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        http_message = _extract_http_error_text(exc)
        lower_http_message = http_message.lower()
        auth_hints = (
            "invalid_grant",
            "invalid_token",
            "access_denied",
            "access is denied",
            "admin approval",
            "consent",
            "insufficient privileges",
            "interaction_required",
            "login_required",
            "unauthorised",
            "unauthorized",
            "forbidden",
            "reauthorisation",
            "reauthorization",
        )
        is_auth_error = status_code == 401 or any(hint in lower_http_message for hint in auth_hints)
        if status_code == 403 and any(
            hint in lower_http_message
            for hint in ("admin approval", "consent", "insufficient privileges", "access is denied", "forbidden")
        ):
            return (
                CalendarSyncStatus.REAUTHORISATION_REQUIRED.value,
                "This calendar account needs admin approval or renewed consent before it can sync again.",
            )
        if is_auth_error:
            return (
                CalendarSyncStatus.REAUTHORISATION_REQUIRED.value,
                "This calendar account needs to be reconnected before it can sync again.",
            )
        return (CalendarSyncStatus.ERROR.value, http_message)

    return (CalendarSyncStatus.ERROR.value, default_message)


def _add_months(value: datetime, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, month_calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _start_of_month(value: datetime) -> datetime:
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _build_sync_window(reference: datetime | None = None) -> tuple[datetime, datetime]:
    base = _start_of_month(reference or _utc_now())
    window_start = _add_months(base, -SYNC_WINDOW_MONTHS_BACK)
    window_end = _add_months(base, SYNC_WINDOW_MONTHS_FORWARD + 1)
    return window_start, window_end


def _build_redirect_uri(provider: str) -> str:
    return f"{get_trusted_web_origin()}/api/v1/calendar/oauth/{provider}/callback"


def _build_account_redirect(status_value: str, provider: str) -> str:
    params = urlencode({"tab": "account", "calendar": status_value, "provider": provider})
    return f"{get_trusted_web_origin()}/settings?{params}"


def _build_code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


async def _save_oauth_state(state: str, payload: dict[str, Any]) -> None:
    expires_at = _utc_now() + timedelta(seconds=OAUTH_STATE_TTL_SECONDS)
    try:
        client = redis.from_url(REDIS_URL)
        await client.set(
            f"nojoin:calendar:oauth:{state}",
            json.dumps(payload),
            ex=OAUTH_STATE_TTL_SECONDS,
        )
        await client.close()
        return
    except Exception:
        pass

    _oauth_state_fallback[state] = (expires_at, payload)


async def _pop_oauth_state(state: str) -> dict[str, Any] | None:
    try:
        client = redis.from_url(REDIS_URL)
        key = f"nojoin:calendar:oauth:{state}"
        stored = await client.get(key)
        if stored is not None:
            await client.delete(key)
            await client.close()
            return json.loads(stored)
        await client.close()
    except Exception:
        pass

    expires_at, payload = _oauth_state_fallback.pop(state, (None, None))
    if expires_at and expires_at > _utc_now() and payload is not None:
        return payload
    return None


async def _request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.request(
            method,
            url,
            headers=headers,
            params=params,
            data=data,
        )
        response.raise_for_status()
        return response.json()


async def get_provider_runtime_config(db: AsyncSession, provider: str) -> ProviderRuntimeConfig:
    statement = select(CalendarProviderConfig).where(CalendarProviderConfig.provider == provider)
    row = (await db.execute(statement)).scalar_one_or_none()

    env_keys = PROVIDER_ENV_KEYS[provider]
    env_client_id = os.getenv(env_keys["client_id"]) if env_keys["client_id"] else None
    env_client_secret = os.getenv(env_keys["client_secret"]) if env_keys["client_secret"] else None
    env_tenant_id = os.getenv(env_keys["tenant_id"]) if env_keys["tenant_id"] else None

    if row is not None:
        if row.enabled is False:
            return ProviderRuntimeConfig(
                provider=provider,
                client_id=row.client_id,
                client_secret=decrypt_secret(row.client_secret_encrypted),
                tenant_id=row.tenant_id or env_tenant_id or MICROSOFT_COMMON_TENANT,
                enabled=False,
                source="database",
            )

        db_client_secret = decrypt_secret(row.client_secret_encrypted)
        uses_database_values = any(
            value
            for value in (row.client_id, row.client_secret_encrypted, row.tenant_id)
        )
        return ProviderRuntimeConfig(
            provider=provider,
            client_id=row.client_id or env_client_id,
            client_secret=db_client_secret or env_client_secret,
            tenant_id=row.tenant_id or env_tenant_id or MICROSOFT_COMMON_TENANT,
            enabled=True,
            source="database" if uses_database_values else ("environment" if env_client_id or env_client_secret else "none"),
        )

    return ProviderRuntimeConfig(
        provider=provider,
        client_id=env_client_id,
        client_secret=env_client_secret,
        tenant_id=env_tenant_id or MICROSOFT_COMMON_TENANT,
        enabled=True,
        source="environment" if env_client_id or env_client_secret else "none",
    )


async def list_provider_statuses(db: AsyncSession) -> list[CalendarProviderStatusRead]:
    statuses: list[CalendarProviderStatusRead] = []
    for provider in (CalendarProvider.GOOGLE.value, CalendarProvider.MICROSOFT.value):
        runtime_config = await get_provider_runtime_config(db, provider)
        statuses.append(
            CalendarProviderStatusRead(
                provider=provider,
                display_name=PROVIDER_DISPLAY_NAMES[provider],
                configured=runtime_config.configured,
                source=runtime_config.source,
                enabled=runtime_config.enabled,
                redirect_uri=_build_redirect_uri(provider),
                client_id=runtime_config.client_id,
                tenant_id=runtime_config.tenant_id if provider == CalendarProvider.MICROSOFT.value else None,
                has_client_secret=bool(runtime_config.client_secret),
            )
        )
    return statuses


def _serialise_provider_availability(
    provider_status: CalendarProviderStatusRead,
) -> CalendarProviderAvailabilityRead:
    return CalendarProviderAvailabilityRead(
        provider=provider_status.provider,
        display_name=provider_status.display_name,
        configured=provider_status.configured,
    )


async def update_provider_configuration(
    db: AsyncSession,
    provider: str,
    *,
    client_id: str | None,
    client_secret: str | None,
    tenant_id: str | None,
    enabled: bool | None,
    clear_client_secret: bool,
) -> CalendarProviderStatusRead:
    statement = select(CalendarProviderConfig).where(CalendarProviderConfig.provider == provider)
    row = (await db.execute(statement)).scalar_one_or_none()
    if row is None:
        row = CalendarProviderConfig(provider=provider)

    if client_id is not None:
        row.client_id = client_id.strip() or None
    if enabled is not None:
        row.enabled = enabled
    if provider == CalendarProvider.MICROSOFT.value and tenant_id is not None:
        row.tenant_id = tenant_id.strip() or MICROSOFT_COMMON_TENANT
    if provider == CalendarProvider.GOOGLE.value:
        row.tenant_id = None

    if clear_client_secret:
        row.client_secret_encrypted = None
    elif client_secret is not None:
        stripped_secret = client_secret.strip()
        row.client_secret_encrypted = encrypt_secret(stripped_secret) if stripped_secret else None

    db.add(row)
    await db.commit()

    refreshed = await list_provider_statuses(db)
    return next(status_item for status_item in refreshed if status_item.provider == provider)


async def start_authorisation(db: AsyncSession, provider: str, user: User) -> str:
    runtime_config = await get_provider_runtime_config(db, provider)
    if not runtime_config.configured:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
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


async def _exchange_google_code(runtime_config: ProviderRuntimeConfig, code: str, code_verifier: str) -> TokenBundle:
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
        expires_at=_utc_now() + timedelta(seconds=int(expires_in)) if expires_in else None,
        scopes=scopes,
    )


async def _exchange_microsoft_code(runtime_config: ProviderRuntimeConfig, code: str, code_verifier: str) -> TokenBundle:
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
        expires_at=_utc_now() + timedelta(seconds=int(expires_in)) if expires_in else None,
        scopes=scopes,
    )


async def _refresh_google_access_token(runtime_config: ProviderRuntimeConfig, refresh_token: str) -> TokenBundle:
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
        expires_at=_utc_now() + timedelta(seconds=int(expires_in)) if expires_in else None,
        scopes=scopes,
    )


async def _refresh_microsoft_access_token(runtime_config: ProviderRuntimeConfig, refresh_token: str) -> TokenBundle:
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
        expires_at=_utc_now() + timedelta(seconds=int(expires_in)) if expires_in else None,
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


async def _list_google_calendars(access_token: str) -> list[ProviderCalendarRecord]:
    next_page_token: str | None = None
    calendars: list[ProviderCalendarRecord] = []
    while True:
        params: dict[str, Any] = {"minAccessRole": "reader"}
        if next_page_token:
            params["pageToken"] = next_page_token
        payload = await _request_json(
            "GET",
            GOOGLE_CALENDAR_LIST_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
        )
        for item in payload.get("items", []):
            access_role = item.get("accessRole")
            calendars.append(
                ProviderCalendarRecord(
                    remote_id=item["id"],
                    name=item.get("summary") or "Untitled calendar",
                    description=item.get("description"),
                    time_zone=item.get("timeZone"),
                    colour=item.get("backgroundColor"),
                    is_primary=bool(item.get("primary")),
                    is_read_only=access_role not in {"owner", "writer"},
                )
            )
        next_page_token = payload.get("nextPageToken")
        if not next_page_token:
            return calendars


async def _list_microsoft_calendars(access_token: str) -> list[ProviderCalendarRecord]:
    next_url: str | None = f"{MICROSOFT_GRAPH_URL}/me/calendars?$select=id,name,hexColor,canEdit,isDefaultCalendar"
    calendars: list[ProviderCalendarRecord] = []
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        while next_url:
            response = await client.get(next_url, headers=headers)
            response.raise_for_status()
            payload = response.json()
            for item in payload.get("value", []):
                calendars.append(
                    ProviderCalendarRecord(
                        remote_id=item["id"],
                        name=item.get("name") or "Untitled calendar",
                        description=None,
                        time_zone=None,
                        colour=item.get("hexColor"),
                        is_primary=bool(item.get("isDefaultCalendar")),
                        is_read_only=not bool(item.get("canEdit", False)),
                    )
                )
            next_url = payload.get("@odata.nextLink")
    return calendars


def _normalise_google_event(item: dict[str, Any]) -> ProviderEventRecord | None:
    if item.get("status") == "cancelled":
        return None
    start_payload = item.get("start", {})
    end_payload = item.get("end", {})
    location_text = _normalise_text(item.get("location"))
    conference_entry_points = item.get("conferenceData", {}).get("entryPoints", [])
    conference_urls = [
        entry.get("uri")
        for entry in conference_entry_points
        if isinstance(entry, dict)
    ]
    meeting_url = _pick_preferred_meeting_url(
        item.get("hangoutLink"),
        *conference_urls,
        location_text,
        item.get("description"),
    )
    if "date" in start_payload:
        start_date = date.fromisoformat(start_payload["date"])
        end_date = date.fromisoformat(end_payload.get("date", start_payload["date"]))
        if end_date <= start_date:
            end_date = start_date + timedelta(days=1)
        return ProviderEventRecord(
            remote_id=item["id"],
            title=item.get("summary") or "Untitled event",
            status=item.get("status") or "confirmed",
            is_all_day=True,
            starts_at=None,
            ends_at=None,
            start_date=start_date,
            end_date=end_date,
            source_url=item.get("htmlLink"),
            location_text=location_text,
            meeting_url=meeting_url,
            external_updated_at=_parse_iso_datetime(item.get("updated")),
        )

    starts_at = _parse_iso_datetime(start_payload.get("dateTime"))
    ends_at = _parse_iso_datetime(end_payload.get("dateTime")) or starts_at
    if starts_at is None:
        return None
    return ProviderEventRecord(
        remote_id=item["id"],
        title=item.get("summary") or "Untitled event",
        status=item.get("status") or "confirmed",
        is_all_day=False,
        starts_at=starts_at,
        ends_at=ends_at or starts_at,
        start_date=None,
        end_date=None,
        source_url=item.get("htmlLink"),
        location_text=location_text,
        meeting_url=meeting_url,
        external_updated_at=_parse_iso_datetime(item.get("updated")),
    )


def _build_google_events_query_params(
    window_start: datetime,
    window_end: datetime,
    *,
    sync_cursor: str | None,
    page_token: str | None,
) -> dict[str, str]:
    params: dict[str, str] = {
        "singleEvents": "true",
        "showDeleted": "true",
        "maxResults": "2500",
    }
    if sync_cursor:
        params["syncToken"] = sync_cursor
    else:
        params["timeMin"] = window_start.isoformat() + "Z"
        params["timeMax"] = window_end.isoformat() + "Z"
    if page_token:
        params["pageToken"] = page_token
    return params


async def _sync_google_events(
    access_token: str,
    calendar_id: str,
    window_start: datetime,
    window_end: datetime,
    *,
    sync_cursor: str | None,
) -> ProviderEventSyncResult:
    next_page_token: str | None = None
    events: list[ProviderEventRecord] = []
    deleted_remote_ids: list[str] = []
    headers = {"Authorization": f"Bearer {access_token}"}
    url = GOOGLE_EVENTS_URL_TEMPLATE.format(calendar_id=quote(calendar_id, safe=""))

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            response = await client.get(
                url,
                headers=headers,
                params=_build_google_events_query_params(
                    window_start,
                    window_end,
                    sync_cursor=sync_cursor,
                    page_token=next_page_token,
                ),
            )
            if sync_cursor and response.status_code == status.HTTP_410_GONE:
                raise IncrementalSyncResetRequired("Google sync token expired")
            response.raise_for_status()
            payload = response.json()
            for item in payload.get("items", []):
                if item.get("status") == "cancelled" or item.get("deleted"):
                    remote_id = item.get("id")
                    if remote_id:
                        deleted_remote_ids.append(str(remote_id))
                    continue
                event = _normalise_google_event(item)
                if event is not None:
                    events.append(event)
            next_page_token = payload.get("nextPageToken")
            if not next_page_token:
                return ProviderEventSyncResult(
                    events=events,
                    deleted_remote_ids=deleted_remote_ids,
                    cursor=payload.get("nextSyncToken"),
                )


def _normalise_microsoft_event(item: dict[str, Any]) -> ProviderEventRecord | None:
    if item.get("isCancelled"):
        return None
    location_text = _get_microsoft_location_text(item)
    body = item.get("body")
    body_content = body.get("content") if isinstance(body, dict) else None
    online_meeting = item.get("onlineMeeting")
    join_url = online_meeting.get("joinUrl") if isinstance(online_meeting, dict) else None
    meeting_url = _pick_preferred_meeting_url(
        join_url,
        item.get("onlineMeetingUrl"),
        location_text,
        item.get("bodyPreview"),
        body_content,
    )
    if item.get("isAllDay"):
        start_date = date.fromisoformat(item["start"]["dateTime"][:10])
        end_date = date.fromisoformat(item["end"]["dateTime"][:10])
        if end_date <= start_date:
            end_date = start_date + timedelta(days=1)
        return ProviderEventRecord(
            remote_id=item["id"],
            title=item.get("subject") or "Untitled event",
            status="cancelled" if item.get("isCancelled") else "confirmed",
            is_all_day=True,
            starts_at=None,
            ends_at=None,
            start_date=start_date,
            end_date=end_date,
            source_url=item.get("webLink"),
            location_text=location_text,
            meeting_url=meeting_url,
            external_updated_at=_parse_iso_datetime(item.get("lastModifiedDateTime")),
        )

    starts_at = _parse_iso_datetime(item.get("start", {}).get("dateTime"))
    ends_at = _parse_iso_datetime(item.get("end", {}).get("dateTime")) or starts_at
    if starts_at is None:
        return None
    return ProviderEventRecord(
        remote_id=item["id"],
        title=item.get("subject") or "Untitled event",
        status="cancelled" if item.get("isCancelled") else "confirmed",
        is_all_day=False,
        starts_at=starts_at,
        ends_at=ends_at or starts_at,
        start_date=None,
        end_date=None,
        source_url=item.get("webLink"),
        location_text=location_text,
        meeting_url=meeting_url,
        external_updated_at=_parse_iso_datetime(item.get("lastModifiedDateTime")),
    )


def _build_microsoft_calendar_view_url(
    calendar_id: str,
    window_start: datetime,
    window_end: datetime,
    *,
    delta: bool,
) -> str:
    params = urlencode(
        {
            "startDateTime": window_start.isoformat() + "Z",
            "endDateTime": window_end.isoformat() + "Z",
            "$select": MICROSOFT_EVENT_SELECT,
        }
    )
    return (
        f"{MICROSOFT_GRAPH_URL}/me/calendars/{quote(calendar_id, safe='')}/calendarView"
        f"{'/delta' if delta else ''}"
        f"?{params}"
    )


def _build_microsoft_delta_url(calendar_id: str, window_start: datetime, window_end: datetime) -> str:
    return _build_microsoft_calendar_view_url(
        calendar_id,
        window_start,
        window_end,
        delta=True,
    )


async def _list_microsoft_events(
    access_token: str,
    calendar_id: str,
    window_start: datetime,
    window_end: datetime,
) -> list[ProviderEventRecord]:
    next_url: str | None = _build_microsoft_calendar_view_url(
        calendar_id,
        window_start,
        window_end,
        delta=False,
    )
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Prefer": 'outlook.timezone="UTC"',
    }
    events: list[ProviderEventRecord] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        while next_url:
            response = await client.get(next_url, headers=headers)
            response.raise_for_status()
            payload = response.json()
            for item in payload.get("value", []):
                if item.get("type") == "seriesMaster":
                    continue
                event = _normalise_microsoft_event(item)
                if event is not None:
                    events.append(event)
            next_url = payload.get("@odata.nextLink")
    return events


async def _get_microsoft_delta_cursor(
    access_token: str,
    calendar_id: str,
    window_start: datetime,
    window_end: datetime,
) -> str | None:
    next_url: str | None = _build_microsoft_delta_url(calendar_id, window_start, window_end)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Prefer": 'outlook.timezone="UTC"',
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        while next_url:
            response = await client.get(next_url, headers=headers)
            response.raise_for_status()
            payload = response.json()
            next_url = payload.get("@odata.nextLink")
            if not next_url:
                return payload.get("@odata.deltaLink")
    return None


async def _sync_microsoft_events(
    access_token: str,
    calendar_id: str,
    window_start: datetime,
    window_end: datetime,
    *,
    sync_cursor: str | None,
) -> ProviderEventSyncResult:
    next_url: str | None = sync_cursor or _build_microsoft_delta_url(calendar_id, window_start, window_end)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Prefer": 'outlook.timezone="UTC"',
    }
    events: list[ProviderEventRecord] = []
    deleted_remote_ids: list[str] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        while next_url:
            response = await client.get(next_url, headers=headers)
            if sync_cursor and response.status_code in {status.HTTP_404_NOT_FOUND, status.HTTP_410_GONE}:
                raise IncrementalSyncResetRequired("Microsoft delta cursor expired")
            response.raise_for_status()
            payload = response.json()
            for item in payload.get("value", []):
                if item.get("@removed"):
                    remote_id = item.get("id")
                    if remote_id:
                        deleted_remote_ids.append(str(remote_id))
                    continue
                if item.get("type") == "seriesMaster" or _is_partial_microsoft_occurrence(item):
                    raise IncrementalSyncResetRequired("Microsoft recurring event delta requires full resync")
                event = _normalise_microsoft_event(item)
                if event is not None:
                    events.append(event)
            next_url = payload.get("@odata.nextLink")
            if not next_url:
                return ProviderEventSyncResult(
                    events=events,
                    deleted_remote_ids=deleted_remote_ids,
                    cursor=payload.get("@odata.deltaLink"),
                )
    return ProviderEventSyncResult(events=events, deleted_remote_ids=deleted_remote_ids, cursor=sync_cursor)


async def _get_access_token_for_connection(
    db: AsyncSession,
    connection: CalendarConnection,
    runtime_config: ProviderRuntimeConfig,
) -> str:
    access_token = decrypt_secret(connection.access_token_encrypted)
    refresh_token = decrypt_secret(connection.refresh_token_encrypted)
    now = _utc_now()
    if access_token and connection.token_expires_at and connection.token_expires_at > now + timedelta(minutes=2):
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
    connection.refresh_token_encrypted = encrypt_secret(refreshed.refresh_token) if refreshed.refresh_token else connection.refresh_token_encrypted
    connection.token_expires_at = refreshed.expires_at
    connection.granted_scopes = refreshed.scopes
    db.add(connection)
    await db.commit()
    return refreshed.access_token


async def _upsert_connection(
    db: AsyncSession,
    *,
    user: User,
    provider: str,
    identity: ProviderIdentity,
    tokens: TokenBundle,
) -> CalendarConnection:
    statement = select(CalendarConnection).where(
        CalendarConnection.user_id == user.id,
        CalendarConnection.provider == provider,
        CalendarConnection.provider_account_id == identity.account_id,
    )
    connection = (await db.execute(statement)).scalar_one_or_none()
    if connection is None:
        connection = CalendarConnection(
            user_id=user.id,
            provider=provider,
            provider_account_id=identity.account_id,
        )

    connection.email = identity.email
    connection.display_name = identity.display_name
    connection.access_token_encrypted = encrypt_secret(tokens.access_token)
    if tokens.refresh_token:
        connection.refresh_token_encrypted = encrypt_secret(tokens.refresh_token)
    connection.token_expires_at = tokens.expires_at
    connection.granted_scopes = tokens.scopes
    connection.sync_status = CalendarSyncStatus.IDLE.value
    connection.sync_error = None
    db.add(connection)
    await db.commit()
    await db.refresh(connection)
    return connection


async def _refresh_connection_calendars(
    db: AsyncSession,
    connection: CalendarConnection,
    provider_calendars: Iterable[ProviderCalendarRecord],
) -> list[CalendarSource]:
    statement = select(CalendarSource).where(CalendarSource.connection_id == connection.id)
    existing_calendars = list((await db.execute(statement)).scalars().all())
    existing_by_remote_id = {calendar.provider_calendar_id: calendar for calendar in existing_calendars}
    selected_existing = {calendar.provider_calendar_id for calendar in existing_calendars if calendar.is_selected}
    seen_remote_ids: set[str] = set()

    for provider_calendar in provider_calendars:
        seen_remote_ids.add(provider_calendar.remote_id)
        calendar = existing_by_remote_id.get(provider_calendar.remote_id)
        is_new = calendar is None
        if calendar is None:
            calendar = CalendarSource(
                connection_id=connection.id,
                provider_calendar_id=provider_calendar.remote_id,
            )

        calendar.name = provider_calendar.name
        calendar.description = provider_calendar.description
        calendar.time_zone = provider_calendar.time_zone
        calendar.colour = provider_calendar.colour
        calendar.is_primary = provider_calendar.is_primary
        calendar.is_read_only = provider_calendar.is_read_only
        if is_new:
            calendar.is_selected = provider_calendar.is_primary and not selected_existing
        db.add(calendar)

    for calendar in existing_calendars:
        if calendar.provider_calendar_id not in seen_remote_ids:
            await db.delete(calendar)

    await db.commit()
    statement = (
        select(CalendarSource)
        .where(CalendarSource.connection_id == connection.id)
        .order_by(CalendarSource.is_primary.desc(), CalendarSource.name.asc())
    )
    return list((await db.execute(statement)).scalars().all())


def _apply_provider_event_to_model(
    calendar_event: CalendarEvent,
    provider_event: ProviderEventRecord,
) -> CalendarEvent:
    calendar_event.provider_event_id = provider_event.remote_id
    calendar_event.title = provider_event.title
    calendar_event.status = provider_event.status
    calendar_event.is_all_day = provider_event.is_all_day
    calendar_event.starts_at = provider_event.starts_at
    calendar_event.ends_at = provider_event.ends_at
    calendar_event.start_date = provider_event.start_date
    calendar_event.end_date = provider_event.end_date
    calendar_event.location_text = provider_event.location_text
    calendar_event.meeting_url = provider_event.meeting_url
    calendar_event.source_url = provider_event.source_url
    calendar_event.external_updated_at = provider_event.external_updated_at
    return calendar_event


def _build_calendar_event_model(
    calendar_id: int,
    provider_event: ProviderEventRecord,
) -> CalendarEvent:
    return _apply_provider_event_to_model(
        CalendarEvent(calendar_id=calendar_id, provider_event_id=provider_event.remote_id, title=provider_event.title),
        provider_event,
    )


async def _replace_calendar_events(
    db: AsyncSession,
    calendar_id: int,
    provider_events: list[ProviderEventRecord],
) -> None:
    await db.execute(delete(CalendarEvent).where(CalendarEvent.calendar_id == calendar_id))
    for provider_event in provider_events:
        db.add(_build_calendar_event_model(calendar_id, provider_event))


async def _apply_incremental_calendar_events(
    db: AsyncSession,
    calendar_id: int,
    provider_events: list[ProviderEventRecord],
    deleted_remote_ids: list[str],
) -> None:
    unique_deleted_ids = sorted(set(deleted_remote_ids))
    if unique_deleted_ids:
        await db.execute(
            delete(CalendarEvent).where(
                CalendarEvent.calendar_id == calendar_id,
                CalendarEvent.provider_event_id.in_(unique_deleted_ids),
            )
        )

    if not provider_events:
        return

    changed_remote_ids = sorted({provider_event.remote_id for provider_event in provider_events})
    existing_events = list(
        (
            await db.execute(
                select(CalendarEvent).where(
                    CalendarEvent.calendar_id == calendar_id,
                    CalendarEvent.provider_event_id.in_(changed_remote_ids),
                )
            )
        ).scalars().all()
    )
    existing_by_remote_id = {
        existing_event.provider_event_id: existing_event
        for existing_event in existing_events
    }

    for provider_event in provider_events:
        existing_event = existing_by_remote_id.get(provider_event.remote_id)
        if existing_event is None:
            db.add(_build_calendar_event_model(calendar_id, provider_event))
            continue
        db.add(_apply_provider_event_to_model(existing_event, provider_event))


def _can_use_incremental_sync(
    calendar: CalendarSource,
    window_start: datetime,
    window_end: datetime,
) -> bool:
    return bool(
        calendar.sync_cursor
        and calendar.sync_window_start == window_start
        and calendar.sync_window_end == window_end
    )


async def _microsoft_calendar_has_partial_occurrence_artifacts(
    db: AsyncSession,
    calendar_id: int,
) -> bool:
    statement = (
        select(CalendarEvent.id)
        .where(CalendarEvent.calendar_id == calendar_id)
        .where(CalendarEvent.title == "Untitled event")
        .where(CalendarEvent.source_url.is_(None))
        .where(CalendarEvent.meeting_url.is_(None))
        .where(CalendarEvent.location_text.is_(None))
        .limit(1)
    )
    return (await db.execute(statement)).scalar_one_or_none() is not None


def _serialise_source(calendar: CalendarSource) -> CalendarSourceRead:
    effective_colour = calendar.user_colour or calendar.colour
    return CalendarSourceRead(
        id=calendar.id,
        provider_calendar_id=calendar.provider_calendar_id,
        name=calendar.name,
        description=calendar.description,
        time_zone=calendar.time_zone,
        colour=effective_colour,
        provider_colour=calendar.colour,
        custom_colour=calendar.user_colour,
        is_primary=calendar.is_primary,
        is_read_only=calendar.is_read_only,
        is_selected=calendar.is_selected,
        last_synced_at=utc_naive_to_aware(calendar.last_synced_at),
    )


def _serialise_connection(connection: CalendarConnection) -> CalendarConnectionRead:
    calendars = sorted(
        connection.calendars,
        key=lambda item: (not item.is_primary, item.name.lower()),
    )
    return CalendarConnectionRead(
        id=connection.id,
        provider=connection.provider,
        email=connection.email,
        display_name=connection.display_name,
        sync_status=connection.sync_status,
        sync_error=connection.sync_error,
        last_sync_started_at=utc_naive_to_aware(connection.last_sync_started_at),
        last_sync_completed_at=utc_naive_to_aware(connection.last_sync_completed_at),
        last_synced_at=utc_naive_to_aware(connection.last_synced_at),
        selected_calendar_count=sum(1 for calendar in calendars if calendar.is_selected),
        calendars=[_serialise_source(calendar) for calendar in calendars],
    )


async def get_overview(db: AsyncSession, user: User) -> CalendarOverviewRead:
    providers = await list_provider_statuses(db)
    statement = (
        select(CalendarConnection)
        .options(selectinload(CalendarConnection.calendars))
        .where(CalendarConnection.user_id == user.id)
        .order_by(CalendarConnection.provider.asc(), CalendarConnection.created_at.asc())
    )
    connections = list((await db.execute(statement)).scalars().unique().all())
    return CalendarOverviewRead(
        providers=[
            _serialise_provider_availability(provider_status)
            for provider_status in providers
        ],
        connections=[_serialise_connection(connection) for connection in connections],
    )


async def handle_callback(db: AsyncSession, provider: str, user: User, code: str, state: str) -> CalendarConnection:
    runtime_config = await get_provider_runtime_config(db, provider)
    if not runtime_config.configured:
        raise HTTPException(status_code=400, detail="Calendar integration is not configured")

    state_payload = await _pop_oauth_state(state)
    if not state_payload:
        raise HTTPException(status_code=400, detail="The calendar connection session expired")
    if int(state_payload.get("user_id", -1)) != user.id or state_payload.get("provider") != provider:
        raise HTTPException(status_code=400, detail="The calendar connection session is invalid")

    if provider == CalendarProvider.GOOGLE.value:
        token_bundle = await _exchange_google_code(runtime_config, code, str(state_payload["code_verifier"]))
        identity = await _fetch_google_identity(token_bundle.access_token)
        provider_calendars = await _list_google_calendars(token_bundle.access_token)
    else:
        token_bundle = await _exchange_microsoft_code(runtime_config, code, str(state_payload["code_verifier"]))
        identity = await _fetch_microsoft_identity(token_bundle.access_token)
        provider_calendars = await _list_microsoft_calendars(token_bundle.access_token)

    connection = await _upsert_connection(
        db,
        user=user,
        provider=provider,
        identity=identity,
        tokens=token_bundle,
    )
    await _refresh_connection_calendars(db, connection, provider_calendars)
    try:
        await sync_connection_in_session(db, connection.id)
    except Exception:
        logger.warning(
            "Initial calendar sync failed for connection %s (%s)",
            connection.id,
            provider,
        )
    await db.refresh(connection)
    return connection


async def update_connection_selection(
    db: AsyncSession,
    user: User,
    connection_id: int,
    selection: CalendarSelectionUpdate,
) -> CalendarConnectionRead:
    statement = (
        select(CalendarConnection)
        .options(selectinload(CalendarConnection.calendars))
        .where(CalendarConnection.id == connection_id, CalendarConnection.user_id == user.id)
    )
    connection = (await db.execute(statement)).scalars().unique().one_or_none()
    if connection is None:
        raise HTTPException(status_code=404, detail="Calendar connection not found")

    selected_ids = set(selection.selected_calendar_ids)
    for calendar in connection.calendars:
        calendar.is_selected = calendar.id in selected_ids
        db.add(calendar)
    connection.sync_status = CalendarSyncStatus.IDLE.value if not selected_ids else connection.sync_status
    if not selected_ids:
        connection.sync_error = None
    db.add(connection)
    await db.commit()

    if selected_ids:
        await sync_connection_in_session(db, connection.id)

    refreshed = (
        await db.execute(
            select(CalendarConnection)
            .options(selectinload(CalendarConnection.calendars))
            .where(CalendarConnection.id == connection.id)
        )
    ).scalars().unique().one()
    return _serialise_connection(refreshed)


async def update_calendar_source_colour(
    db: AsyncSession,
    user: User,
    connection_id: int,
    calendar_id: int,
    payload: CalendarSourceColourUpdate,
) -> CalendarConnectionRead:
    statement = (
        select(CalendarConnection)
        .options(selectinload(CalendarConnection.calendars))
        .where(CalendarConnection.id == connection_id, CalendarConnection.user_id == user.id)
    )
    connection = (await db.execute(statement)).scalars().unique().one_or_none()
    if connection is None:
        raise HTTPException(status_code=404, detail="Calendar connection not found")

    calendar = next(
        (item for item in connection.calendars if item.id == calendar_id),
        None,
    )
    if calendar is None:
        raise HTTPException(status_code=404, detail="Calendar source not found")

    calendar.user_colour = _normalise_colour_value(payload.colour)
    db.add(calendar)
    await db.commit()

    refreshed = (
        await db.execute(
            select(CalendarConnection)
            .options(selectinload(CalendarConnection.calendars))
            .where(CalendarConnection.id == connection.id)
        )
    ).scalars().unique().one()
    return _serialise_connection(refreshed)


async def disconnect_connection(db: AsyncSession, user: User, connection_id: int) -> None:
    statement = select(CalendarConnection).where(
        CalendarConnection.id == connection_id,
        CalendarConnection.user_id == user.id,
    )
    connection = (await db.execute(statement)).scalar_one_or_none()
    if connection is None:
        raise HTTPException(status_code=404, detail="Calendar connection not found")
    await db.delete(connection)
    await db.commit()


async def refresh_connection_now(db: AsyncSession, user: User, connection_id: int) -> CalendarConnectionRead:
    statement = (
        select(CalendarConnection)
        .options(selectinload(CalendarConnection.calendars))
        .where(CalendarConnection.id == connection_id, CalendarConnection.user_id == user.id)
    )
    connection = (await db.execute(statement)).scalars().unique().one_or_none()
    if connection is None:
        raise HTTPException(status_code=404, detail="Calendar connection not found")

    await sync_connection_in_session(db, connection.id)

    refreshed = (
        await db.execute(
            select(CalendarConnection)
            .options(selectinload(CalendarConnection.calendars))
            .where(CalendarConnection.id == connection.id)
        )
    ).scalars().unique().one()
    return _serialise_connection(refreshed)


async def sync_connection_in_session(db: AsyncSession, connection_id: int) -> None:
    statement = (
        select(CalendarConnection)
        .options(selectinload(CalendarConnection.calendars))
        .where(CalendarConnection.id == connection_id)
    )
    connection = (await db.execute(statement)).scalars().unique().one_or_none()
    if connection is None:
        return

    selected_calendars = [calendar for calendar in connection.calendars if calendar.is_selected]
    if not selected_calendars:
        connection.sync_status = CalendarSyncStatus.IDLE.value
        connection.sync_error = None
        db.add(connection)
        await db.commit()
        return

    runtime_config = await get_provider_runtime_config(db, connection.provider)
    if not runtime_config.configured:
        connection.sync_status = CalendarSyncStatus.ERROR.value
        connection.sync_error = f"{PROVIDER_DISPLAY_NAMES[connection.provider]} is not configured"
        db.add(connection)
        await db.commit()
        return

    started_at = _utc_now()
    connection.sync_status = CalendarSyncStatus.SYNCING.value
    connection.sync_error = None
    connection.last_sync_started_at = started_at
    db.add(connection)
    await db.commit()

    try:
        access_token = await _get_access_token_for_connection(db, connection, runtime_config)
        if connection.provider == CalendarProvider.GOOGLE.value:
            provider_calendars = await _list_google_calendars(access_token)
        else:
            provider_calendars = await _list_microsoft_calendars(access_token)

        refreshed_calendars = await _refresh_connection_calendars(db, connection, provider_calendars)
        selected_calendars = [calendar for calendar in refreshed_calendars if calendar.is_selected]
        window_start, window_end = _build_sync_window()

        for calendar in selected_calendars:
            use_incremental_sync = _can_use_incremental_sync(
                calendar,
                window_start,
                window_end,
            )

            if (
                connection.provider == CalendarProvider.MICROSOFT.value
                and use_incremental_sync
                and await _microsoft_calendar_has_partial_occurrence_artifacts(db, calendar.id)
            ):
                use_incremental_sync = False

            if connection.provider == CalendarProvider.GOOGLE.value:
                try:
                    sync_result = await _sync_google_events(
                        access_token,
                        calendar.provider_calendar_id,
                        window_start,
                        window_end,
                        sync_cursor=calendar.sync_cursor if use_incremental_sync else None,
                    )
                    if use_incremental_sync:
                        await _apply_incremental_calendar_events(
                            db,
                            calendar.id,
                            sync_result.events,
                            sync_result.deleted_remote_ids,
                        )
                    else:
                        await _replace_calendar_events(db, calendar.id, sync_result.events)
                except IncrementalSyncResetRequired:
                    sync_result = await _sync_google_events(
                        access_token,
                        calendar.provider_calendar_id,
                        window_start,
                        window_end,
                        sync_cursor=None,
                    )
                    await _replace_calendar_events(db, calendar.id, sync_result.events)
            else:
                if use_incremental_sync:
                    try:
                        sync_result = await _sync_microsoft_events(
                            access_token,
                            calendar.provider_calendar_id,
                            window_start,
                            window_end,
                            sync_cursor=calendar.sync_cursor,
                        )
                        await _apply_incremental_calendar_events(
                            db,
                            calendar.id,
                            sync_result.events,
                            sync_result.deleted_remote_ids,
                        )
                    except IncrementalSyncResetRequired:
                        provider_events = await _list_microsoft_events(
                            access_token,
                            calendar.provider_calendar_id,
                            window_start,
                            window_end,
                        )
                        delta_cursor = await _get_microsoft_delta_cursor(
                            access_token,
                            calendar.provider_calendar_id,
                            window_start,
                            window_end,
                        )
                        sync_result = ProviderEventSyncResult(
                            events=provider_events,
                            deleted_remote_ids=[],
                            cursor=delta_cursor,
                        )
                        await _replace_calendar_events(db, calendar.id, sync_result.events)
                else:
                    provider_events = await _list_microsoft_events(
                        access_token,
                        calendar.provider_calendar_id,
                        window_start,
                        window_end,
                    )
                    delta_cursor = await _get_microsoft_delta_cursor(
                        access_token,
                        calendar.provider_calendar_id,
                        window_start,
                        window_end,
                    )
                    sync_result = ProviderEventSyncResult(
                        events=provider_events,
                        deleted_remote_ids=[],
                        cursor=delta_cursor,
                    )
                    await _replace_calendar_events(db, calendar.id, sync_result.events)

            calendar.sync_cursor = sync_result.cursor
            calendar.last_synced_at = _utc_now()
            calendar.sync_window_start = window_start
            calendar.sync_window_end = window_end
            db.add(calendar)

        completed_at = _utc_now()
        connection.sync_status = CalendarSyncStatus.SUCCESS.value
        connection.last_sync_completed_at = completed_at
        connection.last_synced_at = completed_at
        connection.sync_error = None
        db.add(connection)
        await db.commit()
    except Exception as exc:
        logger.warning(
            "Calendar sync failed for connection %s (%s)",
            connection.id,
            connection.provider,
        )
        failure_status, failure_message = _classify_sync_failure(exc)
        connection.sync_status = failure_status
        connection.last_sync_completed_at = _utc_now()
        connection.sync_error = failure_message
        db.add(connection)
        await db.commit()


async def sync_connection_by_id(connection_id: int) -> None:
    async with async_session_maker() as db:
        await sync_connection_in_session(db, connection_id)


async def sync_all_connections() -> int:
    async with async_session_maker() as db:
        statement = (
            select(CalendarConnection.id)
            .join(CalendarSource, CalendarSource.connection_id == CalendarConnection.id)
            .where(CalendarSource.is_selected.is_(True))
            .where(
                CalendarConnection.sync_status
                != CalendarSyncStatus.REAUTHORISATION_REQUIRED.value
            )
            .distinct()
        )
        connection_ids = list((await db.execute(statement)).scalars().all())

    for connection_id in connection_ids:
        await sync_connection_by_id(connection_id)
    return len(connection_ids)


def _event_sort_key(event: CalendarEvent) -> tuple[datetime, str]:
    if event.is_all_day and event.start_date is not None:
        return datetime.combine(event.start_date, datetime.min.time()), event.title.lower()
    if event.starts_at is not None:
        return event.starts_at, event.title.lower()
    return datetime.max, event.title.lower()


def _iter_event_dates(event: CalendarEvent, timezone_name: str) -> Iterable[date]:
    if event.is_all_day and event.start_date is not None and event.end_date is not None:
        current = event.start_date
        last_date = event.end_date - timedelta(days=1)
        while current <= last_date:
            yield current
            current += timedelta(days=1)
        return

    if event.starts_at is None or event.ends_at is None:
        return

    local_start = utc_naive_to_timezone(event.starts_at, timezone_name)
    local_end = utc_naive_to_timezone(event.ends_at, timezone_name)
    if local_start is None or local_end is None:
        return

    current = local_start.date()
    end_moment = local_end
    if end_moment > local_start:
        end_moment = end_moment - timedelta(microseconds=1)
    last_date = end_moment.date()
    while current <= last_date:
        yield current
        current += timedelta(days=1)


def _to_dashboard_event(
    event: CalendarEvent,
    calendars_by_id: dict[int, CalendarSource],
    accounts_by_connection_id: dict[int, CalendarConnection],
) -> CalendarDashboardEventRead:
    calendar = calendars_by_id[event.calendar_id]
    connection = accounts_by_connection_id[calendar.connection_id]
    account_label = connection.email or connection.display_name
    meeting_url_host = _get_meeting_url_host(event.meeting_url)
    calendar_colour = getattr(calendar, "user_colour", None) or getattr(calendar, "colour", None)
    return CalendarDashboardEventRead(
        id=event.id,
        title=event.title,
        provider=connection.provider,
        calendar_id=calendar.id,
        calendar_name=calendar.name,
        calendar_colour=calendar_colour,
        account_label=account_label,
        location=event.location_text,
        meeting_url=event.meeting_url,
        meeting_url_trusted=_is_trusted_meeting_url(event.meeting_url),
        meeting_url_host=meeting_url_host,
        is_all_day=event.is_all_day,
        starts_at=utc_naive_to_aware(event.starts_at),
        ends_at=utc_naive_to_aware(event.ends_at),
        start_date=event.start_date,
        end_date=event.end_date,
    )


async def get_dashboard_summary(
    db: AsyncSession,
    user: User,
    month: str,
    timezone_name: str | None = None,
) -> CalendarDashboardSummaryRead:
    try:
        viewed_month = datetime.strptime(month, "%Y-%m")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Month must use YYYY-MM format") from exc

    effective_timezone = get_user_timezone_name(
        user.settings or {},
        fallback=timezone_name,
    )
    tz = get_timezone(effective_timezone)

    providers = await list_provider_statuses(db)
    provider_configured = any(provider.configured for provider in providers)
    statement = (
        select(CalendarConnection)
        .options(selectinload(CalendarConnection.calendars))
        .where(CalendarConnection.user_id == user.id)
    )
    connections = list((await db.execute(statement)).scalars().unique().all())
    selected_calendars = [calendar for connection in connections for calendar in connection.calendars if calendar.is_selected]

    month_start_local = datetime(viewed_month.year, viewed_month.month, 1, tzinfo=tz)
    if viewed_month.month == 12:
        month_end_local = datetime(viewed_month.year + 1, 1, 1, tzinfo=tz)
    else:
        month_end_local = datetime(viewed_month.year, viewed_month.month + 1, 1, tzinfo=tz)

    month_start = month_start_local.astimezone(timezone.utc).replace(tzinfo=None)
    month_end = month_end_local.astimezone(timezone.utc).replace(tzinfo=None)
    month_start_date = month_start_local.date()
    month_end_date = month_end_local.date()

    state = CalendarDashboardState.READY.value
    if not provider_configured and not connections:
        state = CalendarDashboardState.PROVIDER_NOT_CONFIGURED.value
    elif not connections:
        state = CalendarDashboardState.NO_ACCOUNTS.value
    elif not selected_calendars:
        state = CalendarDashboardState.NO_SELECTED_CALENDARS.value

    events: list[CalendarEvent] = []
    calendars_by_id = {calendar.id: calendar for calendar in selected_calendars}
    accounts_by_connection_id = {connection.id: connection for connection in connections}
    if selected_calendars:
        calendar_ids = list(calendars_by_id.keys())
        overlap_statement = select(CalendarEvent).where(
            CalendarEvent.calendar_id.in_(calendar_ids),
            sa.or_(
                sa.and_(
                    CalendarEvent.is_all_day.is_(True),
                    CalendarEvent.start_date < month_end_date,
                    CalendarEvent.end_date > month_start_date,
                ),
                sa.and_(
                    CalendarEvent.is_all_day.is_(False),
                    CalendarEvent.starts_at < month_end,
                    CalendarEvent.ends_at >= month_start,
                ),
            ),
        )
        events = list((await db.execute(overlap_statement)).scalars().all())

    if selected_calendars and not events:
        if any(connection.sync_status == CalendarSyncStatus.SYNCING.value for connection in connections):
            state = CalendarDashboardState.SYNC_IN_PROGRESS.value
        else:
            state = CalendarDashboardState.NO_EVENTS.value

    day_counts: dict[date, int] = {}
    for event in events:
        for event_date in _iter_event_dates(event, effective_timezone):
            if month_start_date <= event_date < month_end_date:
                day_counts[event_date] = day_counts.get(event_date, 0) + 1

    agenda_items = [_to_dashboard_event(event, calendars_by_id, accounts_by_connection_id) for event in sorted(events, key=_event_sort_key)]

    next_event_obj: CalendarDashboardEventRead | None = None
    if selected_calendars:
        today = today_in_timezone(effective_timezone)
        now = _utc_now()
        future_statement = select(CalendarEvent).where(
            CalendarEvent.calendar_id.in_(list(calendars_by_id.keys())),
            sa.or_(
                sa.and_(CalendarEvent.is_all_day.is_(True), CalendarEvent.end_date > today),
                sa.and_(CalendarEvent.is_all_day.is_(False), CalendarEvent.ends_at >= now),
            ),
        )
        future_events = list((await db.execute(future_statement)).scalars().all())
        if future_events:
            next_event = sorted(future_events, key=_event_sort_key)[0]
            next_event_obj = _to_dashboard_event(next_event, calendars_by_id, accounts_by_connection_id)

    last_synced_at = max(
        (connection.last_synced_at for connection in connections if connection.last_synced_at is not None),
        default=None,
    )
    is_syncing = any(connection.sync_status == CalendarSyncStatus.SYNCING.value for connection in connections)
    return CalendarDashboardSummaryRead(
        month=month,
        timezone=effective_timezone,
        state=state,
        provider_configured=provider_configured,
        is_syncing=is_syncing,
        connection_count=len(connections),
        selected_calendar_count=len(selected_calendars),
        last_synced_at=utc_naive_to_aware(last_synced_at),
        day_counts=[
            CalendarDashboardDayCountRead(date=event_date, count=count)
            for event_date, count in sorted(day_counts.items())
        ],
        agenda_items=agenda_items,
        next_event=next_event_obj,
    )