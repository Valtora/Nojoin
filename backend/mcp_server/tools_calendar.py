"""MCP tools for the user's calendars.

Read tools need ``mcp:read``; the four write tools need ``mcp:write``, and
change the user's real calendar where list_calendars shows it writable. The
writes run in ``backend/services/calendar_service/writes.py``. Each refusal is a ToolError whose text is one
JSON object, ``{"error": {"code", "message", "retryable", "field",
"details"}}`` (``backend/services/calendar_service/tool_errors.py``), so an
agent can act on the code whatever its client does with errors. The call log
keeps the code and never the details, which can hold addresses.
"""

import functools
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal, Optional, TypeVar

from annotated_types import Ge, Le, MaxLen, MinLen
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel

from backend.api.params import PageOffset, RowId
from backend.core.security import MCP_WRITE_SCOPE
from backend.mcp_server.auth import (
    current_mcp_grant_id,
    get_current_mcp_scopes,
    get_current_mcp_user,
)
from backend.mcp_server.server import mcp_tool
from backend.services.calendar_service import writes
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
from backend.services.calendar_service.write_audit import (
    Actor,
    client_name_for_grant,
)
from backend.services.calendar_service.write_backend import AttendeeDraft
from backend.services.calendar_service.write_inputs import (
    CancelRequest,
    CreateRequest,
    RespondRequest,
    UpdateRequest,
)
from backend.utils.timezones import get_user_timezone_name, utc_naive_to_aware

T = TypeVar("T")
_EVENTS_LIMIT_MAX = 100


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
    limit: int = 25,
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
    # Clamped, as the tool always did, so existing callers keep working.
    limit = max(1, min(int(limit), _EVENTS_LIMIT_MAX))
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


class AttendeeInput(BaseModel):
    email: Annotated[str, MaxLen(254)]
    name: Optional[Annotated[str, MaxLen(255)]] = None
    optional: bool = False


def _attendees(entries: Optional[list[AttendeeInput]]) -> list[AttendeeDraft]:
    return [
        AttendeeDraft(email=entry.email, name=entry.name, optional=entry.optional)
        for entry in entries or []
    ]


def _require_write_scope() -> None:
    """Refuse a grant without mcp:write, as the structured calendar error."""
    if MCP_WRITE_SCOPE not in get_current_mcp_scopes():
        raise CalendarToolError(
            "write_scope_missing",
            "This connection is read-only: its grant lacks the mcp:write scope. "
            "Ask the user to reconnect the Nojoin connector (remove and "
            "re-authorise it) to change their calendars.",
        )


async def _actor(db: Any, tool: str) -> Actor:
    user = get_current_mcp_user()
    grant_id = current_mcp_grant_id.get()
    return Actor(
        user=user,
        user_zone=_user_zone(user),
        tool=tool,
        grant_id=grant_id,
        client_name=await client_name_for_grant(db, grant_id),
    )


@mcp_tool(scope=MCP_WRITE_SCOPE)
@calendar_tool
async def create_calendar_event(  # noqa: PLR0913 - one parameter per event field
    calendar_id: RowId,
    title: Annotated[str, MinLen(1), MaxLen(255)],
    start: str,
    end: Optional[str] = None,
    time_zone: Optional[str] = None,
    all_day: bool = False,
    description: Optional[Annotated[str, MaxLen(8000)]] = None,
    location: Optional[Annotated[str, MaxLen(255)]] = None,
    meeting_url: Optional[Annotated[str, MaxLen(2048)]] = None,
    status: Literal["confirmed", "tentative"] = "confirmed",
    show_as: Optional[Literal["busy", "free"]] = None,
    recurrence: Optional[Annotated[str, MaxLen(500)]] = None,
    attendees: Optional[Annotated[list[AttendeeInput], MaxLen(100)]] = None,
    notify_attendees: bool = False,
    idempotency_key: Optional[Annotated[str, MinLen(8), MaxLen(128)]] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create an event on one of the user's calendars. Ask the user before calling this.

    What it changes: adds one event, or a recurring series when `recurrence`
    is set, to the calendar `calendar_id` names. That calendar must show
    `writable` true in list_calendars.

    Who it notifies: nobody unless notify_attendees is true. With it false,
    the default, no email or invitation is sent and nothing reaches the
    attendees; they are only written onto the event. With it true, every
    attendee is emailed an invitation from the user's own address, and the
    mail shows in the user's Sent folder. The result's `notifications` says
    who was emailed or would have been.

    Times: start and end are ISO 8601. A date-time with an offset
    (2026-12-01T10:00:00+00:00) is an exact moment. One without an offset
    needs time_zone, an IANA name such as Europe/London. time_zone is also
    the zone the event keeps, so a weekly 10:00 meeting stays at 10:00 when
    the clocks change; it defaults to the calendar's time zone. end is
    required for a timed event. For an all-day event set all_day and pass
    dates: end is the day after the last day (an event on 24 and 25 December
    ends 2026-12-26), and defaults to one day.

    recurrence is an RRULE value such as FREQ=WEEKLY;BYDAY=TU;COUNT=10.
    Proton accepts DAILY, WEEKLY, MONTHLY and YEARLY, at most 49 occurrences
    by COUNT, and UNTIL no later than 2037.

    Holds: status "tentative" creates a hold, a provisional booking the user
    has not confirmed. Holds show in list_calendar_events with is_hold true
    and block time in get_free_busy. Confirm one later with
    update_calendar_event (status "confirmed") or release it with
    cancel_calendar_event. A hold can list attendees, but nobody is invited
    until it is confirmed with notify_attendees true.

    dry_run true checks everything and returns the event that would be
    created and who would be notified, without creating anything.
    idempotency_key makes a retry safe: a second call with the same key
    returns the first call's event instead of creating another.

    Returns the event as the provider stored it, read back after the write.
    """
    from backend.core.db import async_session_maker

    _require_write_scope()
    request = CreateRequest(
        calendar_id=calendar_id,
        title=title,
        start=start,
        end=end,
        time_zone=time_zone,
        all_day=all_day,
        description=description,
        location=location,
        meeting_url=meeting_url,
        status=status,
        show_as=show_as,
        recurrence=recurrence,
        attendees=_attendees(attendees),
        notify_attendees=notify_attendees,
        idempotency_key=idempotency_key,
        dry_run=dry_run,
    )
    async with async_session_maker() as db:
        actor = await _actor(db, "create_calendar_event")
        return await writes.create_event(db, actor, request)


@mcp_tool(scope=MCP_WRITE_SCOPE)
@calendar_tool
async def update_calendar_event(  # noqa: PLR0913 - one parameter per event field
    event_id: RowId,
    scope: Optional[Literal["occurrence", "series"]] = None,
    title: Optional[Annotated[str, MinLen(1), MaxLen(255)]] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    time_zone: Optional[str] = None,
    all_day: Optional[bool] = None,
    description: Optional[Annotated[str, MaxLen(8000)]] = None,
    location: Optional[Annotated[str, MaxLen(255)]] = None,
    meeting_url: Optional[Annotated[str, MaxLen(2048)]] = None,
    status: Optional[Literal["confirmed", "tentative"]] = None,
    show_as: Optional[Literal["busy", "free"]] = None,
    recurrence: Optional[Annotated[str, MaxLen(500)]] = None,
    add_attendees: Optional[Annotated[list[AttendeeInput], MaxLen(100)]] = None,
    remove_attendees: Optional[Annotated[list[str], MaxLen(100)]] = None,
    clear_fields: Optional[
        list[Literal["description", "location", "meeting_url", "recurrence"]]
    ] = None,
    notify_attendees: bool = False,
    expected_revision: Optional[Annotated[str, MaxLen(255)]] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Change an event the user organises. Ask the user before calling this.

    What it changes: only the fields you pass; everything else on the event
    stays as it is. To empty a field, name it in clear_fields. For an
    invitation someone else sent, use respond_to_calendar_invitation
    instead; this tool refuses it.

    Recurring events: scope is required. "occurrence" changes only the
    occurrence event_id names. "series" changes every occurrence: start and
    end then give the new time of the occurrence you named, and the same
    change of time of day and length applies to the whole series. A series
    cannot be moved to another date this way. Changing a series' time or its
    recurrence drops any occurrences that were edited one by one, and the
    result says how many. Passing only start keeps the event's length.

    Holds: status "confirmed" confirms a hold; status "tentative" turns an
    event back into one.

    Who it notifies: nobody unless notify_attendees is true. With it false,
    attendees are not told about any change, including being added or
    removed. With it true, every attendee is emailed the update, new
    attendees get an invitation, and removed attendees get a cancellation,
    all from the user's address. Confirming a hold with notify_attendees
    true is how its attendees get invited.

    Pass the event's `revision` as expected_revision to refuse the change if
    the event was edited since you read it. dry_run true returns what would
    change and who would be notified, and changes nothing.

    Returns the event as the provider stored it, read back after the write.
    """
    from backend.core.db import async_session_maker

    _require_write_scope()
    request = UpdateRequest(
        event_id=event_id,
        scope=scope,
        title=title,
        start=start,
        end=end,
        time_zone=time_zone,
        all_day=all_day,
        description=description,
        location=location,
        meeting_url=meeting_url,
        status=status,
        show_as=show_as,
        recurrence=recurrence,
        add_attendees=_attendees(add_attendees),
        remove_attendees=list(remove_attendees or []),
        clear_fields=list(clear_fields or []),
        notify_attendees=notify_attendees,
        expected_revision=expected_revision,
        dry_run=dry_run,
    )
    async with async_session_maker() as db:
        actor = await _actor(db, "update_calendar_event")
        return await writes.update_event(db, actor, request)


@mcp_tool(scope=MCP_WRITE_SCOPE)
@calendar_tool
async def cancel_calendar_event(  # noqa: PLR0913 - one parameter per option
    event_id: RowId,
    scope: Optional[Literal["occurrence", "series"]] = None,
    notify_attendees: bool = False,
    expected_revision: Optional[Annotated[str, MaxLen(255)]] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Remove an event the user organises from their calendar at the provider.

    Ask the user before calling this. Nojoin cannot undo it; the change log
    in Nojoin keeps a copy of the event's details.

    Use it to release a hold. It refuses an invitation someone else sent: to
    turn one down, use respond_to_calendar_invitation with response
    "declined".

    Recurring events: scope is required. "occurrence" removes only the
    occurrence event_id names; "series" removes every occurrence, edited
    ones included.

    Who it notifies: nobody unless notify_attendees is true. With it false,
    attendees are not told, and anyone invited earlier still has the meeting
    in their calendar; the result warns when that is the case. With it true,
    every attendee who was invited is emailed a cancellation from the user's
    address.

    dry_run true returns what would be removed and who would be notified,
    and removes nothing.
    """
    from backend.core.db import async_session_maker

    _require_write_scope()
    request = CancelRequest(
        event_id=event_id,
        scope=scope,
        notify_attendees=notify_attendees,
        expected_revision=expected_revision,
        dry_run=dry_run,
    )
    async with async_session_maker() as db:
        actor = await _actor(db, "cancel_calendar_event")
        return await writes.cancel_event(db, actor, request)


@mcp_tool(scope=MCP_WRITE_SCOPE)
@calendar_tool
async def respond_to_calendar_invitation(  # noqa: PLR0913 - one parameter per option
    event_id: RowId,
    response: Literal["accepted", "declined", "tentative"],
    note: Optional[Annotated[str, MaxLen(500)]] = None,
    notify_organiser: bool = True,
    expected_revision: Optional[Annotated[str, MaxLen(255)]] = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Answer an invitation someone else sent: accept, decline or accept tentatively.

    Ask the user before calling this. The event must show can_respond true.

    What it changes: the user's answer on their copy of the event. For a
    recurring invitation the answer covers every occurrence; answering one
    occurrence alone is not supported yet.

    Who it notifies: the organiser IS EMAILED by default. With
    notify_organiser true, the default, a reply is emailed to the organiser
    from the user's address, carrying note if you pass one. Pass
    notify_organiser false to answer without sending any email; organisers
    who use Proton still see the answer in their own calendar, because Proton
    shares the event between the two accounts, and the result lists them
    under shown_by_provider. Other organisers then learn nothing. note needs
    notify_organiser true.

    dry_run true says what would change and who would hear (the reply shows
    under would_email), and changes nothing.
    """
    from backend.core.db import async_session_maker

    _require_write_scope()
    request = RespondRequest(
        event_id=event_id,
        response=response,
        note=note,
        notify_organiser=notify_organiser,
        expected_revision=expected_revision,
        dry_run=dry_run,
    )
    async with async_session_maker() as db:
        actor = await _actor(db, "respond_to_calendar_invitation")
        return await writes.respond_to_invitation(db, actor, request)
