"""What a calendar write asks for, checked and turned into the neutral model.

Builds an ``EventDraft`` from a create call and an ``EventPatch`` from an
update, under the time rules in ``time_input``: times are exact or named in
an IANA zone, the event keeps the zone it is written in (the call's, else the
calendar's, else the user's), all-day ends are exclusive, and a span is at
most 366 days. Also turns drafts and stored rows into ``ProviderEventRecord``
for previews.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from backend.models.calendar import CalendarEvent
from backend.utils.timezones import (
    UTC_TIMEZONE,
    normalise_timezone_name,
    utc_naive_to_aware,
)

from .event_view import CalendarState
from .models_dto import ProviderEventRecord
from .rrule_limits import parse_rrule
from .time_input import parse_all_day, parse_moment, require_zone, to_utc_naive
from .tool_errors import CalendarToolError
from .write_backend import AttendeeDraft, EventDraft, EventPatch, EventTime

MAX_SPAN = timedelta(days=366)
_EMAIL = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")
CLEARABLE_FIELDS = ("description", "location", "meeting_url", "recurrence")
_TEXT_FIELDS = ("title", "description", "location", "meeting_url")


@dataclass
class CreateRequest:
    calendar_id: int
    title: str
    start: str
    end: str | None = None
    time_zone: str | None = None
    all_day: bool = False
    description: str | None = None
    location: str | None = None
    meeting_url: str | None = None
    status: str = "confirmed"
    show_as: str | None = None
    recurrence: str | None = None
    attendees: list[AttendeeDraft] = field(default_factory=list)
    notify_attendees: bool = False
    idempotency_key: str | None = None
    dry_run: bool = False


@dataclass
class UpdateRequest:
    event_id: int
    scope: str | None = None
    title: str | None = None
    start: str | None = None
    end: str | None = None
    time_zone: str | None = None
    all_day: bool | None = None
    description: str | None = None
    location: str | None = None
    meeting_url: str | None = None
    status: str | None = None
    show_as: str | None = None
    recurrence: str | None = None
    add_attendees: list[AttendeeDraft] = field(default_factory=list)
    remove_attendees: list[str] = field(default_factory=list)
    clear_fields: list[str] = field(default_factory=list)
    notify_attendees: bool = False
    expected_revision: str | None = None
    dry_run: bool = False


@dataclass
class CancelRequest:
    event_id: int
    scope: str | None = None
    notify_attendees: bool = False
    expected_revision: str | None = None
    dry_run: bool = False


@dataclass
class RespondRequest:
    event_id: int
    response: str
    note: str | None = None
    # Replies are emailed unless the call turns it off (maintainer, 28 Nov).
    notify_organiser: bool = True
    expected_revision: str | None = None
    dry_run: bool = False


def _casefold(value: str | None) -> str:
    return (value or "").strip().casefold()


def check_https(url: str | None, field_name: str = "meeting_url") -> str | None:
    if url is None:
        return None
    cleaned = url.strip()
    if not cleaned.lower().startswith("https://") or len(cleaned) <= len("https://"):
        raise CalendarToolError(
            "invalid_argument",
            "meeting_url must be an https:// link.",
            field=field_name,
        )
    return cleaned


def with_join_line(description: str | None, meeting_url: str | None) -> str | None:
    """The description with a final ``Join: <url>`` line, unless it has the link."""
    if not meeting_url or (description and meeting_url in description):
        return description
    join = f"Join: {meeting_url}"
    return f"{description.rstrip()}\n{join}" if description else join


def replace_join_line(
    description: str | None, old_url: str | None, new_url: str | None
) -> str | None:
    if old_url and description:
        lines = [line for line in description.split("\n") if line != f"Join: {old_url}"]
        description = "\n".join(lines) or None
    return with_join_line(description, new_url)


def normalise_attendees(
    attendees: list[AttendeeDraft], account_email: str | None, field_name: str
) -> tuple[list[AttendeeDraft], list[str]]:
    """Valid, distinct attendees other than the user, and a warning per drop."""
    kept: list[AttendeeDraft] = []
    seen: set[str] = set()
    warnings: list[str] = []
    own = _casefold(account_email)
    for attendee in attendees:
        email = attendee.email.strip()
        if not _EMAIL.match(email):
            raise CalendarToolError(
                "invalid_argument",
                f"{email!r} is not an email address.",
                field=field_name,
            )
        key = email.casefold()
        if own and key == own:
            warnings.append(
                "The user's own address was left out of the attendees; they "
                "organise the event."
            )
            continue
        if key in seen:
            continue
        seen.add(key)
        kept.append(replace(attendee, email=email))
    return kept, warnings


def event_zone(time_zone: str | None, state: CalendarState, user_zone: str) -> str:
    return (
        require_zone(time_zone)
        or normalise_timezone_name(state.source.time_zone)
        or normalise_timezone_name(user_zone)
        or UTC_TIMEZONE
    )


def _span_problem(start: EventTime, end: EventTime) -> None:
    if start.day is not None and end.day is not None:
        length = end.day - start.day
    elif start.at is not None and end.at is not None:
        length = end.at - start.at
    else:  # pragma: no cover - guarded by the parsers
        raise CalendarToolError("invalid_time", "start and end disagree.", field="end")
    if length <= timedelta(0):
        raise CalendarToolError("invalid_time", "end must be after start.", field="end")
    if length > MAX_SPAN:
        raise CalendarToolError(
            "invalid_time", "An event lasts at most 366 days.", field="end"
        )


def parse_time(
    value: str, *, field_name: str, all_day: bool, time_zone: str | None, user_zone: str
) -> EventTime:
    if all_day:
        return EventTime(day=parse_all_day(value, field=field_name))
    moment = parse_moment(
        value, field=field_name, time_zone=time_zone, user_zone=user_zone
    )
    return EventTime(at=moment.at)


def parse_span(
    start: str,
    end: str | None,
    *,
    all_day: bool,
    time_zone: str | None,
    user_zone: str,
) -> tuple[EventTime, EventTime]:
    """Start and end of a new event; an all-day end defaults to the next day."""
    first = parse_time(
        start,
        field_name="start",
        all_day=all_day,
        time_zone=time_zone,
        user_zone=user_zone,
    )
    if end is None:
        if not all_day:
            raise CalendarToolError(
                "invalid_argument", "end is required for a timed event.", field="end"
            )
        assert first.day is not None
        last = EventTime(day=first.day + timedelta(days=1))
    else:
        last = parse_time(
            end,
            field_name="end",
            all_day=all_day,
            time_zone=time_zone,
            user_zone=user_zone,
        )
    _span_problem(first, last)
    return first, last


def check_recurrence(value: str | None) -> str | None:
    if value is None:
        return None
    parse_rrule(value)
    text = value.strip()
    return text[6:] if text.upper().startswith("RRULE:") else text


def build_draft(
    request: CreateRequest, state: CalendarState, user_zone: str
) -> tuple[EventDraft, list[str]]:
    if request.status == "tentative" and request.notify_attendees:
        raise CalendarToolError(
            "invalid_argument",
            "A hold stays private until it is confirmed: create it with "
            "notify_attendees false, then confirm it with update_calendar_event "
            "(status confirmed, notify_attendees true) to invite its attendees.",
            field="notify_attendees",
        )
    start, end = parse_span(
        request.start,
        request.end,
        all_day=request.all_day,
        time_zone=request.time_zone,
        user_zone=user_zone,
    )
    attendees, warnings = normalise_attendees(
        request.attendees, state.connection.email, "attendees"
    )
    meeting_url = check_https(request.meeting_url)
    draft = EventDraft(
        title=request.title.strip(),
        start=start,
        end=end,
        time_zone=event_zone(request.time_zone, state, user_zone),
        all_day=request.all_day,
        description=with_join_line(request.description, meeting_url),
        location=request.location,
        meeting_url=meeting_url,
        status=request.status,
        show_as=request.show_as or "busy",
        recurrence=check_recurrence(request.recurrence),
        attendees=attendees,
        organiser={
            "name": state.connection.display_name or state.connection.email,
            "email": state.connection.email,
        },
    )
    if not draft.title:
        raise CalendarToolError(
            "invalid_argument", "title must not be empty.", field="title"
        )
    return draft, warnings


def _attendee_entries(attendees: list[AttendeeDraft]) -> list[dict[str, Any]]:
    return [
        {
            "name": attendee.name or attendee.email,
            "email": attendee.email,
            "response": "needs_action",
            "role": "optional" if attendee.optional else "required",
            "is_self": False,
        }
        for attendee in attendees
    ]


def draft_record(draft: EventDraft, remote_id: str, *, uid: str) -> ProviderEventRecord:
    """The record an event created from ``draft`` would have."""
    organiser = draft.organiser or {}
    return ProviderEventRecord(
        remote_id=remote_id,
        title=draft.title,
        status=draft.status,
        is_all_day=draft.all_day,
        starts_at=to_utc_naive(draft.start.at) if draft.start.at else None,
        ends_at=to_utc_naive(draft.end.at) if draft.end.at else None,
        start_date=draft.start.day,
        end_date=draft.end.day,
        location_text=draft.location,
        meeting_url=draft.meeting_url,
        description=draft.description,
        attendees=_attendee_entries(draft.attendees),
        start_time_zone=draft.time_zone,
        end_time_zone=draft.time_zone,
        show_as=draft.show_as,
        organiser={
            "name": organiser.get("name"),
            "email": organiser.get("email"),
            "is_self": True,
        },
        is_organiser=True,
        series_key=uid if draft.recurrence else None,
        occurrence_type="occurrence" if draft.recurrence else "single",
        recurrence_rule=draft.recurrence,
        provider_data={"uid": uid},
    )


_RECORD_FIELDS = (
    "title",
    "status",
    "is_all_day",
    "starts_at",
    "ends_at",
    "start_date",
    "end_date",
    "location_text",
    "meeting_url",
    "description",
    "attendees",
    "external_updated_at",
    "start_time_zone",
    "end_time_zone",
    "show_as",
    "organiser",
    "is_organiser",
    "my_response",
    "series_key",
    "original_start",
    "original_start_date",
    "occurrence_type",
    "recurrence_rule",
    "provider_revision",
    "provider_data",
)


def event_record(event: CalendarEvent) -> ProviderEventRecord:
    """A stored event as a provider record, for previews and comparisons."""
    values = {name: getattr(event, name) for name in _RECORD_FIELDS}
    values["attendees"] = [dict(entry) for entry in event.attendees or []]
    return ProviderEventRecord(remote_id=event.provider_event_id, **values)


def stored_zone(event: CalendarEvent, state: CalendarState, user_zone: str) -> str:
    return (
        normalise_timezone_name(event.start_time_zone)
        or normalise_timezone_name(state.source.time_zone)
        or normalise_timezone_name(user_zone)
        or UTC_TIMEZONE
    )


def stored_span(event: CalendarEvent) -> tuple[EventTime, EventTime]:
    if event.is_all_day and event.start_date is not None:
        end_date = event.end_date or event.start_date + timedelta(days=1)
        return EventTime(day=event.start_date), EventTime(day=end_date)
    start = utc_naive_to_aware(event.starts_at)
    end = utc_naive_to_aware(event.ends_at or event.starts_at)
    return EventTime(at=start), EventTime(at=end)


def same_time(first: EventTime, second: EventTime) -> bool:
    if first.day is not None or second.day is not None:
        return first.day == second.day
    if first.at is None or second.at is None:
        return first.at is second.at
    return to_utc_naive(first.at) == to_utc_naive(second.at)


def time_text(value: EventTime) -> str | None:
    if value.day is not None:
        return value.day.isoformat()
    return value.at.isoformat() if value.at else None


def first_day(value: EventTime, zone: str) -> date | None:
    if value.day is not None:
        return value.day
    if value.at is None:
        return None
    return value.at.astimezone(ZoneInfo(zone)).date()


def naive_utc(value: datetime | None) -> datetime | None:
    return to_utc_naive(value) if value else None


@dataclass
class PatchPlan:
    """An update's patch, and its changes as the result and change log show them."""

    patch: EventPatch
    changes: list[dict[str, Any]]
    start: EventTime
    end: EventTime
    status: str
    warnings: list[str] = field(default_factory=list)


def _new_span(
    request: UpdateRequest, event: CalendarEvent, zone_arg: str | None, user_zone: str
) -> tuple[EventTime, EventTime]:
    """The event's start and end after the update.

    Moving only the start keeps the event's length; switching between timed
    and all-day needs the new start.
    """
    all_day = event.is_all_day if request.all_day is None else request.all_day
    before_start, before_end = stored_span(event)
    if all_day != event.is_all_day and request.start is None:
        raise CalendarToolError(
            "invalid_argument",
            "start is required when all_day changes.",
            field="start",
        )
    options = {"all_day": all_day, "time_zone": zone_arg, "user_zone": user_zone}
    start = (
        parse_time(request.start, field_name="start", **options)
        if request.start is not None
        else before_start
    )
    if request.end is not None:
        end = parse_time(request.end, field_name="end", **options)
    elif request.start is None:
        end = before_end
    elif all_day:
        assert start.day is not None
        days = (
            before_end.day - before_start.day
            if before_start.day and before_end.day
            else timedelta(days=1)
        )
        end = EventTime(day=start.day + days)
    elif before_start.at is not None and before_end.at is not None and start.at:
        end = EventTime(at=start.at + (before_end.at - before_start.at))
    else:
        raise CalendarToolError(
            "invalid_argument", "end is required for a timed event.", field="end"
        )
    _span_problem(start, end)
    return start, end


def _text_value(request: UpdateRequest, name: str) -> tuple[bool, Any]:
    """Whether the update names a text field, and its new value."""
    if name in request.clear_fields:
        return True, None
    value = getattr(request, name)
    if value is None:
        return False, None
    return True, value.strip() if name == "title" else value


def _clear_fields_problem(request: UpdateRequest) -> None:
    for name in request.clear_fields:
        if name not in CLEARABLE_FIELDS:
            raise CalendarToolError(
                "invalid_argument",
                f"clear_fields takes {', '.join(CLEARABLE_FIELDS)}.",
                field="clear_fields",
            )
        if getattr(request, name) is not None:
            raise CalendarToolError(
                "invalid_argument",
                f"{name} is both set and named in clear_fields.",
                field="clear_fields",
            )


def _text_changes(request: UpdateRequest, event: CalendarEvent) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    current = {
        "title": event.title,
        "description": event.description,
        "location": event.location_text,
        "meeting_url": event.meeting_url,
    }
    for name in _TEXT_FIELDS:
        named, value = _text_value(request, name)
        if name == "meeting_url" and value is not None:
            value = check_https(value)
        if name == "title" and named and not value:
            raise CalendarToolError(
                "invalid_argument", "title must not be empty.", field="title"
            )
        if named and value != current[name]:
            changes[name] = value
    if "meeting_url" in changes:
        description = changes.get("description", event.description)
        if "description" in changes:
            description = with_join_line(description, changes["meeting_url"])
        else:
            description = replace_join_line(
                description, event.meeting_url, changes["meeting_url"]
            )
        if description != event.description:
            changes["description"] = description
    return changes


def _attendee_changes(
    request: UpdateRequest, event: CalendarEvent, account_email: str | None
) -> tuple[list[AttendeeDraft], list[str], list[str]]:
    present = {
        _casefold(entry.get("email")): entry
        for entry in event.attendees or []
        if isinstance(entry, dict) and entry.get("email")
    }
    added, warnings = normalise_attendees(
        request.add_attendees, account_email, "add_attendees"
    )
    added = [attendee for attendee in added if attendee.email.casefold() not in present]
    removed: list[str] = []
    for email in request.remove_attendees:
        key = _casefold(email)
        if key in present:
            removed.append(str(present[key].get("email")))
        else:
            warnings.append(f"{email} is not on the event, so it was not removed.")
    return added, removed, warnings


def _change(name: str, before: Any, after: Any) -> dict[str, Any]:
    return {"field": name, "before": before, "after": after}


def _setting_changes(
    request: UpdateRequest, event: CalendarEvent, zone_arg: str | None
) -> dict[str, Any]:
    """Changes to all_day, the zone, status, show_as and the recurrence."""
    values: dict[str, Any] = {}
    wanted = {
        "all_day": (request.all_day, event.is_all_day),
        "time_zone": (zone_arg, event.start_time_zone),
        "status": (request.status, event.status),
        "show_as": (request.show_as, event.show_as),
    }
    for name, (new, current) in wanted.items():
        if new is not None and new != current:
            values[name] = new
    if "recurrence" in request.clear_fields:
        if event.recurrence_rule:
            values["recurrence"] = None
    elif request.recurrence is not None:
        rule = check_recurrence(request.recurrence)
        if rule != event.recurrence_rule:
            values["recurrence"] = rule
    return values


def _current_values(event: CalendarEvent) -> dict[str, Any]:
    return {
        "title": event.title,
        "description": event.description,
        "location": event.location_text,
        "meeting_url": event.meeting_url,
        "all_day": event.is_all_day,
        "time_zone": event.start_time_zone,
        "status": event.status,
        "show_as": event.show_as,
        "recurrence": event.recurrence_rule,
    }


def build_patch(
    request: UpdateRequest, event: CalendarEvent, state: CalendarState, user_zone: str
) -> PatchPlan:
    _clear_fields_problem(request)
    zone_arg = require_zone(request.time_zone)
    start, end = _new_span(request, event, zone_arg, user_zone)
    before_start, before_end = stored_span(event)
    values: dict[str, Any] = {
        **_text_changes(request, event),
        **_setting_changes(request, event, zone_arg),
    }
    log: list[dict[str, Any]] = []
    for name, new_time, old_time in (
        ("start", start, before_start),
        ("end", end, before_end),
    ):
        if not same_time(new_time, old_time):
            values[name] = new_time
            log.append(_change(name, time_text(old_time), time_text(new_time)))
    added, removed, warnings = _attendee_changes(request, event, state.connection.email)
    log += [
        _change(name, current, values[name])
        for name, current in _current_values(event).items()
        if name in values
    ]
    if added or removed:
        before = [entry.get("email") for entry in event.attendees or []]
        gone = {email.casefold() for email in removed}
        after = [email for email in before if _casefold(email) not in gone]
        after += [attendee.email for attendee in added]
        log.append(_change("attendees", before, after))
    return PatchPlan(
        patch=EventPatch(changes=values, add_attendees=added, remove_attendees=removed),
        changes=log,
        start=start,
        end=end,
        status=values.get("status", event.status),
        warnings=warnings,
    )


def patched_record(
    event: CalendarEvent, plan: PatchPlan, zone: str
) -> ProviderEventRecord:
    """What the event would look like after the update, for a preview."""
    record = event_record(event)
    changes = plan.patch.changes
    for name, attribute in (
        ("title", "title"),
        ("description", "description"),
        ("location", "location_text"),
        ("meeting_url", "meeting_url"),
        ("status", "status"),
        ("show_as", "show_as"),
        ("recurrence", "recurrence_rule"),
    ):
        if name in changes:
            setattr(record, attribute, changes[name])
    record.is_all_day = plan.start.day is not None
    record.starts_at = naive_utc(plan.start.at)
    record.ends_at = naive_utc(plan.end.at)
    record.start_date = plan.start.day
    record.end_date = plan.end.day
    record.start_time_zone = changes.get("time_zone", record.start_time_zone) or zone
    gone = {email.casefold() for email in plan.patch.remove_attendees}
    record.attendees = [
        entry
        for entry in record.attendees or []
        if _casefold(entry.get("email")) not in gone
    ] + _attendee_entries(plan.patch.add_attendees)
    return record
