"""MCP tools for the user's calendars.

Read tools need ``mcp:read``. Each refusal is a ToolError whose text is one
JSON object, ``{"error": {"code", "message", "retryable", "field",
"details"}}`` (``backend/services/calendar_service/tool_errors.py``), so an
agent can act on the code whatever its client does with errors. The call log
keeps the code and never the details, which can hold addresses.
"""

import functools
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal, Optional, TypeVar

from annotated_types import Ge, Le, MaxLen
from mcp.server.mcpserver.exceptions import ToolError

from backend.api.params import PageOffset, RowId
from backend.mcp_server.auth import get_current_mcp_user
from backend.mcp_server.server import mcp_tool
from backend.services.calendar_service.event_refresh import refresh_event
from backend.services.calendar_service.event_view import (
    CalendarState,
    event_view,
)
from backend.services.calendar_service.free_busy import free_busy, resolve_window
from backend.services.calendar_service.reads import (
    EventFilters,
    event_counts,
    linked_recordings,
    list_events,
    resolve_series,
    user_calendars,
    user_event,
)
from backend.services.calendar_service.time_input import resolve_filter_bound
from backend.services.calendar_service.tool_errors import CalendarToolError
from backend.services.calendar_service.tool_failures import calendar_tool_error
from backend.utils.timezones import get_user_timezone_name, utc_naive_to_aware

T = TypeVar("T")


class CalendarRefusal(ToolError):
    """A calendar tool's refusal on the wire, logged by its code alone."""

    def __init__(self, error: CalendarToolError) -> None:
        super().__init__(error.to_json())
        self.code = error.code
        self.log_summary = error.log_summary()


def calendar_tool(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    """Turn the calendar service's refusals into the JSON ToolError."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        try:
            return await func(*args, **kwargs)
        except CalendarToolError as exc:
            raise CalendarRefusal(exc) from exc
        except Exception as exc:
            refusal = calendar_tool_error(exc)
            if refusal is None:
                raise
            raise CalendarRefusal(refusal) from exc

    return wrapper


def _user_zone(user: Any) -> str:
    return get_user_timezone_name(getattr(user, "settings", None))


@mcp_tool()
@calendar_tool
async def list_calendar_events(  # noqa: PLR0913 - one parameter per filter
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: Annotated[int, Ge(1), Le(100)] = 25,
    skip: PageOffset = 0,
    calendar_ids: Optional[list[RowId]] = None,
    query: Optional[Annotated[str, MaxLen(200)]] = None,
    status: Optional[Literal["confirmed", "tentative"]] = None,
    series_id: Optional[Annotated[str, MaxLen(600)]] = None,
    order: Literal["newest_first", "oldest_first"] = "newest_first",
) -> list[dict[str, Any]]:
    """List events from the user's selected calendars. Changes nothing.

    The range keeps every event that overlaps it, all-day events included:
    one that ends after start_date and starts before end_date. A bare date
    means that whole day in the user's time zone, so start_date 2026-12-01
    and end_date 2026-12-07 cover the first to the seventh inclusive. A
    date-time with an offset is exact; one without an offset is read as UTC.

    Use the numeric `id` with get_calendar_event, the write tools and
    link_calendar_event. Each occurrence of a recurring event is listed
    separately with its own id. `status` "tentative" lists holds. `query`
    matches title and location. The default order puts the latest events
    first, which suits finding the meeting a recording came from; pass order
    "oldest_first" with a start_date to read a diary forwards. At most
    `limit` events are returned; page with `skip` while a page comes back
    full.

    Each event says which calendar it came from, and `calendar.stale` is
    true when that calendar may be out of date.

    Args:
        start_date: Only events ending after this date or date-time.
        end_date: Only events starting before this date or date-time.
        limit: Maximum events to return (1-100, default 25).
        skip: Events to skip, for paging.
        calendar_ids: Only these calendars (ids from list_calendars).
        query: Text to find in the title or location.
        status: "confirmed", or "tentative" for holds.
        series_id: Only occurrences of this series (from an event's
            recurrence.series_id).
        order: "newest_first" (default) or "oldest_first".
    """
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    user_zone = _user_zone(user)
    start = resolve_filter_bound(
        start_date, field="start_date", user_zone=user_zone, is_end=False
    )
    end = resolve_filter_bound(
        end_date, field="end_date", user_zone=user_zone, is_end=True
    )

    async with async_session_maker() as db:
        states = _selected_only(
            await user_calendars(db, user, calendar_ids), calendar_ids
        )
        series_key = None
        if series_id is not None:
            series = await resolve_series(db, states, series_id)
            if series is None:
                return []
            calendar_id, series_key = series
            states = [state for state in states if state.source.id == calendar_id]
        rows = await list_events(
            db,
            states,
            EventFilters(
                start=start,
                end=end,
                query=query,
                status=status,
                series_key=series_key,
                newest_first=order == "newest_first",
            ),
            limit=limit,
            skip=skip,
        )
    return [event_view(event, state, user_zone=user_zone) for event, state in rows]


def _selected_only(
    states: list[CalendarState], calendar_ids: Optional[list[int]]
) -> list[CalendarState]:
    """The selected calendars; naming an unselected one is refused."""
    unselected = [state.source.id for state in states if not state.source.is_selected]
    if calendar_ids is not None and unselected:
        raise CalendarToolError(
            "invalid_argument",
            f"Calendar {unselected[0]} is not selected, so Nojoin does not keep "
            "its events. The user can select it in Settings > Integrations.",
            field="calendar_ids",
            details={"not_selected": unselected},
        )
    return [state for state in states if state.source.is_selected]


def _iso_date(value: Any) -> Optional[str]:
    return value.date().isoformat() if value else None


def _calendar_entry(state: CalendarState, event_count: int) -> dict[str, Any]:
    source, connection = state.source, state.connection
    capabilities = state.capabilities
    last_synced = utc_naive_to_aware(source.last_synced_at)
    synced_range = None
    if source.sync_window_start and source.sync_window_end:
        synced_range = {
            "from": _iso_date(source.sync_window_start),
            "to": _iso_date(source.sync_window_end),
        }
    return {
        "id": source.id,
        "name": source.name,
        "provider": connection.provider,
        "account": connection.email or connection.display_name,
        "connection_id": connection.id,
        "is_primary": source.is_primary,
        "selected": source.is_selected,
        "writable": capabilities.writable,
        "write_unavailable_reason": capabilities.reason,
        "time_zone": source.time_zone,
        "colour": source.user_colour or source.colour,
        "sync_status": connection.sync_status,
        "sync_error": connection.sync_error,
        "last_synced_at": last_synced.isoformat() if last_synced else None,
        "stale": state.stale,
        "synced_range": synced_range,
        "event_count": event_count,
        "capabilities": capabilities.as_dict(),
    }


@mcp_tool()
@calendar_tool
async def list_calendars(include_unselected: bool = False) -> list[dict[str, Any]]:
    """List the user's calendars from every connected calendar account. Changes nothing.

    Only selected calendars are kept in sync, and only they are searched by
    list_calendar_events and get_free_busy; pass include_unselected to see
    the rest. Before telling the user a time is free, check `stale` and
    `last_synced_at`: a stale calendar may be missing recent changes, and
    `synced_range` says how far back and ahead events are held.

    `writable` is true when create_calendar_event, update_calendar_event and
    cancel_calendar_event can change this calendar. When it is false,
    `write_unavailable_reason` says why: `provider_read_only` (Nojoin cannot
    change this provider's calendars yet), `calendar_read_only` (the user can
    only view this calendar), `not_selected`, or `reconnect_required`.

    Args:
        include_unselected: Also list calendars the user has not selected.
    """
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    async with async_session_maker() as db:
        states = await user_calendars(db, user)
        if not include_unselected:
            states = [state for state in states if state.source.is_selected]
        counts = await event_counts(db, [state.source.id for state in states])
    return [_calendar_entry(state, counts.get(state.source.id, 0)) for state in states]


@mcp_tool()
@calendar_tool
async def get_calendar_event(event_id: RowId, refresh: bool = False) -> dict[str, Any]:
    """Read one calendar event in full. Changes nothing at the calendar provider.

    Gives the whole description, every attendee with their response, the
    organiser, its recurrence and the recordings linked to it.

    By default this is Nojoin's copy from the last sync. Pass refresh true to
    read the event from the provider now, which is slower and also updates
    Nojoin's copy; do that before a write when the user may have just edited
    the event elsewhere.

    Args:
        event_id: The event's numeric id from list_calendar_events.
        refresh: Read the event from the provider now.
    """
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    user_zone = _user_zone(user)
    async with async_session_maker() as db:
        event, state = await user_event(db, user, event_id)
        if refresh:
            event = await refresh_event(db, event, state, user_zone=user_zone)
        linked = await linked_recordings(db, user, event_id)
    return event_view(
        event, state, user_zone=user_zone, full=True, linked_recordings=linked
    )


@mcp_tool()
@calendar_tool
async def get_free_busy(  # noqa: PLR0913 - one parameter per option
    start: str,
    end: str,
    calendar_ids: Optional[list[RowId]] = None,
    time_zone: Optional[str] = None,
    include_all_day: bool = False,
    include_holds: bool = True,
    min_free_minutes: Annotated[int, Ge(0), Le(1440)] = 0,
) -> dict[str, Any]:
    """Find when the user is busy and free across their selected calendars, or
    the ones calendar_ids names. Changes nothing.

    Busy time comes from Nojoin's synced copy: events shown as busy and not
    declined, holds (marked tentative), and all-day events only when
    include_all_day is true, since most all-day entries are reminders or
    holidays. The window is at most 62 days. start and end follow the same
    rules as create_calendar_event: a date-time with an offset is exact, one
    without needs time_zone; a bare date is that whole day. time_zone also
    sets the zone the answer is written in and defaults to the user's.

    `complete` is false when any calendar is stale or the window runs past
    what Nojoin has synced. Say so to the user rather than offering a slot as
    certainly free.

    Args:
        start: Start of the window.
        end: End of the window.
        calendar_ids: Only these calendars (ids from list_calendars).
        time_zone: IANA zone for naive times and for the answer.
        include_all_day: Count all-day events as busy.
        include_holds: Count holds as busy (default true).
        min_free_minutes: Leave out free gaps shorter than this.
    """
    from backend.core.db import async_session_maker

    user = get_current_mcp_user()
    window = resolve_window(start, end, time_zone=time_zone, user_zone=_user_zone(user))
    async with async_session_maker() as db:
        states = _selected_only(
            await user_calendars(db, user, calendar_ids), calendar_ids
        )
        return await free_busy(
            db,
            states,
            window=window,
            include_all_day=include_all_day,
            include_holds=include_holds,
            min_free_minutes=min_free_minutes,
        )
