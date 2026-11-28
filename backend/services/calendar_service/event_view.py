"""The event view every calendar tool returns, and the calendar summary on it.

The first eight keys are exactly what ``list_calendar_events`` always gave
(``id``, ``title``, ``starts_at``, ``ends_at``, ``is_all_day``, ``status``,
``location``, ``meeting_url``), so a client that reads them keeps working:
``starts_at`` and ``ends_at`` stay naive UTC and null for all-day events. The
rest add offset-aware ``start`` and ``end`` in the event's own zone, people,
holds, recurrence and what the write tools would accept.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from backend.models.calendar import (
    CalendarConnection,
    CalendarEvent,
    CalendarSource,
    CalendarSyncStatus,
)
from backend.utils.timezones import (
    UTC_TIMEZONE,
    normalise_timezone_name,
    utc_naive_to_aware,
    utc_naive_to_timezone,
)

from .capabilities import WriteCapabilities, calendar_capabilities
from .text_utils import _utc_now

# Three missed 15-minute syncs.
STALE_AFTER = timedelta(minutes=45)
LIST_ATTENDEE_LIMIT = 10
LIST_DESCRIPTION_LIMIT = 300
_STALE_SYNC_STATUSES = frozenset(
    {
        CalendarSyncStatus.ERROR.value,
        CalendarSyncStatus.REAUTHORISATION_REQUIRED.value,
    }
)


@dataclass(frozen=True)
class CalendarState:
    """One calendar and its account, with what the view derives from them."""

    source: CalendarSource
    connection: CalendarConnection
    capabilities: WriteCapabilities
    stale: bool


def is_stale(
    source: CalendarSource, connection: CalendarConnection, now: datetime
) -> bool:
    """Whether a calendar's synced copy may be missing recent changes.

    An unselected calendar is not kept in sync at all, so it counts as stale.
    """
    if not source.is_selected or connection.sync_status in _STALE_SYNC_STATUSES:
        return True
    return source.last_synced_at is None or now - source.last_synced_at > STALE_AFTER


def calendar_state(
    source: CalendarSource,
    connection: CalendarConnection,
    now: datetime | None = None,
) -> CalendarState:
    return CalendarState(
        source=source,
        connection=connection,
        capabilities=calendar_capabilities(connection, source),
        stale=is_stale(source, connection, now or _utc_now()),
    )


def calendar_summary(state: CalendarState) -> dict[str, Any]:
    return {
        "id": state.source.id,
        "name": state.source.name,
        "provider": state.connection.provider,
        "writable": state.capabilities.writable,
        "stale": state.stale,
    }


def series_id(calendar_id: int, series_key: str) -> str:
    """An opaque id for a series, stable and carrying no provider detail."""
    digest = hashlib.sha256(f"{calendar_id}:{series_key}".encode()).hexdigest()[:20]
    return f"{calendar_id}-{digest}"


def series_calendar_id(value: str) -> int | None:
    """The calendar a ``series_id`` belongs to, or None if it is not one."""
    head, _, digest = value.partition("-")
    if not head.isdigit() or len(digest) != 20:
        return None
    return int(head)


def display_zone(
    event: CalendarEvent, source: CalendarSource, user_zone: str
) -> tuple[str, str]:
    """The zone an event is shown in, and where it came from."""
    for candidate, origin in (
        (event.start_time_zone, "event"),
        (source.time_zone, "calendar"),
        (user_zone, "user"),
    ):
        zone = normalise_timezone_name(candidate)
        if zone is not None:
            return zone, origin
    return UTC_TIMEZONE, "user"


def _iso(value: datetime | None, zone: str) -> str | None:
    moment = utc_naive_to_timezone(value, zone)
    return moment.isoformat() if moment else None


def _iso_date(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _old_fields(event: CalendarEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "title": event.title,
        "starts_at": event.starts_at.isoformat() if event.starts_at else None,
        "ends_at": event.ends_at.isoformat() if event.ends_at else None,
        "is_all_day": event.is_all_day,
        "status": event.status,
        "location": event.location_text,
        "meeting_url": event.meeting_url,
    }


def _times(event: CalendarEvent, zone: str) -> dict[str, Any]:
    if event.is_all_day and event.start_date is not None:
        end_date = event.end_date or event.start_date + timedelta(days=1)
        return {
            "start": {"date": event.start_date.isoformat()},
            "end": {"date": end_date.isoformat()},
            "last_date": (end_date - timedelta(days=1)).isoformat(),
        }
    end_zone = normalise_timezone_name(event.end_time_zone) or zone
    return {
        "start": {"date_time": _iso(event.starts_at, zone), "time_zone": zone},
        "end": {
            "date_time": _iso(event.ends_at or event.starts_at, end_zone),
            "time_zone": end_zone,
        },
        "last_date": None,
    }


def _is_you(entry: dict[str, Any], account_email: str | None) -> bool:
    if entry.get("is_self"):
        return True
    email = entry.get("email")
    return bool(
        account_email
        and isinstance(email, str)
        and email.strip().casefold() == account_email.strip().casefold()
    )


def _people(
    event: CalendarEvent, account_email: str | None, *, full: bool
) -> dict[str, Any]:
    attendees = [entry for entry in event.attendees or [] if isinstance(entry, dict)]
    organiser = None
    if isinstance(event.organiser, dict):
        organiser = {
            "name": event.organiser.get("name"),
            "email": event.organiser.get("email"),
            "is_you": _is_you(event.organiser, account_email),
        }
    shown = attendees if full else attendees[:LIST_ATTENDEE_LIMIT]
    return {
        "organiser": organiser,
        "is_organiser": event.is_organiser,
        "my_response": event.my_response if event.is_organiser is False else None,
        "attendee_count": len(attendees),
        "attendees": [
            {
                "name": entry.get("name"),
                "email": entry.get("email"),
                "response": entry.get("response"),
                "role": entry.get("role") or "required",
                "is_you": _is_you(entry, account_email),
            }
            for entry in shown
        ],
    }


def _recurrence(event: CalendarEvent, zone: str) -> dict[str, Any] | None:
    if not event.series_key:
        return None
    original_start = (
        _iso_date(event.original_start_date)
        if event.is_all_day
        else _iso(event.original_start, zone)
    )
    return {
        "series_id": series_id(event.calendar_id, event.series_key),
        "rule": event.recurrence_rule,
        "original_start": original_start,
        "is_exception": event.occurrence_type == "exception",
    }


def is_hold(event: CalendarEvent) -> bool:
    """A tentative event the user organises: a provisional booking."""
    return event.status == "tentative" and event.is_organiser is not False


def owns_event(event: CalendarEvent) -> bool:
    """The user organises it, or it has no attendees to answer to."""
    return event.is_organiser is True or (
        event.is_organiser is None and not event.attendees
    )


def permissions(
    event: CalendarEvent, capabilities: WriteCapabilities
) -> dict[str, bool]:
    own = owns_event(event)
    return {
        "can_edit": capabilities.update and own,
        "can_cancel": capabilities.cancel and own,
        "can_respond": capabilities.respond and event.is_organiser is False,
    }


def event_view(
    event: CalendarEvent,
    state: CalendarState,
    *,
    user_zone: str,
    full: bool = False,
    linked_recordings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The event as calendar tools return it; ``full`` adds the get-only fields."""
    zone, zone_source = display_zone(event, state.source, user_zone)
    description = event.description
    truncated = False
    if description and not full and len(description) > LIST_DESCRIPTION_LIMIT:
        description = description[:LIST_DESCRIPTION_LIMIT]
        truncated = True
    view: dict[str, Any] = {
        **_old_fields(event),
        **_times(event, zone),
        "time_zone": zone,
        "time_zone_source": zone_source,
        "is_hold": is_hold(event),
        "show_as": event.show_as or "busy",
        "calendar": calendar_summary(state),
        **_people(event, state.connection.email, full=full),
        "recurrence": _recurrence(event, zone),
        "description": description,
        "description_truncated": truncated,
        "revision": event.provider_revision,
        **permissions(event, state.capabilities),
        "updated_at": _iso_aware(event.external_updated_at),
    }
    if full:
        view["source_url"] = event.source_url
        view["linked_recordings"] = linked_recordings or []
    return view


def _iso_aware(value: datetime | None) -> str | None:
    moment = utc_naive_to_aware(value)
    return moment.isoformat() if moment else None
