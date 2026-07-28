"""Public connection management and sync orchestration.

The API-facing surface (overview, OAuth callback, selection/colour updates,
disconnect, manual refresh) plus the worker-facing incremental sync driver
(``sync_connection_in_session`` and the ``sync_*`` entry points). Push-service
imports are function-scoped so this module stays importable in the worker image
and keeps the calendar_service -> calendar_push_service dependency one-way at
import time.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from backend.core.db import async_session_maker
from backend.models.calendar import (
    CalendarConnection,
    CalendarConnectionRead,
    CalendarOverviewRead,
    CalendarProvider,
    CalendarSelectionUpdate,
    CalendarSource,
    CalendarSourceColourUpdate,
    CalendarSyncStatus,
)
from backend.models.user import User

from .config import (
    _prune_unreadable_connections,
    _reset_connection_requiring_reconnect,
    _serialise_provider_availability,
    get_provider_runtime_config,
    list_provider_statuses,
)
from .constants import PROVIDER_DISPLAY_NAMES
from .models_dto import (
    IncrementalSyncResetRequired,
    ProviderEventSyncResult,
    UnreadableCalendarConnectionState,
)
from .oauth import (
    _exchange_google_code,
    _exchange_microsoft_code,
    _fetch_google_identity,
    _fetch_microsoft_identity,
    _get_access_token_for_connection,
    _pop_oauth_state,
)
from .persistence import (
    _active_push_connection_ids,
    _apply_incremental_calendar_events,
    _can_use_incremental_sync,
    _microsoft_calendar_has_partial_occurrence_artifacts,
    _refresh_connection_calendars,
    _replace_calendar_events,
    _serialise_connection,
    _serialise_connection_with_push,
    _upsert_connection,
)
from .providers import (
    _get_microsoft_delta_cursor,
    _list_google_calendars,
    _list_microsoft_calendars,
    _list_microsoft_events,
    _sync_google_events,
    _sync_microsoft_events,
)
from .text_utils import (
    _build_sync_window,
    _classify_sync_failure,
    _normalise_colour_value,
    _utc_now,
)

# HTTPException is raised only from this module's API-facing helpers, never from
# the sync/reconcile path the Celery worker runs. The worker image ships no web
# framework, so import it lazily and tolerate its absence there.
try:
    from fastapi import HTTPException
except ModuleNotFoundError:  # pragma: no cover - worker image has no fastapi
    HTTPException = None  # type: ignore[assignment, misc]

logger = logging.getLogger(__package__)


async def _enqueue_push_channel_refresh(connection_id: int) -> None:
    """Best-effort background provisioning/renewal of push channels.

    Runs in the worker so API paths (connect, calendar selection) stay fast and
    are not coupled to the provider's synchronous webhook validation. Queuing it
    is itself a blocking socket call, so both callers being request handlers,
    the dispatch goes off the event loop too (ADR-0007).
    """
    # Imported inside the function to keep this module importable by the worker,
    # which has no ASGI stack.
    from backend.core.task_dispatch import dispatch_task_best_effort

    await dispatch_task_best_effort(
        "backend.worker.tasks.ensure_calendar_push_channels_task",
        args=[connection_id],
        context=f"connection {connection_id}",
    )


async def get_overview(db: AsyncSession, user: User) -> CalendarOverviewRead:
    providers = await list_provider_statuses(db)
    await _prune_unreadable_connections(db, user_id=user.id)
    statement = (
        select(CalendarConnection)
        .options(selectinload(CalendarConnection.calendars))
        .where(CalendarConnection.user_id == user.id)
        .order_by(
            CalendarConnection.provider.asc(), CalendarConnection.created_at.asc()
        )
    )
    connections = list((await db.execute(statement)).scalars().unique().all())
    active_push_ids = await _active_push_connection_ids(
        db, [connection.id for connection in connections]
    )
    return CalendarOverviewRead(
        providers=[
            _serialise_provider_availability(provider_status)
            for provider_status in providers
        ],
        connections=[
            _serialise_connection(
                connection, push_active=connection.id in active_push_ids
            )
            for connection in connections
        ],
    )


async def handle_callback(
    db: AsyncSession, provider: str, user: User, code: str, state: str
) -> CalendarConnection:
    runtime_config = await get_provider_runtime_config(db, provider)
    if not runtime_config.configured:
        raise HTTPException(
            status_code=400, detail="Calendar integration is not configured"
        )

    state_payload = await _pop_oauth_state(state)
    if not state_payload:
        raise HTTPException(
            status_code=400, detail="The calendar connection session expired"
        )
    if (
        int(state_payload.get("user_id", -1)) != user.id
        or state_payload.get("provider") != provider
    ):
        raise HTTPException(
            status_code=400, detail="The calendar connection session is invalid"
        )

    if provider == CalendarProvider.GOOGLE.value:
        token_bundle = await _exchange_google_code(
            runtime_config, code, str(state_payload["code_verifier"])
        )
        identity = await _fetch_google_identity(token_bundle.access_token)
        provider_calendars = await _list_google_calendars(token_bundle.access_token)
    else:
        token_bundle = await _exchange_microsoft_code(
            runtime_config, code, str(state_payload["code_verifier"])
        )
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
    except Exception:  # noqa: BLE001
        logger.warning(
            "Initial calendar sync failed for connection %s (%s)",
            connection.id,
            provider,
        )
    await db.refresh(connection)
    await _enqueue_push_channel_refresh(connection.id)
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
        .where(
            CalendarConnection.id == connection_id,
            CalendarConnection.user_id == user.id,
        )
    )
    connection = (await db.execute(statement)).scalars().unique().one_or_none()
    if connection is None:
        raise HTTPException(status_code=404, detail="Calendar connection not found")

    selected_ids = set(selection.selected_calendar_ids)
    for calendar in connection.calendars:
        calendar.is_selected = calendar.id in selected_ids
        db.add(calendar)
    connection.sync_status = (
        CalendarSyncStatus.IDLE.value if not selected_ids else connection.sync_status
    )
    if not selected_ids:
        connection.sync_error = None
    db.add(connection)
    await db.commit()

    if selected_ids:
        await sync_connection_in_session(db, connection.id)
    # Reconcile push channels: provision for newly selected calendars, stop for
    # deselected ones (including when the selection is now empty).
    await _enqueue_push_channel_refresh(connection.id)

    refreshed = (
        (
            await db.execute(
                select(CalendarConnection)
                .options(selectinload(CalendarConnection.calendars))
                .where(CalendarConnection.id == connection.id)
            )
        )
        .scalars()
        .unique()
        .one_or_none()
    )
    if refreshed is None:
        raise HTTPException(
            status_code=409,
            detail="Calendar connection was reset because its stored tokens could not be read. Reconnect the calendar account.",
        )
    return await _serialise_connection_with_push(db, refreshed)


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
        .where(
            CalendarConnection.id == connection_id,
            CalendarConnection.user_id == user.id,
        )
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
        (
            await db.execute(
                select(CalendarConnection)
                .options(selectinload(CalendarConnection.calendars))
                .where(CalendarConnection.id == connection.id)
            )
        )
        .scalars()
        .unique()
        .one()
    )
    return await _serialise_connection_with_push(db, refreshed)


async def disconnect_connection(
    db: AsyncSession, user: User, connection_id: int
) -> None:
    statement = select(CalendarConnection).where(
        CalendarConnection.id == connection_id,
        CalendarConnection.user_id == user.id,
    )
    connection = (await db.execute(statement)).scalar_one_or_none()
    if connection is None:
        raise HTTPException(status_code=404, detail="Calendar connection not found")

    # Stop provider-side push subscriptions while we still hold valid tokens.
    # The channel rows themselves cascade-delete with the connection.
    from backend.services.calendar_push_service import (
        teardown_push_channels_for_connection,
    )

    try:
        await teardown_push_channels_for_connection(db, connection)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to stop push channels for connection %s during disconnect: %s",
            connection.id,
            exc,
        )

    await db.delete(connection)
    await db.commit()


async def refresh_connection_now(
    db: AsyncSession, user: User, connection_id: int
) -> CalendarConnectionRead:
    statement = (
        select(CalendarConnection)
        .options(selectinload(CalendarConnection.calendars))
        .where(
            CalendarConnection.id == connection_id,
            CalendarConnection.user_id == user.id,
        )
    )
    connection = (await db.execute(statement)).scalars().unique().one_or_none()
    if connection is None:
        raise HTTPException(status_code=404, detail="Calendar connection not found")

    await sync_connection_in_session(db, connection.id)

    refreshed = (
        (
            await db.execute(
                select(CalendarConnection)
                .options(selectinload(CalendarConnection.calendars))
                .where(CalendarConnection.id == connection.id)
            )
        )
        .scalars()
        .unique()
        .one_or_none()
    )
    if refreshed is None:
        raise HTTPException(
            status_code=409,
            detail="Calendar connection was reset because its stored tokens could not be read. Reconnect the calendar account.",
        )
    return await _serialise_connection_with_push(db, refreshed)


async def sync_connection_in_session(db: AsyncSession, connection_id: int) -> None:
    statement = (
        select(CalendarConnection)
        .options(selectinload(CalendarConnection.calendars))
        .where(CalendarConnection.id == connection_id)
    )
    connection = (await db.execute(statement)).scalars().unique().one_or_none()
    if connection is None:
        return

    selected_calendars = [
        calendar for calendar in connection.calendars if calendar.is_selected
    ]
    if not selected_calendars:
        connection.sync_status = CalendarSyncStatus.IDLE.value
        connection.sync_error = None
        db.add(connection)
        await db.commit()
        return

    runtime_config = await get_provider_runtime_config(db, connection.provider)
    if not runtime_config.configured:
        connection.sync_status = CalendarSyncStatus.ERROR.value
        connection.sync_error = (
            f"{PROVIDER_DISPLAY_NAMES[connection.provider]} is not configured"
        )
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
        access_token = await _get_access_token_for_connection(
            db, connection, runtime_config
        )
        if connection.provider == CalendarProvider.GOOGLE.value:
            provider_calendars = await _list_google_calendars(access_token)
        else:
            provider_calendars = await _list_microsoft_calendars(access_token)

        refreshed_calendars = await _refresh_connection_calendars(
            db, connection, provider_calendars
        )
        selected_calendars = [
            calendar for calendar in refreshed_calendars if calendar.is_selected
        ]
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
                and await _microsoft_calendar_has_partial_occurrence_artifacts(
                    db, calendar.id
                )
            ):
                use_incremental_sync = False

            if connection.provider == CalendarProvider.GOOGLE.value:
                try:
                    sync_result = await _sync_google_events(
                        access_token,
                        calendar.provider_calendar_id,
                        window_start,
                        window_end,
                        sync_cursor=calendar.sync_cursor
                        if use_incremental_sync
                        else None,
                    )
                    if use_incremental_sync:
                        await _apply_incremental_calendar_events(
                            db,
                            calendar.id,
                            sync_result.events,
                            sync_result.deleted_remote_ids,
                        )
                    else:
                        await _replace_calendar_events(
                            db, calendar.id, sync_result.events
                        )
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
                        await _replace_calendar_events(
                            db, calendar.id, sync_result.events
                        )
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
    except UnreadableCalendarConnectionState:
        await _reset_connection_requiring_reconnect(db, connection)
        await db.commit()
        return
    except Exception as exc:  # noqa: BLE001
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
    # Keep push channels in step with the sync (background worker context only).
    # Best-effort: never let push provisioning failures affect the sync result.
    from backend.services.calendar_push_service import (
        ensure_push_channels_for_connection,
    )

    await ensure_push_channels_for_connection(connection_id)


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
