"""Times an agent passes to the calendar tools, resolved to exact instants.

Two rule sets:

- **Filters** (list_calendar_events): a bare date is that whole day in the
  user's zone, a date-time with an offset is exact, and one without an offset
  is read as UTC, the meaning the tool always gave it.
- **Writes and free/busy**: a date-time with an offset is exact; one without
  needs an IANA ``time_zone`` and must exist exactly once there (not in the
  spring-forward gap, not twice in the autumn overlap); an offset given with a
  zone must be the zone's offset at that moment, since a mismatch is the usual
  sign of a daylight-saving slip. Dates appear only for all-day events.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from backend.utils.timezones import local_midnight_utc, normalise_timezone_name

from .tool_errors import CalendarToolError

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_date_only(value: str) -> bool:
    return bool(_DATE_ONLY.match(value.strip()))


def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise CalendarToolError(
            "invalid_time",
            f"{field} must be an ISO 8601 date such as 2026-12-24, got {value!r}.",
            field=field,
        ) from exc


def _parse_datetime(value: str, field: str) -> datetime:
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise CalendarToolError(
            "invalid_time",
            f"{field} must be an ISO 8601 date-time such as "
            f"2026-12-01T10:00:00+00:00, got {value!r}.",
            field=field,
        ) from exc


def to_utc_naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def resolve_filter_bound(
    value: str | None, *, field: str, user_zone: str, is_end: bool
) -> datetime | None:
    """One end of a list filter, as naive UTC, or None when not given.

    A bare end date runs to the end of that day, so 2026-12-01 to 2026-12-07
    covers the first to the seventh inclusive.
    """
    if value is None or not value.strip():
        return None
    if is_date_only(value):
        day = _parse_date(value, field)
        return local_midnight_utc(day + timedelta(days=1) if is_end else day, user_zone)
    parsed = _parse_datetime(value, field)
    if parsed.tzinfo is None:
        return parsed
    return to_utc_naive(parsed)


def require_zone(value: str | None, field: str = "time_zone") -> str | None:
    """An IANA zone name the caller passed, checked, or None when not passed."""
    if value is None:
        return None
    zone = normalise_timezone_name(value)
    if zone is None:
        raise CalendarToolError(
            "invalid_time",
            f"{field} {value!r} is not an IANA time zone such as Europe/London.",
            field=field,
        )
    return zone


def _format_offset(moment: datetime) -> str:
    offset = moment.utcoffset() or timedelta(0)
    sign = "-" if offset < timedelta(0) else "+"
    minutes = abs(int(offset.total_seconds())) // 60
    return f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def _long_date(day: date) -> str:
    return f"{day.day} {day.strftime('%B %Y')}"


@dataclass(frozen=True)
class Moment:
    """An exact instant, aware, and the zone it was written in."""

    at: datetime
    zone: str | None

    @property
    def utc(self) -> datetime:
        return to_utc_naive(self.at)


def _check_offset_matches_zone(parsed: datetime, zone: str, field: str) -> None:
    in_zone = parsed.astimezone(ZoneInfo(zone))
    if in_zone.utcoffset() == parsed.utcoffset():
        return
    raise CalendarToolError(
        "invalid_time",
        f"{field} is {parsed.strftime('%H:%M')}{_format_offset(parsed)}, but "
        f"{zone} is on {_format_offset(in_zone)} on {_long_date(in_zone.date())}.",
        field=field,
        details={"zone_offset": _format_offset(in_zone)},
    )


def _localise(naive: datetime, zone: str, field: str) -> datetime:
    tz = ZoneInfo(zone)
    early = naive.replace(tzinfo=tz, fold=0)
    late = naive.replace(tzinfo=tz, fold=1)

    def round_trips(candidate: datetime) -> bool:
        return candidate.astimezone(UTC).astimezone(tz).replace(tzinfo=None) == naive

    if not round_trips(early) and not round_trips(late):
        raise CalendarToolError(
            "invalid_time",
            f"{field} {naive.strftime('%H:%M')} does not exist in {zone} on "
            f"{_long_date(naive.date())}: the clocks go forward then.",
            field=field,
        )
    if early.utcoffset() != late.utcoffset():
        candidates = [
            early.astimezone(tz).isoformat(),
            late.astimezone(tz).isoformat(),
        ]
        raise CalendarToolError(
            "ambiguous_time",
            f"{field} {naive.strftime('%H:%M')} happens twice in {zone} on "
            f"{_long_date(naive.date())}: the clocks go back then. Pass it with "
            "the offset you mean.",
            field=field,
            details={"candidates": candidates},
        )
    return early


def parse_moment(
    value: str, *, field: str, time_zone: str | None, user_zone: str
) -> Moment:
    """A date-time a write or free/busy call names, under the write rules."""
    if is_date_only(value):
        raise CalendarToolError(
            "invalid_time",
            f"{field} is a date; pass a date-time, or set all_day for an "
            "all-day event.",
            field=field,
        )
    parsed = _parse_datetime(value, field)
    zone = require_zone(time_zone)
    if parsed.tzinfo is not None:
        if zone is not None:
            _check_offset_matches_zone(parsed, zone, field)
        return Moment(at=parsed, zone=zone)
    if zone is None:
        raise CalendarToolError(
            "invalid_time",
            f"{field} has no UTC offset. Pass time_zone, for example "
            f"{user_zone}, the user's time zone in Nojoin.",
            field=field,
            details={"user_time_zone": user_zone},
        )
    return Moment(at=_localise(parsed, zone, field), zone=zone)


def parse_all_day(value: str, *, field: str) -> date:
    """A date an all-day write names; a date-time is refused."""
    if not is_date_only(value):
        raise CalendarToolError(
            "invalid_time",
            f"{field} must be a date such as 2026-12-24 for an all-day event.",
            field=field,
        )
    return _parse_date(value, field)
