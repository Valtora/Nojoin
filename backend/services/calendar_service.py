from __future__ import annotations

import base64
import calendar as month_calendar
import hashlib
import json
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import quote, urlencode

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
    CalendarProviderConfig,
    CalendarProviderStatusRead,
    CalendarSelectionUpdate,
    CalendarSource,
    CalendarSourceRead,
    CalendarSyncStatus,
    CalendarEvent,
)
from backend.models.user import User
from backend.utils.config_manager import get_trusted_web_origin


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
    source_url: str | None
    external_updated_at: datetime | None


def _utc_now() -> datetime:
    return datetime.utcnow()


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
                client_id=runtime_config.client_id,
                tenant_id=runtime_config.tenant_id if provider == CalendarProvider.MICROSOFT.value else None,
                has_client_secret=bool(runtime_config.client_secret),
            )
        )
    return statuses


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
        external_updated_at=_parse_iso_datetime(item.get("updated")),
    )


async def _list_google_events(access_token: str, calendar_id: str, window_start: datetime, window_end: datetime) -> list[ProviderEventRecord]:
    next_page_token: str | None = None
    events: list[ProviderEventRecord] = []
    while True:
        params: dict[str, Any] = {
            "singleEvents": "true",
            "orderBy": "startTime",
            "timeMin": window_start.isoformat() + "Z",
            "timeMax": window_end.isoformat() + "Z",
            "showDeleted": "true",
            "maxResults": "2500",
        }
        if next_page_token:
            params["pageToken"] = next_page_token
        payload = await _request_json(
            "GET",
            GOOGLE_EVENTS_URL_TEMPLATE.format(calendar_id=quote(calendar_id, safe="")),
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
        )
        for item in payload.get("items", []):
            event = _normalise_google_event(item)
            if event is not None:
                events.append(event)
        next_page_token = payload.get("nextPageToken")
        if not next_page_token:
            return events


def _normalise_microsoft_event(item: dict[str, Any]) -> ProviderEventRecord | None:
    if item.get("isCancelled"):
        return None
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
        external_updated_at=_parse_iso_datetime(item.get("lastModifiedDateTime")),
    )


async def _list_microsoft_events(access_token: str, calendar_id: str, window_start: datetime, window_end: datetime) -> list[ProviderEventRecord]:
    next_url: str | None = (
        f"{MICROSOFT_GRAPH_URL}/me/calendars/{calendar_id}/calendarView"
        f"?$select=id,subject,start,end,isAllDay,webLink,lastModifiedDateTime,isCancelled"
        f"&$top=1000&startDateTime={window_start.isoformat()}Z&endDateTime={window_end.isoformat()}Z"
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
                event = _normalise_microsoft_event(item)
                if event is not None:
                    events.append(event)
            next_url = payload.get("@odata.nextLink")
    return events


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


def _serialise_source(calendar: CalendarSource) -> CalendarSourceRead:
    return CalendarSourceRead(
        id=calendar.id,
        provider_calendar_id=calendar.provider_calendar_id,
        name=calendar.name,
        description=calendar.description,
        time_zone=calendar.time_zone,
        colour=calendar.colour,
        is_primary=calendar.is_primary,
        is_read_only=calendar.is_read_only,
        is_selected=calendar.is_selected,
        last_synced_at=calendar.last_synced_at,
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
        last_sync_started_at=connection.last_sync_started_at,
        last_sync_completed_at=connection.last_sync_completed_at,
        last_synced_at=connection.last_synced_at,
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
        providers=providers,
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
            await db.execute(delete(CalendarEvent).where(CalendarEvent.calendar_id == calendar.id))

            if connection.provider == CalendarProvider.GOOGLE.value:
                provider_events = await _list_google_events(
                    access_token,
                    calendar.provider_calendar_id,
                    window_start,
                    window_end,
                )
            else:
                provider_events = await _list_microsoft_events(
                    access_token,
                    calendar.provider_calendar_id,
                    window_start,
                    window_end,
                )

            for provider_event in provider_events:
                db.add(
                    CalendarEvent(
                        calendar_id=calendar.id,
                        provider_event_id=provider_event.remote_id,
                        title=provider_event.title,
                        status=provider_event.status,
                        is_all_day=provider_event.is_all_day,
                        starts_at=provider_event.starts_at,
                        ends_at=provider_event.ends_at,
                        start_date=provider_event.start_date,
                        end_date=provider_event.end_date,
                        source_url=provider_event.source_url,
                        external_updated_at=provider_event.external_updated_at,
                    )
                )

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


def _iter_event_dates(event: CalendarEvent) -> Iterable[date]:
    if event.is_all_day and event.start_date is not None and event.end_date is not None:
        current = event.start_date
        last_date = event.end_date - timedelta(days=1)
        while current <= last_date:
            yield current
            current += timedelta(days=1)
        return

    if event.starts_at is None or event.ends_at is None:
        return

    current = event.starts_at.date()
    end_moment = event.ends_at
    if end_moment > event.starts_at:
        end_moment = end_moment - timedelta(microseconds=1)
    last_date = end_moment.date()
    while current <= last_date:
        yield current
        current += timedelta(days=1)


def _to_dashboard_event(event: CalendarEvent, calendars_by_id: dict[int, CalendarSource], accounts_by_connection_id: dict[int, CalendarConnection]) -> CalendarDashboardEventRead:
    calendar = calendars_by_id[event.calendar_id]
    connection = accounts_by_connection_id[calendar.connection_id]
    account_label = connection.email or connection.display_name
    return CalendarDashboardEventRead(
        id=event.id,
        title=event.title,
        provider=connection.provider,
        calendar_name=calendar.name,
        account_label=account_label,
        is_all_day=event.is_all_day,
        starts_at=event.starts_at,
        ends_at=event.ends_at,
        start_date=event.start_date,
        end_date=event.end_date,
    )


async def get_dashboard_summary(db: AsyncSession, user: User, month: str) -> CalendarDashboardSummaryRead:
    try:
        viewed_month = datetime.strptime(month, "%Y-%m")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Month must use YYYY-MM format") from exc

    providers = await list_provider_statuses(db)
    provider_configured = any(provider.configured for provider in providers)
    statement = (
        select(CalendarConnection)
        .options(selectinload(CalendarConnection.calendars))
        .where(CalendarConnection.user_id == user.id)
    )
    connections = list((await db.execute(statement)).scalars().unique().all())
    selected_calendars = [calendar for connection in connections for calendar in connection.calendars if calendar.is_selected]

    month_start = _start_of_month(viewed_month)
    month_end = _add_months(month_start, 1)
    month_start_date = month_start.date()
    month_end_date = month_end.date()

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
        for event_date in _iter_event_dates(event):
            if month_start_date <= event_date < month_end_date:
                day_counts[event_date] = day_counts.get(event_date, 0) + 1

    agenda_items = [_to_dashboard_event(event, calendars_by_id, accounts_by_connection_id) for event in sorted(events, key=_event_sort_key)]

    next_event_obj: CalendarDashboardEventRead | None = None
    if selected_calendars:
        today = date.today()
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
        state=state,
        provider_configured=provider_configured,
        is_syncing=is_syncing,
        connection_count=len(connections),
        selected_calendar_count=len(selected_calendars),
        last_synced_at=last_synced_at,
        day_counts=[
            CalendarDashboardDayCountRead(date=event_date, count=count)
            for event_date, count in sorted(day_counts.items())
        ],
        agenda_items=agenda_items,
        next_event=next_event_obj,
    )