"""Who, when and which series: the event fields beyond title and times.

Reads organiser, attendee responses, the user's own answer, holds, busy or
free, the series an occurrence belongs to, the zone the event was written in
and the provider's revision from Google and Microsoft payloads, in the
provider-neutral vocabulary ``ProviderEventRecord`` carries:

- responses: ``accepted``, ``declined``, ``tentative``, ``needs_action``;
- roles: ``required``, ``optional``, ``information``;
- ``show_as``: ``busy`` or ``free``;
- ``occurrence_type``: ``single``, ``occurrence`` or ``exception``.

Attendee entries keep ``name`` and ``email`` first and unchanged, since the
notes and Meeting Edge prompts read those two.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .models_dto import ProviderEventRecord
from .text_utils import _parse_iso_datetime
from .zones import iana_zone_name

_GOOGLE_RESPONSES = {
    "needsAction": "needs_action",
    "accepted": "accepted",
    "declined": "declined",
    "tentative": "tentative",
}
_MICROSOFT_RESPONSES = {
    "none": "needs_action",
    "notResponded": "needs_action",
    "accepted": "accepted",
    "declined": "declined",
    "tentativelyAccepted": "tentative",
    # Graph gives the organiser this status, on the event and on its entry.
    "organizer": "accepted",
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _google_attendee(attendee: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": attendee.get("displayName") or attendee.get("email"),
        "email": attendee.get("email"),
        "response": _GOOGLE_RESPONSES.get(str(attendee.get("responseStatus"))),
        "role": "optional" if attendee.get("optional") else "required",
        "is_self": bool(attendee.get("self")),
    }


def google_attendees(item: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _google_attendee(attendee)
        for attendee in item.get("attendees", []) or []
        if isinstance(attendee, dict)
    ]


def _google_people_fields(
    item: dict[str, Any], attendees: list[dict[str, Any]]
) -> dict[str, Any]:
    organizer = _dict(item.get("organizer"))
    organiser = None
    is_organiser: bool | None = None
    if organizer:
        is_organiser = bool(organizer.get("self"))
        organiser = {
            "name": organizer.get("displayName") or organizer.get("email"),
            "email": organizer.get("email"),
            "is_self": is_organiser,
        }
    my_response = None
    if is_organiser is False:
        my_response = next(
            (entry["response"] for entry in attendees if entry["is_self"]), None
        )
    return {
        "organiser": organiser,
        "is_organiser": is_organiser,
        "my_response": my_response,
    }


def _google_moment(payload: dict[str, Any]) -> tuple[datetime | None, date | None]:
    if payload.get("date"):
        return None, date.fromisoformat(str(payload["date"]))
    return _parse_iso_datetime(payload.get("dateTime")), None


def _google_series_fields(item: dict[str, Any]) -> dict[str, Any]:
    series_key = item.get("recurringEventId")
    if not series_key:
        return {"series_key": None, "occurrence_type": "single"}
    original_start, original_start_date = _google_moment(
        _dict(item.get("originalStartTime"))
    )
    start, start_date = _google_moment(_dict(item.get("start")))
    # Google flags no instance as edited; one moved off its slot is the edit
    # this can see. A retitled occurrence still reads as an occurrence.
    moved = (original_start, original_start_date) != (start, start_date)
    return {
        "series_key": str(series_key),
        "original_start": original_start,
        "original_start_date": original_start_date,
        "occurrence_type": "exception" if moved else "occurrence",
    }


def google_extra_fields(
    item: dict[str, Any], attendees: list[dict[str, Any]]
) -> dict[str, Any]:
    """The ProviderEventRecord fields beyond title, times and text."""
    return {
        **_google_people_fields(item, attendees),
        **_google_series_fields(item),
        "start_time_zone": iana_zone_name(_dict(item.get("start")).get("timeZone")),
        "end_time_zone": iana_zone_name(_dict(item.get("end")).get("timeZone")),
        "show_as": "free" if item.get("transparency") == "transparent" else "busy",
        "provider_revision": item.get("etag"),
    }


def google_status(item: dict[str, Any]) -> str:
    status = item.get("status")
    return "tentative" if status == "tentative" else "confirmed"


def microsoft_attendees(item: dict[str, Any]) -> list[dict[str, Any]]:
    attendees: list[dict[str, Any]] = []
    for attendee in item.get("attendees", []) or []:
        if not isinstance(attendee, dict) or attendee.get("type") == "resource":
            continue
        email_address = attendee.get("emailAddress")
        if not isinstance(email_address, dict):
            continue
        address = email_address.get("address")
        attendees.append(
            {
                "name": email_address.get("name") or address,
                "email": address,
                "response": _MICROSOFT_RESPONSES.get(
                    str(_dict(attendee.get("status")).get("response"))
                ),
                "role": "optional"
                if attendee.get("type") == "optional"
                else "required",
                # Graph does not mark the user's own entry; see mark_own_attendee.
                "is_self": False,
            }
        )
    return attendees


def _microsoft_original_start(
    item: dict[str, Any], zone_name: str | None
) -> tuple[datetime | None, date | None]:
    raw = item.get("originalStart")
    if not raw:
        return None, None
    if not item.get("isAllDay"):
        return _parse_iso_datetime(str(raw)), None
    moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if zone_name:
        moment = moment.astimezone(ZoneInfo(zone_name))
    return None, moment.date()


def microsoft_extra_fields(item: dict[str, Any]) -> dict[str, Any]:
    """The ProviderEventRecord fields beyond title, times and text."""
    is_organiser = item.get("isOrganizer")
    is_organiser = bool(is_organiser) if is_organiser is not None else None
    organizer = _dict(_dict(item.get("organizer")).get("emailAddress"))
    organiser = (
        {
            "name": organizer.get("name") or organizer.get("address"),
            "email": organizer.get("address"),
            "is_self": bool(is_organiser),
        }
        if organizer
        else None
    )
    my_response = None
    if is_organiser is False:
        my_response = _MICROSOFT_RESPONSES.get(
            str(_dict(item.get("responseStatus")).get("response"))
        )
    start_zone = iana_zone_name(item.get("originalStartTimeZone"))
    original_start, original_start_date = _microsoft_original_start(item, start_zone)
    event_type = item.get("type")
    return {
        "organiser": organiser,
        "is_organiser": is_organiser,
        "my_response": my_response,
        "series_key": item.get("seriesMasterId") or None,
        "original_start": original_start,
        "original_start_date": original_start_date,
        "occurrence_type": event_type
        if event_type in {"occurrence", "exception"}
        else "single",
        "start_time_zone": start_zone,
        "end_time_zone": iana_zone_name(item.get("originalEndTimeZone")),
        "show_as": "free" if item.get("showAs") == "free" else "busy",
        "provider_revision": item.get("changeKey"),
    }


def microsoft_status(item: dict[str, Any]) -> str:
    # A tentative event the user organises is their own hold; one they were
    # invited to and answered tentatively is my_response, not the status.
    if item.get("showAs") == "tentative" and item.get("isOrganizer"):
        return "tentative"
    return "confirmed"


def mark_own_attendee(
    events: list[ProviderEventRecord], account_email: str | None
) -> list[ProviderEventRecord]:
    """Mark the user's own entry among each event's attendees.

    For providers that do not flag it themselves (Microsoft), the entry whose
    address is the connected account's is the user's.
    """
    if not account_email:
        return events
    own = account_email.strip().casefold()
    for event in events:
        for attendee in event.attendees or []:
            email = attendee.get("email")
            if isinstance(email, str) and email.strip().casefold() == own:
                attendee["is_self"] = True
    return events
