"""Provider calendar/event HTTP clients and payload normalisation.

Talks to the Google Calendar and Microsoft Graph REST APIs: lists calendars,
fetches/normalises events, and drives incremental sync via each provider's
change cursor (Google ``syncToken``, Microsoft Graph ``delta``). No web
framework and no database access here -- callers pass in the access token.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from http import HTTPStatus
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from .constants import (
    GOOGLE_CALENDAR_LIST_URL,
    GOOGLE_EVENTS_URL_TEMPLATE,
    MICROSOFT_EVENT_SELECT,
    MICROSOFT_GRAPH_URL,
)
from .models_dto import (
    IncrementalSyncResetRequired,
    ProviderCalendarRecord,
    ProviderEventRecord,
    ProviderEventSyncResult,
)
from .text_utils import (
    _get_microsoft_location_text,
    _is_partial_microsoft_occurrence,
    _normalise_text,
    _parse_iso_datetime,
    _pick_preferred_meeting_url,
    _request_json,
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
    next_url: str | None = (
        f"{MICROSOFT_GRAPH_URL}/me/calendars?$select=id,name,hexColor,canEdit,isDefaultCalendar"
    )
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
        entry.get("uri") for entry in conference_entry_points if isinstance(entry, dict)
    ]
    meeting_url = _pick_preferred_meeting_url(
        item.get("hangoutLink"),
        *conference_urls,
        location_text,
        item.get("description"),
    )
    description = item.get("description")
    attendees = [
        {
            "name": attendee.get("displayName") or attendee.get("email"),
            "email": attendee.get("email"),
        }
        for attendee in item.get("attendees", []) or []
        if isinstance(attendee, dict)
    ]
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
            description=description,
            attendees=attendees,
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
        description=description,
        attendees=attendees,
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
            if sync_cursor and response.status_code == HTTPStatus.GONE:
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
    join_url = (
        online_meeting.get("joinUrl") if isinstance(online_meeting, dict) else None
    )
    meeting_url = _pick_preferred_meeting_url(
        join_url,
        item.get("onlineMeetingUrl"),
        location_text,
        item.get("bodyPreview"),
        body_content,
    )
    description = item.get("bodyPreview")
    attendees = []
    for attendee in item.get("attendees", []) or []:
        if not isinstance(attendee, dict):
            continue
        if attendee.get("type") == "resource":
            continue
        email_address = attendee.get("emailAddress")
        if not isinstance(email_address, dict):
            continue
        address = email_address.get("address")
        attendees.append(
            {
                "name": email_address.get("name") or address,
                "email": address,
            }
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
            description=description,
            attendees=attendees,
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
        description=description,
        attendees=attendees,
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


def _build_microsoft_delta_url(
    calendar_id: str, window_start: datetime, window_end: datetime
) -> str:
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
    next_url: str | None = _build_microsoft_delta_url(
        calendar_id, window_start, window_end
    )
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
    next_url: str | None = sync_cursor or _build_microsoft_delta_url(
        calendar_id, window_start, window_end
    )
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
            if sync_cursor and response.status_code in {
                HTTPStatus.NOT_FOUND,
                HTTPStatus.GONE,
            }:
                raise IncrementalSyncResetRequired("Microsoft delta cursor expired")
            response.raise_for_status()
            payload = response.json()
            for item in payload.get("value", []):
                if item.get("@removed"):
                    remote_id = item.get("id")
                    if remote_id:
                        deleted_remote_ids.append(str(remote_id))
                    continue
                if item.get(
                    "type"
                ) == "seriesMaster" or _is_partial_microsoft_occurrence(item):
                    raise IncrementalSyncResetRequired(
                        "Microsoft recurring event delta requires full resync"
                    )
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
    return ProviderEventSyncResult(
        events=events, deleted_remote_ids=deleted_remote_ids, cursor=sync_cursor
    )
