"""Recurrence rules: their syntax, and the limits Proton's apps accept.

``parse_rrule`` checks an RRULE value for any provider. ``proton_problems``
mirrors the web client's ``getIsRruleSupported`` as proton-cal lists it, for
the Proton backend's ``validate``: Proton refuses anything else (code 2001),
and the helper checks again.
"""

from __future__ import annotations

import re
from datetime import date

from .tool_errors import CalendarToolError

FREQUENCIES = frozenset(
    {"SECONDLY", "MINUTELY", "HOURLY", "DAILY", "WEEKLY", "MONTHLY", "YEARLY"}
)
_KNOWN_PARTS = frozenset(
    {
        "FREQ",
        "INTERVAL",
        "COUNT",
        "UNTIL",
        "BYDAY",
        "BYMONTHDAY",
        "BYMONTH",
        "BYSETPOS",
        "BYYEARDAY",
        "BYWEEKNO",
        "BYHOUR",
        "BYMINUTE",
        "BYSECOND",
        "WKST",
    }
)
_UNTIL = re.compile(r"^\d{8}(T\d{6}Z?)?$")
_BYDAY = re.compile(r"^([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)$")
PROTON_FREQUENCIES = frozenset({"DAILY", "WEEKLY", "MONTHLY", "YEARLY"})
PROTON_MAX_COUNT = 49
PROTON_LAST_UNTIL = date(2037, 12, 31)
_PROTON_SETPOS = frozenset({-1, 1, 2, 3, 4})


def _refuse(message: str) -> CalendarToolError:
    return CalendarToolError("invalid_argument", message, field="recurrence")


def _positive_int(name: str, value: str) -> None:
    if not value.isdigit() or int(value) < 1:
        raise _refuse(f"{name} must be a whole number of at least 1.")


def _split(value: str) -> dict[str, str]:
    text = value.strip()
    if text.upper().startswith("RRULE:"):
        text = text[6:]
    parts: dict[str, str] = {}
    for item in filter(None, text.split(";")):
        name, sep, part = item.partition("=")
        name = name.strip().upper()
        if not sep or not part.strip():
            raise _refuse(f"recurrence part {item!r} is not NAME=VALUE.")
        if name not in _KNOWN_PARTS:
            raise _refuse(f"recurrence has an unknown part {name}.")
        if name in parts:
            raise _refuse(f"recurrence gives {name} twice.")
        parts[name] = part.strip().upper()
    return parts


def parse_rrule(value: str) -> dict[str, str]:
    """An RRULE value's parts, or ``invalid_argument`` naming recurrence."""
    parts = _split(value)
    if parts.get("FREQ") not in FREQUENCIES:
        raise _refuse("recurrence needs FREQ, such as FREQ=WEEKLY;BYDAY=TU;COUNT=10.")
    if "COUNT" in parts and "UNTIL" in parts:
        raise _refuse("recurrence takes COUNT or UNTIL, not both.")
    for name in ("COUNT", "INTERVAL"):
        if name in parts:
            _positive_int(name, parts[name])
    if "UNTIL" in parts and not _UNTIL.match(parts["UNTIL"]):
        raise _refuse("UNTIL must be a date such as 20271231 or 20271231T235959Z.")
    days = parts["BYDAY"].split(",") if "BYDAY" in parts else []
    bad_days = [day for day in days if not _BYDAY.match(day)]
    if bad_days:
        raise _refuse(f"BYDAY value {bad_days[0]!r} is not a day such as TU or 2TU.")
    return parts


def _until_date(value: str) -> date:
    return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))


def _monthly_problem(parts: dict[str, str]) -> str | None:
    days = [day for day in parts.get("BYDAY", "").split(",") if day]
    month_days = [day for day in parts.get("BYMONTHDAY", "").split(",") if day]
    if days and month_days:
        return "A monthly rule takes BYDAY or BYMONTHDAY, not both."
    if len(month_days) > 1 or len(days) > 1:
        return "A monthly rule takes one BYDAY or one BYMONTHDAY."
    if days:
        match = _BYDAY.match(days[0])
        ordinal = match.group(1) if match else None
        position = parts.get("BYSETPOS") or ordinal
        if position is None or int(position) not in _PROTON_SETPOS:
            return "A monthly BYDAY needs a position of 1, 2, 3, 4 or -1 (last)."
    return None


def proton_problems(parts: dict[str, str], first_day: date | None = None) -> list[str]:
    """Why Proton would refuse a parsed rule; empty when it accepts it."""
    problems: list[str] = []
    if parts["FREQ"] not in PROTON_FREQUENCIES:
        problems.append("Proton repeats events DAILY, WEEKLY, MONTHLY or YEARLY.")
    if "COUNT" in parts and int(parts["COUNT"]) > PROTON_MAX_COUNT:
        problems.append(
            f"Proton allows at most {PROTON_MAX_COUNT} occurrences by COUNT."
        )
    if "UNTIL" in parts and _until_date(parts["UNTIL"]) > PROTON_LAST_UNTIL:
        problems.append("Proton allows UNTIL no later than 2037-12-31.")
    if parts["FREQ"] == "MONTHLY":
        problem = _monthly_problem(parts)
        if problem:
            problems.append(problem)
    if parts["FREQ"] == "YEARLY" and "BYMONTHDAY" in parts and "BYMONTH" not in parts:
        problems.append("A yearly BYMONTHDAY needs BYMONTH.")
    if first_day is not None and parts["FREQ"] == "WEEKLY" and "BYDAY" in parts:
        weekday = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")[first_day.weekday()]
        if weekday not in parts["BYDAY"].split(","):
            problems.append(
                "The event must start on its first occurrence (a BYDAY day)."
            )
    return problems
