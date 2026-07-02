"""Low-level shared helpers: text/URL normalisation, error classification,
time, the sync window, and the generic JSON HTTP request wrapper.

Pure helpers with no calendar-domain state. Depends only on ``constants`` so it
can sit beneath ``oauth`` and ``providers`` in the import graph.
"""

from __future__ import annotations

import calendar as month_calendar
import html
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from backend.models.calendar import CalendarSyncStatus
from backend.utils.time import utc_now

from .constants import (
    HREF_URL_PATTERN,
    MEETING_URL_HOST_PRIORITY,
    PLAIN_URL_PATTERN,
    SYNC_WINDOW_MONTHS_BACK,
    SYNC_WINDOW_MONTHS_FORWARD,
    TRAILING_URL_PUNCTUATION,
    TRUSTED_MEETING_HOST_SUFFIXES,
    TRUSTED_MEETING_HOSTS,
)


def _utc_now() -> datetime:
    return utc_now()


def _parse_iso_datetime(
    value: str | None, *, default_tz: timezone | None = timezone.utc
) -> datetime | None:
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
    for candidate in [
        *HREF_URL_PATTERN.findall(text),
        *PLAIN_URL_PATTERN.findall(text),
    ]:
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
        hostname.endswith(suffix) for suffix in TRUSTED_MEETING_HOST_SUFFIXES
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
        direct_message = (
            payload.get("error_description")
            or payload.get("message")
            or payload.get("detail")
        )
        cleaned_direct_message = _clean_error_text(direct_message)
        if cleaned_direct_message:
            return cleaned_direct_message

        nested_error = payload.get("error")
        if isinstance(nested_error, str):
            return _clean_error_text(nested_error)
        if isinstance(nested_error, dict):
            nested_message = (
                nested_error.get("message")
                or nested_error.get("description")
                or nested_error.get("detail")
            )
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
        is_auth_error = status_code == 401 or any(
            hint in lower_http_message for hint in auth_hints
        )
        if status_code == 403 and any(
            hint in lower_http_message
            for hint in (
                "admin approval",
                "consent",
                "insufficient privileges",
                "access is denied",
                "forbidden",
            )
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
