"""Database persistence and read-model serialisation for calendar state.

Upserts connections, reconciles calendar sources, writes event models (full
replace and incremental apply), and serialises connections/sources into the
API read models, including live push-channel status.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import delete, select

from backend.core.encryption import encrypt_secret
from backend.models.calendar import (
    CalendarConnection,
    CalendarConnectionRead,
    CalendarEvent,
    CalendarPushChannel,
    CalendarPushChannelStatus,
    CalendarSource,
    CalendarSourceRead,
    CalendarSyncStatus,
)
from backend.models.user import User
from backend.utils.timezones import utc_naive_to_aware

from .models_dto import (
    ProviderCalendarRecord,
    ProviderEventRecord,
    ProviderIdentity,
    TokenBundle,
)
from .text_utils import _utc_now


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
    statement = select(CalendarSource).where(
        CalendarSource.connection_id == connection.id
    )
    existing_calendars = list((await db.execute(statement)).scalars().all())
    existing_by_remote_id = {
        calendar.provider_calendar_id: calendar for calendar in existing_calendars
    }
    selected_existing = {
        calendar.provider_calendar_id
        for calendar in existing_calendars
        if calendar.is_selected
    }
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
            calendar.is_selected = (
                provider_calendar.is_primary and not selected_existing
            )
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
    calendar_event.description = provider_event.description
    calendar_event.attendees = provider_event.attendees
    calendar_event.meeting_url = provider_event.meeting_url
    calendar_event.source_url = provider_event.source_url
    calendar_event.external_updated_at = provider_event.external_updated_at
    return calendar_event


def _build_calendar_event_model(
    calendar_id: int,
    provider_event: ProviderEventRecord,
) -> CalendarEvent:
    return _apply_provider_event_to_model(
        CalendarEvent(
            calendar_id=calendar_id,
            provider_event_id=provider_event.remote_id,
            title=provider_event.title,
        ),
        provider_event,
    )


async def _replace_calendar_events(
    db: AsyncSession,
    calendar_id: int,
    provider_events: list[ProviderEventRecord],
) -> None:
    await db.execute(
        delete(CalendarEvent).where(CalendarEvent.calendar_id == calendar_id)
    )
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

    changed_remote_ids = sorted(
        {provider_event.remote_id for provider_event in provider_events}
    )
    existing_events = list(
        (
            await db.execute(
                select(CalendarEvent).where(
                    CalendarEvent.calendar_id == calendar_id,
                    CalendarEvent.provider_event_id.in_(changed_remote_ids),
                )
            )
        )
        .scalars()
        .all()
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


def _serialise_connection(
    connection: CalendarConnection, *, push_active: bool = False
) -> CalendarConnectionRead:
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
        selected_calendar_count=sum(
            1 for calendar in calendars if calendar.is_selected
        ),
        push_active=push_active,
        calendars=[_serialise_source(calendar) for calendar in calendars],
    )


async def _active_push_connection_ids(
    db: AsyncSession, connection_ids: list[int]
) -> set[int]:
    """Connection ids with at least one live (active, unexpired) push channel."""
    if not connection_ids:
        return set()
    now = _utc_now()
    statement = (
        select(CalendarPushChannel.connection_id)
        .where(
            CalendarPushChannel.connection_id.in_(connection_ids),
            CalendarPushChannel.status == CalendarPushChannelStatus.ACTIVE.value,
            sa.or_(
                CalendarPushChannel.expiration.is_(None),
                CalendarPushChannel.expiration > now,
            ),
        )
        .distinct()
    )
    return set((await db.execute(statement)).scalars().all())


async def _serialise_connection_with_push(
    db: AsyncSession, connection: CalendarConnection
) -> CalendarConnectionRead:
    active_push_ids = await _active_push_connection_ids(db, [connection.id])
    return _serialise_connection(
        connection, push_active=connection.id in active_push_ids
    )
