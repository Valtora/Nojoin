"""Reading one event fresh from Google or Microsoft, with the stored token.

``get_calendar_event(refresh=True)`` uses this through the read-only write
backend. An event the provider no longer has is deleted from Nojoin, as a
sync would, which unlinks any recording from it, and reported as not found.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.calendar import (
    CalendarConnection,
    CalendarEvent,
    CalendarProvider,
    CalendarSource,
    CalendarSyncStatus,
)
from backend.utils.timezones import normalise_timezone_name

from .config import get_provider_runtime_config
from .event_view import CalendarState
from .models_dto import (
    CalendarReauthorisationRequired,
    ProviderEventRecord,
    UnreadableCalendarConnectionState,
)
from .oauth import _get_access_token_for_connection
from .persistence import _apply_provider_event_to_model
from .provider_fields import mark_own_attendee
from .providers import _fetch_google_event, _fetch_microsoft_event
from .tool_errors import CalendarToolError
from .tool_failures import RECONNECT_MESSAGE


async def _mark_reconnect_required(
    db: AsyncSession, connection: CalendarConnection
) -> None:
    connection.sync_status = CalendarSyncStatus.REAUTHORISATION_REQUIRED.value
    connection.sync_error = (
        "This calendar account needs to be reconnected before it can sync again."
    )
    db.add(connection)
    await db.commit()


async def fetch_oauth_event(
    db: AsyncSession,
    connection: CalendarConnection,
    source: CalendarSource,
    provider_event_id: str,
) -> ProviderEventRecord | None:
    """The event as Google or Microsoft holds it now, or None when it is gone.

    A refused grant marks the connection for reconnecting and is raised as
    ``auth_expired``; other provider failures propagate for the caller to map.
    """
    runtime_config = await get_provider_runtime_config(db, connection.provider)
    if not runtime_config.configured:
        raise CalendarToolError(
            "provider_unavailable",
            "This calendar provider is not configured on this installation.",
        )
    try:
        access_token = await _get_access_token_for_connection(
            db, connection, runtime_config
        )
    except (CalendarReauthorisationRequired, UnreadableCalendarConnectionState) as exc:
        await _mark_reconnect_required(db, connection)
        raise CalendarToolError("auth_expired", RECONNECT_MESSAGE) from exc
    if connection.provider == CalendarProvider.GOOGLE.value:
        return await _fetch_google_event(
            access_token, source.provider_calendar_id, provider_event_id
        )
    record = await _fetch_microsoft_event(access_token, provider_event_id)
    if record is not None:
        mark_own_attendee([record], connection.email)
    return record


async def forget_event(db: AsyncSession, event: CalendarEvent) -> CalendarToolError:
    """Delete an event the provider no longer has; the refusal to report."""
    await db.delete(event)
    await db.commit()
    return CalendarToolError(
        "not_found",
        "The event was deleted at the provider since the last sync, and Nojoin "
        "has removed its copy. List the calendar again.",
    )


async def store_refreshed_event(
    db: AsyncSession,
    event: CalendarEvent,
    record: ProviderEventRecord | None,
    state: CalendarState,
    *,
    user_zone: str,
) -> CalendarEvent:
    """Save what the provider holds now over Nojoin's copy of an event."""
    if record is None:
        raise await forget_event(db, event)
    range_zone = normalise_timezone_name(state.source.time_zone) or user_zone
    _apply_provider_event_to_model(event, record, range_zone=range_zone)
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


async def refresh_event(
    db: AsyncSession, event: CalendarEvent, state: CalendarState, *, user_zone: str
) -> CalendarEvent:
    """Read one event fresh from its provider and update Nojoin's copy."""
    from .write_backend import write_backend_for
    from .write_steps import event_ref, provider_context

    backend = write_backend_for(state.connection.provider)
    record = await backend.get_event(
        provider_context(db, state, user_zone), event_ref(event, state)
    )
    return await store_refreshed_event(db, event, record, state, user_zone=user_zone)
