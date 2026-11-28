"""Recurrence rules: the syntax every provider takes, and Proton's limits."""

from __future__ import annotations

from datetime import date

import pytest

from backend.services.calendar_service.rrule_limits import parse_rrule, proton_problems
from backend.services.calendar_service.tool_errors import CalendarToolError


def test_a_rule_parses_into_its_parts() -> None:
    assert parse_rrule("RRULE:FREQ=WEEKLY;BYDAY=TU,TH;COUNT=10") == {
        "FREQ": "WEEKLY",
        "BYDAY": "TU,TH",
        "COUNT": "10",
    }


@pytest.mark.parametrize(
    "rule",
    [
        "BYDAY=TU",
        "FREQ=FORTNIGHTLY",
        "FREQ=WEEKLY;COUNT=3;UNTIL=20271231",
        "FREQ=WEEKLY;COUNT=0",
        "FREQ=WEEKLY;BYDAY=TUESDAY",
        "FREQ=WEEKLY;FREQ=DAILY",
        "FREQ=WEEKLY;COLOUR=RED",
        "FREQ=WEEKLY;UNTIL=next-year",
    ],
)
def test_a_bad_rule_names_the_recurrence_field(rule) -> None:
    with pytest.raises(CalendarToolError) as refused:
        parse_rrule(rule)
    assert (refused.value.code, refused.value.field) == (
        "invalid_argument",
        "recurrence",
    )


@pytest.mark.parametrize(
    ("rule", "first_day", "problem"),
    [
        ("FREQ=WEEKLY;BYDAY=TU;COUNT=10", date(2026, 12, 1), None),
        ("FREQ=HOURLY;COUNT=3", None, "DAILY, WEEKLY"),
        ("FREQ=DAILY;COUNT=50", None, "at most 49"),
        ("FREQ=DAILY;UNTIL=20380101", None, "2037-12-31"),
        ("FREQ=MONTHLY;BYDAY=TU;BYSETPOS=2", None, None),
        ("FREQ=MONTHLY;BYDAY=-1FR", None, None),
        ("FREQ=MONTHLY;BYDAY=TU", None, "position"),
        ("FREQ=MONTHLY;BYMONTHDAY=1,15", None, "one BYDAY or one BYMONTHDAY"),
        ("FREQ=YEARLY;BYMONTHDAY=25", None, "needs BYMONTH"),
        ("FREQ=WEEKLY;BYDAY=WE", date(2026, 12, 1), "first occurrence"),
    ],
)
def test_proton_limits(rule, first_day, problem) -> None:
    problems = proton_problems(parse_rrule(rule), first_day)
    if problem is None:
        assert problems == []
    else:
        assert any(problem in item for item in problems), problems
