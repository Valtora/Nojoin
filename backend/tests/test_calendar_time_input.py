"""Times agents pass to the calendar tools, and how events read back.

Covers the write rules (an offset is exact, a naive time needs an IANA zone,
the gap and the overlap when the clocks change, an offset that disagrees with
its zone), the filter rules (a bare date is the user's whole day), all-day
dates, and a weekly meeting that keeps its wall-clock time across the change.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from backend.models.calendar import CalendarConnection, CalendarEvent, CalendarSource
from backend.services.calendar_service.event_view import calendar_state, event_view
from backend.services.calendar_service.time_input import (
    parse_all_day,
    parse_moment,
    resolve_filter_bound,
)
from backend.services.calendar_service.tool_errors import CalendarToolError

LONDON = "Europe/London"


def _refusal(call) -> CalendarToolError:
    with pytest.raises(CalendarToolError) as refused:
        call()
    return refused.value


def test_an_offset_is_an_exact_moment() -> None:
    moment = parse_moment(
        "2026-12-01T10:00:00+02:00", field="start", time_zone=None, user_zone=LONDON
    )
    assert moment.utc == datetime(2026, 12, 1, 8, 0)
    assert moment.zone is None

    zulu = parse_moment(
        "2026-12-01T10:00:00Z", field="start", time_zone=LONDON, user_zone=LONDON
    )
    assert zulu.utc == datetime(2026, 12, 1, 10, 0)
    assert zulu.zone == LONDON


def test_a_naive_time_is_read_in_the_named_zone() -> None:
    summer = parse_moment(
        "2026-07-01T10:00:00", field="start", time_zone=LONDON, user_zone=LONDON
    )
    new_york = parse_moment(
        "2026-12-01T10:00:00",
        field="start",
        time_zone="America/New_York",
        user_zone=LONDON,
    )
    assert summer.utc == datetime(2026, 7, 1, 9, 0)
    assert new_york.utc == datetime(2026, 12, 1, 15, 0)
    assert new_york.zone == "America/New_York"


def test_a_naive_time_without_a_zone_names_the_users_zone() -> None:
    error = _refusal(
        lambda: parse_moment(
            "2026-12-01T10:00:00", field="start", time_zone=None, user_zone=LONDON
        )
    )
    assert error.code == "invalid_time"
    assert error.field == "start"
    assert error.message == (
        "start has no UTC offset. Pass time_zone, for example Europe/London, "
        "the user's time zone in Nojoin."
    )


def test_an_unknown_zone_is_refused() -> None:
    error = _refusal(
        lambda: parse_moment(
            "2026-12-01T10:00:00",
            field="start",
            time_zone="Mars/Olympus",
            user_zone=LONDON,
        )
    )
    assert (error.code, error.field) == ("invalid_time", "time_zone")


@pytest.mark.parametrize(
    ("value", "zone_offset"),
    [
        ("2026-12-01T10:00:00+01:00", "+00:00"),  # London in winter
        ("2026-07-01T10:00:00+00:00", "+01:00"),  # London in summer
    ],
)
def test_an_offset_that_disagrees_with_the_zone_is_a_slip(value, zone_offset) -> None:
    error = _refusal(
        lambda: parse_moment(value, field="start", time_zone=LONDON, user_zone=LONDON)
    )
    assert error.code == "invalid_time"
    assert error.details == {"zone_offset": zone_offset}


def test_the_winter_slip_message() -> None:
    error = _refusal(
        lambda: parse_moment(
            "2026-12-01T10:00:00+01:00",
            field="start",
            time_zone=LONDON,
            user_zone=LONDON,
        )
    )
    assert error.message == (
        "start is 10:00+01:00, but Europe/London is on +00:00 on 1 December 2026."
    )


def test_the_spring_forward_gap_does_not_exist() -> None:
    error = _refusal(
        lambda: parse_moment(
            "2026-03-29T01:30:00", field="start", time_zone=LONDON, user_zone=LONDON
        )
    )
    assert error.code == "invalid_time"

    new_york = _refusal(
        lambda: parse_moment(
            "2026-03-08T02:30:00",
            field="start",
            time_zone="America/New_York",
            user_zone=LONDON,
        )
    )
    assert new_york.code == "invalid_time"


def test_the_autumn_overlap_is_ambiguous_unless_an_offset_picks_one() -> None:
    error = _refusal(
        lambda: parse_moment(
            "2026-10-25T01:30:00", field="start", time_zone=LONDON, user_zone=LONDON
        )
    )
    assert error.code == "ambiguous_time"
    assert error.details["candidates"] == [
        "2026-10-25T01:30:00+01:00",
        "2026-10-25T01:30:00+00:00",
    ]

    picked = parse_moment(
        "2026-10-25T01:30:00+00:00", field="start", time_zone=LONDON, user_zone=LONDON
    )
    assert picked.utc == datetime(2026, 10, 25, 1, 30)


def test_dates_are_for_all_day_events_only() -> None:
    assert parse_all_day("2026-12-24", field="start") == date(2026, 12, 24)
    assert (
        _refusal(lambda: parse_all_day("2026-12-24T10:00:00", field="start")).code
        == "invalid_time"
    )
    assert (
        _refusal(
            lambda: parse_moment(
                "2026-12-24", field="start", time_zone=None, user_zone=LONDON
            )
        ).code
        == "invalid_time"
    )


def test_filter_dates_cover_the_users_whole_day() -> None:
    start = resolve_filter_bound(
        "2026-07-01", field="start_date", user_zone=LONDON, is_end=False
    )
    end = resolve_filter_bound(
        "2026-07-07", field="end_date", user_zone=LONDON, is_end=True
    )
    naive = resolve_filter_bound(
        "2026-07-01T10:00:00", field="start_date", user_zone=LONDON, is_end=False
    )
    aware = resolve_filter_bound(
        "2026-07-01T10:00:00+02:00", field="start_date", user_zone=LONDON, is_end=False
    )

    assert start == datetime(2026, 6, 30, 23, 0)
    assert end == datetime(2026, 7, 7, 23, 0)
    assert naive == datetime(2026, 7, 1, 10, 0)
    assert aware == datetime(2026, 7, 1, 8, 0)


def _state(time_zone: str | None = None):
    connection = CalendarConnection(
        id=1,
        user_id=1,
        provider="google",
        provider_account_id="acct",
        email="alice@example.com",
        sync_status="success",
    )
    source = CalendarSource(
        id=1,
        connection_id=1,
        provider_calendar_id="cal",
        name="Personal",
        is_selected=True,
        time_zone=time_zone,
        last_synced_at=datetime.now(UTC).replace(tzinfo=None),
    )
    return calendar_state(source, connection)


def test_a_weekly_meeting_keeps_its_wall_clock_across_the_change() -> None:
    """Before and after 25 October the UTC instant moves; 10:00 stays 10:00."""
    state = _state()
    occurrences = [
        CalendarEvent(
            id=index,
            calendar_id=1,
            provider_event_id=f"weekly#{index}",
            title="Weekly sync",
            starts_at=starts_at,
            ends_at=starts_at.replace(hour=starts_at.hour + 1),
            start_time_zone=LONDON,
            series_key="weekly",
            occurrence_type="occurrence",
            original_start=starts_at,
        )
        for index, starts_at in enumerate(
            [datetime(2026, 10, 20, 9, 0), datetime(2026, 10, 27, 10, 0)], start=1
        )
    ]

    views = [event_view(event, state, user_zone="UTC") for event in occurrences]

    assert [view["start"]["date_time"] for view in views] == [
        "2026-10-20T10:00:00+01:00",
        "2026-10-27T10:00:00+00:00",
    ]
    # The old fields are UTC, so the instant visibly moves there.
    assert [view["starts_at"] for view in views] == [
        "2026-10-20T09:00:00",
        "2026-10-27T10:00:00",
    ]
    assert {view["recurrence"]["series_id"] for view in views} == {
        views[0]["recurrence"]["series_id"]
    }
    assert views[1]["recurrence"]["original_start"] == "2026-10-27T10:00:00+00:00"


def test_an_all_day_event_reads_with_an_exclusive_end_and_its_last_day() -> None:
    event = CalendarEvent(
        id=1,
        calendar_id=1,
        provider_event_id="xmas",
        title="Christmas",
        is_all_day=True,
        start_date=date(2026, 12, 24),
        end_date=date(2026, 12, 26),
    )
    view = event_view(event, _state(LONDON), user_zone="UTC")

    assert view["start"] == {"date": "2026-12-24"}
    assert view["end"] == {"date": "2026-12-26"}
    assert view["last_date"] == "2026-12-25"
    assert view["time_zone"] == LONDON
    assert view["time_zone_source"] == "calendar"
