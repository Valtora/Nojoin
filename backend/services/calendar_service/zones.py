"""Time-zone names from providers, and the span an event covers.

Microsoft reports an event's zone by its Windows name ("GMT Standard Time"),
Google and Proton by its IANA name. ``iana_zone_name`` turns any of these into
an IANA name or None. The Windows names map through CLDR's ``windowsZones``
table (territory 001), vendored as ``windows_zones.json`` with its source and
licence.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from functools import cache
from pathlib import Path

from backend.utils.timezones import (
    UTC_TIMEZONE,
    local_midnight_utc,
    normalise_timezone_name,
)

from .models_dto import ProviderEventRecord

_WINDOWS_ZONES_PATH = Path(__file__).with_name("windows_zones.json")
# Graph's name for UTC when an event was written in it.
_MICROSOFT_UTC_NAMES = frozenset(
    {"tzone://microsoft/utc", "utc", "coordinated universal time"}
)


@cache
def _windows_zone_map() -> dict[str, str]:
    payload = json.loads(_WINDOWS_ZONES_PATH.read_text(encoding="utf-8"))
    return {str(name): str(zone) for name, zone in payload["zones"].items()}


def iana_zone_name(value: str | None) -> str | None:
    """The IANA zone a provider's zone name stands for, or None if unknown."""
    if not value or not value.strip():
        return None
    cleaned = value.strip()
    if cleaned.lower() in _MICROSOFT_UTC_NAMES:
        return UTC_TIMEZONE
    direct = normalise_timezone_name(cleaned)
    if direct is not None:
        return direct
    mapped = _windows_zone_map().get(cleaned)
    if mapped is None:
        return None
    # CLDR names some zones by their legacy link ("Etc/UTC"); zoneinfo knows them.
    return normalise_timezone_name(mapped)


def event_range(
    event: ProviderEventRecord, range_zone: str | None
) -> tuple[datetime, datetime] | None:
    """The naive-UTC span an event covers, or None when it has no times.

    An all-day event covers midnight at its first date to midnight at its
    exclusive end date in ``range_zone`` (the calendar's zone, else the
    user's), or UTC when no zone is known.
    """
    if event.starts_at is not None:
        return event.starts_at, event.ends_at or event.starts_at
    if event.start_date is None:
        return None
    end_date: date = event.end_date or event.start_date + timedelta(days=1)
    zone = normalise_timezone_name(range_zone) or UTC_TIMEZONE
    return (
        local_midnight_utc(event.start_date, zone),
        local_midnight_utc(end_date, zone),
    )
