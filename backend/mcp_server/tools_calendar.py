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
from backend.services.calendar_service.event_view import event_view
from backend.services.calendar_service.reads import (
    EventFilters,
    list_events,
    resolve_series,
    user_calendars,
)
from backend.services.calendar_service.time_input import resolve_filter_bound
from backend.services.calendar_service.tool_errors import CalendarToolError
from backend.utils.timezones import get_user_timezone_name

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
        states = await user_calendars(db, user, calendar_ids)
        unselected = [
            state.source.id for state in states if not state.source.is_selected
        ]
        if calendar_ids is not None and unselected:
            raise CalendarToolError(
                "invalid_argument",
                f"Calendar {unselected[0]} is not selected, so Nojoin does not keep "
                "its events. The user can select it in Settings > Integrations.",
                field="calendar_ids",
                details={"not_selected": unselected},
            )
        states = [state for state in states if state.source.is_selected]
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
