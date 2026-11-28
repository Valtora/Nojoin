"""Between the helper's shapes (proton/PROTOCOL.md) and Nojoin's neutral model.

Records the helper returns become ``ProviderEventRecord``; drafts and patches
become the helper's event fields; the helper's error codes become calendar
tool refusals. The ``Hold: `` title prefix is Python's job (maintainer
decision Q3): added when an event becomes a hold, stripped when it is
confirmed, while the hidden marker the helper writes stays the source of
truth.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from .models_dto import ProviderEventRecord
from .proton_helper import HelperError
from .text_utils import _parse_iso_datetime
from .tool_errors import ERROR_CODES, CalendarToolError
from .write_backend import AttendeeDraft, EventDraft, EventRef, EventTime

HOLD_PREFIX = "Hold: "
# The helper's argument names where the tools' differ.
_TOOL_FIELDS = {"recurrence_rule": "recurrence", "url": "meeting_url"}


def with_hold_prefix(title: str) -> str:
    return title if title.startswith(HOLD_PREFIX) else f"{HOLD_PREFIX}{title}"


def without_hold_prefix(title: str) -> str:
    return title[len(HOLD_PREFIX) :] if title.startswith(HOLD_PREFIX) else title


def _date(value: Any) -> date | None:
    return date.fromisoformat(str(value)) if value else None


def record_from_helper(item: dict[str, Any]) -> ProviderEventRecord:
    """One helper event record as the neutral record sync and writes store."""
    attendees = [
        {
            "name": entry.get("name") or entry.get("email"),
            "email": entry.get("email"),
            "response": entry.get("response"),
            "role": entry.get("role") or "required",
            "is_self": bool(entry.get("is_self")),
        }
        for entry in item.get("attendees") or []
        if isinstance(entry, dict)
    ]
    is_hold = bool(item.get("is_hold"))
    status = (
        "tentative" if is_hold or item.get("status") == "tentative" else "confirmed"
    )
    return ProviderEventRecord(
        remote_id=str(item["provider_event_id"]),
        title=str(item.get("title") or "Untitled event"),
        status=status,
        is_all_day=bool(item.get("is_all_day")),
        starts_at=_parse_iso_datetime(item.get("starts_at")),
        ends_at=_parse_iso_datetime(item.get("ends_at")),
        start_date=_date(item.get("start_date")),
        end_date=_date(item.get("end_date")),
        location_text=item.get("location"),
        meeting_url=item.get("meeting_url") or item.get("url"),
        description=item.get("description"),
        attendees=attendees,
        external_updated_at=_parse_iso_datetime(item.get("updated_at")),
        start_time_zone=item.get("start_time_zone"),
        end_time_zone=item.get("end_time_zone"),
        show_as="free" if item.get("show_as") == "free" else "busy",
        organiser=item.get("organiser"),
        is_organiser=item.get("is_organiser"),
        my_response=item.get("my_response"),
        series_key=item.get("series_key"),
        original_start=_parse_iso_datetime(item.get("original_start")),
        original_start_date=_date(item.get("original_start_date")),
        occurrence_type=str(item.get("occurrence_type") or "single"),
        recurrence_rule=item.get("recurrence_rule"),
        provider_revision=item.get("revision"),
        provider_data=item.get("provider_data") or {"uid": item.get("uid")},
    )


def records(items: list[Any] | None) -> list[ProviderEventRecord]:
    return [record_from_helper(item) for item in items or [] if isinstance(item, dict)]


def time_value(value: EventTime, zone: str) -> dict[str, Any]:
    if value.day is not None:
        return {"date": value.day.isoformat()}
    assert value.at is not None
    return {"date_time": value.at.isoformat(), "time_zone": zone}


def attendee_value(attendee: AttendeeDraft) -> dict[str, Any]:
    return {
        "email": attendee.email,
        "name": attendee.name,
        "role": "optional" if attendee.optional else "required",
    }


def event_fields(draft: EventDraft) -> dict[str, Any]:
    """A draft as the helper's event fields, the hold prefix added."""
    hold = draft.status == "tentative"
    return {
        "title": with_hold_prefix(draft.title) if hold else draft.title,
        "description": draft.description,
        "location": draft.location,
        "url": draft.meeting_url,
        "all_day": draft.all_day,
        "start": time_value(draft.start, draft.time_zone),
        "end": time_value(draft.end, draft.time_zone),
        "hold": hold,
        "show_as": draft.show_as,
        "recurrence_rule": draft.recurrence,
        "attendees": [attendee_value(attendee) for attendee in draft.attendees],
    }


def target(ref: EventRef) -> dict[str, Any]:
    """The helper's target for an event: its UID and, for an occurrence, the
    part of its id after ``#``."""
    uid = ref.provider_data.get("uid") or ref.uid
    recurrence_id = None
    if ref.provider_event_id and "#" in ref.provider_event_id:
        uid = uid or ref.provider_event_id.split("#", 1)[0]
        recurrence_id = ref.provider_event_id.split("#", 1)[1]
    return {"uid": uid or ref.provider_event_id, "recurrence_id": recurrence_id}


def window(start: datetime | None, end: datetime | None) -> dict[str, str] | None:
    if start is None or end is None:
        return None
    return {"start": f"{start.isoformat()}Z", "end": f"{end.isoformat()}Z"}


def chunks(start: datetime, end: datetime, days: int = 92) -> list[dict[str, str]]:
    """The sync window in pieces the helper accepts (at most 93 days each)."""
    pieces: list[dict[str, str]] = []
    cursor = start
    while cursor < end:
        piece_end = min(cursor + timedelta(days=days), end)
        pieces.append(
            {"start": f"{cursor.isoformat()}Z", "end": f"{piece_end.isoformat()}Z"}
        )
        cursor = piece_end
    return pieces


# Helper codes the caller handles; an assistant never sees them as they are.
_UNAVAILABLE = {"invalid_request", "output_too_large", "access_token_expired"}


def tool_error(error: HelperError) -> CalendarToolError:
    """The calendar refusal for a helper error (PROTOCOL.md, Error codes)."""
    code = error.code
    details = dict(error.details)
    if code in _UNAVAILABLE or code not in ERROR_CODES:
        code = "provider_unavailable" if code in _UNAVAILABLE else "provider_rejected"
    if code == "provider_rejected":
        details.setdefault("provider_code", error.provider_code)
    if code == "not_writable":
        details.setdefault("reason", "calendar_read_only")
    field = None
    if error.field:
        field = error.field.removeprefix("event.").removeprefix("patch.")
        field = _TOOL_FIELDS.get(field, field)
    return CalendarToolError(code, error.message, field=field, details=details)
