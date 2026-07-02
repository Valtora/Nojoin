"""Dashboard summary aggregation for the month and agenda views.

Combines synced calendar events with unlinked Nojoin recordings into day
counts, agenda items, and the next-event surface, resolving per-user timezone,
speaker names, and tags.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Iterable

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from backend.models.calendar import (
    CalendarConnection,
    CalendarDashboardDayCountRead,
    CalendarDashboardEventRead,
    CalendarDashboardRecordingRead,
    CalendarDashboardState,
    CalendarDashboardSummaryRead,
    CalendarDashboardTagRead,
    CalendarEvent,
    CalendarSource,
    CalendarSyncStatus,
)
from backend.models.recording import Recording
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.tag import RecordingTag, Tag
from backend.models.user import User
from backend.utils.timezones import (
    get_timezone,
    get_user_timezone_name,
    today_in_timezone,
    utc_naive_to_aware,
    utc_naive_to_timezone,
)

from .config import _prune_unreadable_connections, list_provider_statuses
from .text_utils import _get_meeting_url_host, _is_trusted_meeting_url, _utc_now

# HTTPException is raised only from this module's API-facing helper, never from
# the worker path. The worker image ships no web framework, so import it lazily
# and tolerate its absence there.
try:
    from fastapi import HTTPException
except ModuleNotFoundError:  # pragma: no cover - worker image has no fastapi
    HTTPException = None  # type: ignore[assignment, misc]


def _event_sort_key(event: CalendarEvent) -> tuple[datetime, str]:
    if event.is_all_day and event.start_date is not None:
        return datetime.combine(
            event.start_date, datetime.min.time()
        ), event.title.lower()
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
    linked_recordings: list[Recording] | None = None,
    recording_speaker_names_by_id: dict[int, list[str]] | None = None,
    recording_tags_by_id: dict[int, list[CalendarDashboardTagRead]] | None = None,
) -> CalendarDashboardEventRead:
    calendar = calendars_by_id[event.calendar_id]
    connection = accounts_by_connection_id[calendar.connection_id]
    account_label = connection.email or connection.display_name
    meeting_url_host = _get_meeting_url_host(event.meeting_url)
    calendar_colour = getattr(calendar, "user_colour", None) or getattr(
        calendar, "colour", None
    )
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
        linked_recordings=[
            _to_dashboard_recording(
                recording,
                speaker_names=(recording_speaker_names_by_id or {}).get(recording.id),
                tags=(recording_tags_by_id or {}).get(recording.id),
            )
            for recording in sorted(
                linked_recordings or [],
                key=lambda recording: (
                    recording.created_at or datetime.min,
                    recording.name,
                    recording.public_id,
                ),
            )
        ],
    )


def _get_recording_end(recording: Recording) -> datetime | None:
    if recording.created_at is None:
        return None
    if recording.duration_seconds is None or recording.duration_seconds <= 0:
        return None
    return recording.created_at + timedelta(seconds=recording.duration_seconds)


def _recording_status_value(recording: Recording) -> str:
    status_value = getattr(recording.status, "value", recording.status)
    return str(status_value)


async def _get_dashboard_recording_speaker_names(
    db: AsyncSession,
    recording_ids: list[int],
) -> dict[int, list[str]]:
    if not recording_ids:
        return {}

    statement = (
        select(
            RecordingSpeaker.recording_id,
            RecordingSpeaker.local_name,
            RecordingSpeaker.name,
            RecordingSpeaker.diarization_label,
            RecordingSpeaker.merged_into_id,
            GlobalSpeaker.name,
        )
        .select_from(RecordingSpeaker)
        .outerjoin(
            GlobalSpeaker, RecordingSpeaker.global_speaker_id == GlobalSpeaker.id
        )
        .where(RecordingSpeaker.recording_id.in_(recording_ids))
        .where(RecordingSpeaker.speaker_status == "active")
        .where(
            sa.or_(
                sa.not_(RecordingSpeaker.diarization_label.like("LIVE_%")),
                RecordingSpeaker.local_name.isnot(None),
                RecordingSpeaker.global_speaker_id.isnot(None),
            )
        )
    )
    rows = (await db.execute(statement)).all()

    names_by_recording_id: dict[int, list[str]] = {}
    seen_by_recording_id: dict[int, set[str]] = {}
    for (
        recording_id,
        local_name,
        deprecated_name,
        diarization_label,
        merged_into_id,
        global_name,
    ) in rows:
        if merged_into_id is not None:
            continue

        display_name = local_name or global_name or deprecated_name or diarization_label
        if not display_name:
            continue

        clean_name = display_name.strip()
        if not clean_name:
            continue

        normalized_name = clean_name.casefold()
        seen_names = seen_by_recording_id.setdefault(recording_id, set())
        if normalized_name in seen_names:
            continue

        seen_names.add(normalized_name)
        names_by_recording_id.setdefault(recording_id, []).append(clean_name)

    for names in names_by_recording_id.values():
        names.sort(key=str.casefold)

    return names_by_recording_id


async def _get_dashboard_recording_tags(
    db: AsyncSession,
    recording_ids: list[int],
) -> dict[int, list[CalendarDashboardTagRead]]:
    if not recording_ids:
        return {}

    statement = (
        select(
            RecordingTag.recording_id,
            Tag.id,
            Tag.name,
            Tag.color,
        )
        .select_from(RecordingTag)
        .join(Tag, RecordingTag.tag_id == Tag.id)
        .where(RecordingTag.recording_id.in_(recording_ids))
    )
    rows = (await db.execute(statement)).all()

    tags_by_recording_id: dict[int, list[CalendarDashboardTagRead]] = {}
    for recording_id, tag_id, tag_name, tag_color in rows:
        tags_by_recording_id.setdefault(recording_id, []).append(
            CalendarDashboardTagRead(
                id=tag_id,
                name=tag_name,
                color=tag_color,
            )
        )

    for tags in tags_by_recording_id.values():
        tags.sort(key=lambda tag: (tag.name.casefold(), tag.id))

    return tags_by_recording_id


def _to_dashboard_recording(
    recording: Recording,
    *,
    speaker_names: list[str] | None = None,
    tags: list[CalendarDashboardTagRead] | None = None,
) -> CalendarDashboardRecordingRead:
    starts_at = utc_naive_to_aware(recording.created_at)
    ends_at = _get_recording_end(recording)
    return CalendarDashboardRecordingRead(
        id=recording.public_id,
        name=recording.name,
        starts_at=starts_at,
        ends_at=utc_naive_to_aware(ends_at),
        duration_seconds=recording.duration_seconds,
        status=_recording_status_value(recording),
        speaker_names=list(speaker_names or []),
        tags=list(tags or []),
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
        raise HTTPException(
            status_code=400, detail="Month must use YYYY-MM format"
        ) from exc

    effective_timezone = get_user_timezone_name(
        user.settings or {},
        fallback=timezone_name,
    )
    tz = get_timezone(effective_timezone)

    providers = await list_provider_statuses(db)
    provider_configured = any(provider.configured for provider in providers)
    await _prune_unreadable_connections(db, user_id=user.id)
    statement = (
        select(CalendarConnection)
        .options(selectinload(CalendarConnection.calendars))
        .where(CalendarConnection.user_id == user.id)
    )
    connections = list((await db.execute(statement)).scalars().unique().all())
    selected_calendars = [
        calendar
        for connection in connections
        for calendar in connection.calendars
        if calendar.is_selected
    ]

    month_start_local = datetime(viewed_month.year, viewed_month.month, 1, tzinfo=tz)
    if viewed_month.month == 12:
        month_end_local = datetime(viewed_month.year + 1, 1, 1, tzinfo=tz)
    else:
        month_end_local = datetime(
            viewed_month.year, viewed_month.month + 1, 1, tzinfo=tz
        )

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

    unlinked_recordings_statement = select(Recording).where(
        Recording.user_id == user.id,
        Recording.is_deleted.is_(False),
        Recording.is_archived.is_(False),
        Recording.calendar_event_id.is_(None),
        Recording.created_at >= month_start,
        Recording.created_at < month_end,
    )
    unlinked_recordings = list(
        (await db.execute(unlinked_recordings_statement)).scalars().all()
    )

    events: list[CalendarEvent] = []
    calendars_by_id = {calendar.id: calendar for calendar in selected_calendars}
    accounts_by_connection_id = {
        connection.id: connection for connection in connections
    }
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

    linked_recordings_by_event_id: dict[int, list[Recording]] = {}
    linked_recordings: list[Recording] = []
    if events:
        linked_recordings_statement = select(Recording).where(
            Recording.user_id == user.id,
            Recording.is_deleted.is_(False),
            Recording.is_archived.is_(False),
            Recording.calendar_event_id.in_([event.id for event in events]),
        )
        linked_recordings = list(
            (await db.execute(linked_recordings_statement)).scalars().all()
        )
        for recording in linked_recordings:
            if recording.calendar_event_id is None:
                continue
            linked_recordings_by_event_id.setdefault(
                recording.calendar_event_id, []
            ).append(recording)

    all_dashboard_recordings = [*unlinked_recordings, *linked_recordings]
    all_dashboard_recording_ids = [
        recording.id for recording in all_dashboard_recordings
    ]
    recording_speaker_names_by_id = await _get_dashboard_recording_speaker_names(
        db,
        all_dashboard_recording_ids,
    )
    recording_tags_by_id = await _get_dashboard_recording_tags(
        db,
        all_dashboard_recording_ids,
    )

    if events or unlinked_recordings:
        state = CalendarDashboardState.READY.value
    elif selected_calendars and not events:
        if any(
            connection.sync_status == CalendarSyncStatus.SYNCING.value
            for connection in connections
        ):
            state = CalendarDashboardState.SYNC_IN_PROGRESS.value
        else:
            state = CalendarDashboardState.NO_EVENTS.value

    day_counts: dict[date, int] = {}
    for event in events:
        for event_date in _iter_event_dates(event, effective_timezone):
            if month_start_date <= event_date < month_end_date:
                day_counts[event_date] = day_counts.get(event_date, 0) + 1

    for recording in unlinked_recordings:
        if recording.created_at is None:
            continue
        recording_date = utc_naive_to_timezone(
            recording.created_at, effective_timezone
        ).date()
        if month_start_date <= recording_date < month_end_date:
            day_counts[recording_date] = day_counts.get(recording_date, 0) + 1

    agenda_items = [
        _to_dashboard_event(
            event,
            calendars_by_id,
            accounts_by_connection_id,
            linked_recordings_by_event_id.get(event.id),
            recording_speaker_names_by_id,
            recording_tags_by_id,
        )
        for event in sorted(events, key=_event_sort_key)
    ]
    recording_items = [
        _to_dashboard_recording(
            recording,
            speaker_names=recording_speaker_names_by_id.get(recording.id),
            tags=recording_tags_by_id.get(recording.id),
        )
        for recording in sorted(
            unlinked_recordings,
            key=lambda recording: (
                recording.created_at or datetime.min,
                recording.name,
                recording.public_id,
            ),
        )
    ]

    next_event_obj: CalendarDashboardEventRead | None = None
    if selected_calendars:
        today = today_in_timezone(effective_timezone)
        now = _utc_now()
        future_statement = select(CalendarEvent).where(
            CalendarEvent.calendar_id.in_(list(calendars_by_id.keys())),
            sa.or_(
                sa.and_(
                    CalendarEvent.is_all_day.is_(True), CalendarEvent.end_date > today
                ),
                sa.and_(
                    CalendarEvent.is_all_day.is_(False), CalendarEvent.ends_at >= now
                ),
            ),
        )
        future_events = list((await db.execute(future_statement)).scalars().all())
        if future_events:
            next_event = sorted(future_events, key=_event_sort_key)[0]
            next_event_obj = _to_dashboard_event(
                next_event, calendars_by_id, accounts_by_connection_id
            )

    last_synced_at = max(
        (
            connection.last_synced_at
            for connection in connections
            if connection.last_synced_at is not None
        ),
        default=None,
    )
    is_syncing = any(
        connection.sync_status == CalendarSyncStatus.SYNCING.value
        for connection in connections
    )
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
        recording_items=recording_items,
        next_event=next_event_obj,
    )
