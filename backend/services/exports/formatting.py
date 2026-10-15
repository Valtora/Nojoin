"""Number and date formatting for exports.

The duration, timestamp, share and latency formats are ports of
``frontend/src/components/analytics/formatDuration.ts``, so a figure reads the
same in an export as on the Analytics tab. Change both together.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from backend.utils.timezones import get_timezone


def _js_round(value: float) -> int:
    """JavaScript's Math.round: halves round up, unlike Python's banker's round."""
    return math.floor(value + 0.5)


def format_duration(ms: float) -> str:
    """Durations for reading, not for precision."""
    total_seconds = _js_round(ms / 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def format_timestamp(ms: float) -> str:
    """A clock position: m:ss, or h:mm:ss from the first hour."""
    total_seconds = math.floor(ms / 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def format_share(share: float) -> str:
    value = _js_round(share * 1000) / 10
    return f"{value:g}%"


def format_latency(ms: float) -> str:
    """A reply gap. Tenths of a second is the finest the timestamps support."""
    if ms < 10_000:
        return f"{_js_round(ms / 100) / 10:.1f}s"
    return format_duration(ms)


def format_transcript_time(seconds: float) -> str:
    """The ``[MM:SS]`` stamp every transcript line carries in every format.

    Minutes run past 59 rather than gaining an hour field: the bracketed form
    is documented in docs/USAGE.md and readers may parse it.
    """
    minutes = int(seconds // 60)
    return f"[{minutes:02d}:{int(seconds % 60):02d}]"


def format_meeting_datetime(value: datetime | None, timezone_name: str) -> str:
    """'14 October 2026, 15:07 BST' in the reader's own timezone.

    The schema stores naive UTC, so the value is marked as UTC before it is
    converted.
    """
    if value is None:
        return "Unknown date"
    local = _to_local(value, timezone_name)
    zone = local.tzname() or timezone_name
    return f"{local.day} {local:%B %Y, %H:%M} {zone}"


def format_meeting_date(value: datetime | None, timezone_name: str) -> str:
    """'14 Oct 2026', for the letterhead."""
    if value is None:
        return ""
    local = _to_local(value, timezone_name)
    return f"{local.day} {local:%b %Y}"


def _to_local(value: datetime, timezone_name: str) -> datetime:
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(get_timezone(timezone_name))


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    word = singular if count == 1 else (plural_form or f"{singular}s")
    return f"{count} {word}"
