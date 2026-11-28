"""Busy and free time across the user's calendars, from Nojoin's synced copy.

An event blocks time when it shows as busy and the user has not declined it.
All-day events count only when asked for, since most are reminders or
holidays, and holds count unless left out. Overlapping blocks merge, and a
block is tentative only when every event in it is a hold. ``complete`` is
false when a calendar is stale or the window runs past what was synced, so
an agent can say a slot is probably, not certainly, free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.models.calendar import CalendarEvent
from backend.utils.timezones import (
    local_midnight_utc,
    utc_naive_to_aware,
    utc_naive_to_timezone,
)

from .event_view import CalendarState, is_hold
from .time_input import is_date_only, parse_all_day, parse_moment, require_zone
from .tool_errors import CalendarToolError

MAX_WINDOW = timedelta(days=62)


@dataclass
class _Block:
    start: datetime
    end: datetime
    all_holds: bool
    calendar_ids: set[int] = field(default_factory=set)
    event_ids: set[int] = field(default_factory=set)


def resolve_window(
    start: str, end: str, *, time_zone: str | None, user_zone: str
) -> tuple[datetime, datetime, str]:
    """The window as naive UTC, and the zone the answer is written in.

    Date-times follow the write rules. A bare date is that whole day in the
    answer's zone, so an end date covers its own day.
    """
    zone = require_zone(time_zone) or user_zone

    def bound(value: str, name: str, is_end: bool) -> datetime:
        if is_date_only(value):
            day = parse_all_day(value, field=name)
            return local_midnight_utc(day + timedelta(days=1) if is_end else day, zone)
        return parse_moment(
            value, field=name, time_zone=time_zone, user_zone=user_zone
        ).utc

    window_start = bound(start, "start", False)
    window_end = bound(end, "end", True)
    if window_end <= window_start:
        raise CalendarToolError("invalid_time", "end must be after start.", field="end")
    if window_end - window_start > MAX_WINDOW:
        raise CalendarToolError(
            "invalid_argument",
            "The window is at most 62 days; ask for a shorter one.",
            field="end",
        )
    return window_start, window_end, zone


def _blocks_time(
    event: CalendarEvent, *, include_all_day: bool, include_holds: bool
) -> bool:
    if event.my_response == "declined":
        return False
    if event.is_all_day and not include_all_day:
        return False
    if is_hold(event):
        return include_holds
    return (event.show_as or "busy") == "busy"


def _merge(events: list[CalendarEvent], start: datetime, end: datetime) -> list[_Block]:
    blocks: list[_Block] = []
    for event in sorted(events, key=lambda item: (item.range_start, item.range_end)):
        event_start = max(event.range_start or start, start)
        event_end = min(event.range_end or end, end)
        if event_end <= event_start:
            continue
        hold = is_hold(event)
        if blocks and event_start <= blocks[-1].end:
            block = blocks[-1]
            block.end = max(block.end, event_end)
            block.all_holds = block.all_holds and hold
        else:
            block = _Block(start=event_start, end=event_end, all_holds=hold)
            blocks.append(block)
        block.calendar_ids.add(event.calendar_id)
        if event.id is not None:
            block.event_ids.add(event.id)
    return blocks


def _iso(value: datetime, zone: str) -> str:
    moment = utc_naive_to_timezone(value, zone)
    assert moment is not None
    return moment.isoformat()


def _free(
    blocks: list[_Block], start: datetime, end: datetime, min_minutes: int, zone: str
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    cursor = start
    for block in [*blocks, _Block(start=end, end=end, all_holds=False)]:
        minutes = int((block.start - cursor).total_seconds() // 60)
        if block.start > cursor and minutes >= min_minutes:
            gaps.append(
                {
                    "start": _iso(cursor, zone),
                    "end": _iso(block.start, zone),
                    "minutes": minutes,
                }
            )
        cursor = max(cursor, block.end)
    return gaps


def _covers(state: CalendarState, start: datetime, end: datetime) -> bool:
    source = state.source
    return bool(
        source.sync_window_start
        and source.sync_window_end
        and source.sync_window_start <= start
        and end <= source.sync_window_end
    )


async def free_busy(  # noqa: PLR0913 - one keyword per option of the tool
    db: AsyncSession,
    states: list[CalendarState],
    *,
    window: tuple[datetime, datetime, str],
    include_all_day: bool,
    include_holds: bool,
    min_free_minutes: int,
) -> dict[str, Any]:
    start, end, zone = window
    events: list[CalendarEvent] = []
    if states:
        events = list(
            (
                await db.execute(
                    select(CalendarEvent)
                    .where(CalendarEvent.calendar_id.in_([s.source.id for s in states]))
                    .where(CalendarEvent.status != "cancelled")
                    .where(CalendarEvent.range_end > start)
                    .where(CalendarEvent.range_start < end)
                )
            )
            .scalars()
            .all()
        )
    blocking = [
        event
        for event in events
        if _blocks_time(
            event, include_all_day=include_all_day, include_holds=include_holds
        )
    ]
    blocks = _merge(blocking, start, end)
    last_synced = {
        s.source.id: utc_naive_to_aware(s.source.last_synced_at) for s in states
    }
    return {
        "window": {
            "start": _iso(start, zone),
            "end": _iso(end, zone),
            "time_zone": zone,
        },
        "complete": all(not s.stale and _covers(s, start, end) for s in states),
        "calendars": [
            {
                "id": s.source.id,
                "name": s.source.name,
                "provider": s.connection.provider,
                "stale": s.stale,
                "last_synced_at": last_synced[s.source.id].isoformat()
                if last_synced[s.source.id]
                else None,
            }
            for s in states
        ],
        "busy": [
            {
                "start": _iso(block.start, zone),
                "end": _iso(block.end, zone),
                "tentative": block.all_holds,
                "calendar_ids": sorted(block.calendar_ids),
                "event_ids": sorted(block.event_ids),
            }
            for block in blocks
        ],
        "free": _free(blocks, start, end, max(min_free_minutes, 1), zone),
    }
