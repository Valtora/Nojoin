"""Reading the user's synced calendars for the calendar tools.

Every query here is scoped to one user's connections. Events are matched by
the span they cover (``range_start``/``range_end``), so all-day events sort by
their date and fall inside date filters like any other.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.models.calendar import CalendarConnection, CalendarEvent, CalendarSource
from backend.models.recording import Recording
from backend.models.user import User

from .event_view import CalendarState, calendar_state, series_calendar_id, series_id
from .tool_errors import CalendarToolError


async def user_calendars(
    db: AsyncSession, user: User, calendar_ids: Sequence[int] | None = None
) -> list[CalendarState]:
    """The user's calendars, each with its account, ordered for display.

    With ``calendar_ids``, every id must be one of the user's calendars.
    """
    statement = (
        select(CalendarSource, CalendarConnection)
        .join(CalendarConnection, CalendarConnection.id == CalendarSource.connection_id)
        .where(CalendarConnection.user_id == user.id)
    )
    if calendar_ids is not None:
        statement = statement.where(CalendarSource.id.in_(list(calendar_ids)))
    rows = (await db.execute(statement)).all()
    states = [calendar_state(source, connection) for source, connection in rows]
    if calendar_ids is not None:
        missing = sorted(set(calendar_ids) - {state.source.id for state in states})
        if missing:
            raise CalendarToolError(
                "not_found",
                f"No calendar {missing[0]} among the user's calendars. Call "
                "list_calendars for their ids.",
                field="calendar_ids",
                details={"calendar_ids": missing},
            )
    states.sort(
        key=lambda state: (
            (state.connection.email or state.connection.display_name or "").lower(),
            state.connection.id,
            not state.source.is_primary,
            state.source.name.lower(),
        )
    )
    return states


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass(frozen=True)
class EventFilters:
    start: datetime | None = None
    end: datetime | None = None
    query: str | None = None
    status: str | None = None
    series_key: str | None = None
    newest_first: bool = True


async def _series_key_for(
    db: AsyncSession, calendar_id: int, wanted: str
) -> str | None:
    keys = (
        await db.execute(
            select(CalendarEvent.series_key)
            .where(CalendarEvent.calendar_id == calendar_id)
            .where(CalendarEvent.series_key.is_not(None))
            .distinct()
        )
    ).scalars()
    return next((key for key in keys if series_id(calendar_id, key) == wanted), None)


async def resolve_series(
    db: AsyncSession, states: list[CalendarState], wanted: str
) -> tuple[int, str] | None:
    """The calendar and provider series key a ``series_id`` names, if any."""
    calendar_id = series_calendar_id(wanted)
    if calendar_id is None or calendar_id not in {s.source.id for s in states}:
        return None
    key = await _series_key_for(db, calendar_id, wanted)
    return (calendar_id, key) if key is not None else None


def _filtered(statement: Any, filters: EventFilters) -> Any:
    if filters.start is not None:
        statement = statement.where(CalendarEvent.range_end > filters.start)
    if filters.end is not None:
        statement = statement.where(CalendarEvent.range_start < filters.end)
    if filters.status is not None:
        statement = statement.where(CalendarEvent.status == filters.status)
    if filters.series_key is not None:
        statement = statement.where(CalendarEvent.series_key == filters.series_key)
    if filters.query:
        pattern = f"%{_escape_like(filters.query.strip())}%"
        statement = statement.where(
            sa.or_(
                CalendarEvent.title.ilike(pattern, escape="\\"),
                CalendarEvent.location_text.ilike(pattern, escape="\\"),
            )
        )
    return statement


async def list_events(
    db: AsyncSession,
    states: list[CalendarState],
    filters: EventFilters,
    *,
    limit: int,
    skip: int,
) -> list[tuple[CalendarEvent, CalendarState]]:
    """Events on the given calendars that overlap the filters, one page."""
    by_id = {state.source.id: state for state in states}
    if not by_id:
        return []
    statement = (
        select(CalendarEvent)
        .where(CalendarEvent.calendar_id.in_(list(by_id)))
        .where(CalendarEvent.status != "cancelled")
    )
    statement = _filtered(statement, filters)
    if filters.newest_first:
        statement = statement.order_by(
            CalendarEvent.range_start.desc(), CalendarEvent.id.desc()
        )
    else:
        statement = statement.order_by(
            CalendarEvent.range_start.asc(), CalendarEvent.id.asc()
        )
    events = (await db.execute(statement.offset(skip).limit(limit))).scalars().all()
    return [(event, by_id[event.calendar_id]) for event in events]


async def event_counts(db: AsyncSession, calendar_ids: list[int]) -> dict[int, int]:
    if not calendar_ids:
        return {}
    rows = await db.execute(
        select(CalendarEvent.calendar_id, sa.func.count())
        .where(CalendarEvent.calendar_id.in_(calendar_ids))
        .where(CalendarEvent.status != "cancelled")
        .group_by(CalendarEvent.calendar_id)
    )
    return {int(calendar_id): int(count) for calendar_id, count in rows.all()}


async def user_event(
    db: AsyncSession, user: User, event_id: int
) -> tuple[CalendarEvent, CalendarState]:
    """One of the user's events with its calendar, or ``not_found``."""
    row = (
        await db.execute(
            select(CalendarEvent, CalendarSource, CalendarConnection)
            .join(CalendarSource, CalendarSource.id == CalendarEvent.calendar_id)
            .join(
                CalendarConnection,
                CalendarConnection.id == CalendarSource.connection_id,
            )
            .where(CalendarEvent.id == event_id)
            .where(CalendarConnection.user_id == user.id)
        )
    ).one_or_none()
    if row is None:
        raise CalendarToolError(
            "not_found",
            f"No calendar event {event_id} among the user's events. List them "
            "again with list_calendar_events.",
            field="event_id",
        )
    event, source, connection = row
    return event, calendar_state(source, connection)


async def linked_recordings(
    db: AsyncSession, user: User, event_id: int
) -> list[dict[str, Any]]:
    """The user's recordings linked to an event, outside the bin."""
    rows = await db.execute(
        select(Recording.public_id, Recording.name)
        .where(Recording.calendar_event_id == event_id)
        .where(Recording.user_id == user.id)
        .where(Recording.is_deleted.is_(False))
        .order_by(Recording.id)
    )
    return [{"id": public_id, "name": name} for public_id, name in rows.all()]
