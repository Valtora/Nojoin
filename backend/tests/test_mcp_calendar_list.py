"""list_calendar_events: ordering, overlap filters, selection, staleness, paging.

The old eight fields stay byte-for-byte what the tool gave before, and an
all-day event sorts by its date: on Postgres, which puts nulls first when
sorting descending, bank holidays used to fill the first page.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import date, timedelta

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker

from backend.mcp_server.tools_calendar import list_calendar_events
from backend.models.calendar import CalendarEvent
from backend.models.user import User
from backend.services.calendar_service import event_view as event_view_module
from backend.services.calendar_service.event_view import series_id
from backend.tests.calendar_tool_support import (
    ALICE,
    BOB,
    NOW,
    add_account,
    add_calendar,
    add_event,
    bind_identity,
    postgres_engine,
    sqlite_engine,
    timed,
)

OLD_KEYS = [
    "id",
    "title",
    "starts_at",
    "ends_at",
    "is_all_day",
    "status",
    "location",
    "meeting_url",
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(params=["sqlite", "postgres"])
async def engine(request: pytest.FixtureRequest) -> AsyncIterator[AsyncEngine]:
    if request.param == "postgres":
        built = await postgres_engine(
            request.getfixturevalue("postgres_test_url"), "mcp_calendar_list_test"
        )
    else:
        built = await sqlite_engine()
    try:
        yield built
    finally:
        await built.dispose()


@pytest.fixture
def session_maker(engine: AsyncEngine, monkeypatch) -> sessionmaker:
    import backend.core.db as core_db

    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(core_db, "async_session_maker", maker)
    monkeypatch.setattr(event_view_module, "_utc_now", lambda: NOW)
    return maker


async def _alice(session_maker) -> User:
    async with session_maker() as session:
        user = await session.get(User, ALICE)
    assert user is not None
    bind_identity(user)
    return user


def _old_view(event: CalendarEvent) -> dict:
    """What list_calendar_events returned for an event before this change."""
    return {
        "id": event.id,
        "title": event.title,
        "starts_at": event.starts_at.isoformat() if event.starts_at else None,
        "ends_at": event.ends_at.isoformat() if event.ends_at else None,
        "is_all_day": event.is_all_day,
        "status": event.status,
        "location": event.location_text,
        "meeting_url": event.meeting_url,
    }


@pytest.mark.anyio
async def test_old_eight_fields_are_unchanged(session_maker) -> None:
    async with session_maker() as session:
        add_account(session, connection_id=1)
        await session.flush()
        add_calendar(session, calendar_id=1, connection_id=1)
        await session.flush()
        seeded = [
            add_event(
                session,
                event_id=1,
                calendar_id=1,
                location_text="Room 4",
                meeting_url="https://meet.example.com/abc",
                **timed(date(2026, 11, 20), 10),
            ),
            add_event(
                session,
                event_id=2,
                calendar_id=1,
                title="Bank holiday",
                start_date=date(2026, 11, 19),
                end_date=date(2026, 11, 20),
            ),
        ]
        await session.commit()
        expected = [_old_view(event) for event in seeded]

    await _alice(session_maker)
    events = await list_calendar_events()

    assert [list(event)[:8] for event in events] == [OLD_KEYS, OLD_KEYS]
    assert json.dumps([{k: e[k] for k in OLD_KEYS} for e in events]) == json.dumps(
        expected
    )
    assert events[0]["starts_at"] == "2026-11-20T10:00:00"
    assert events[1]["starts_at"] is None


@pytest.mark.anyio
async def test_bank_holidays_do_not_fill_the_first_page(session_maker) -> None:
    """All-day rows have no starts_at; ordering by it put them first."""
    async with session_maker() as session:
        add_account(session, connection_id=1)
        await session.flush()
        add_calendar(session, calendar_id=1, connection_id=1)
        await session.flush()
        for index in range(30):
            day = date(2026, 1, 1) + timedelta(days=7 * index)
            add_event(
                session,
                event_id=100 + index,
                calendar_id=1,
                title="Bank holiday",
                start_date=day,
                end_date=day + timedelta(days=1),
            )
        add_event(
            session,
            event_id=1,
            calendar_id=1,
            title="Yesterday's meeting",
            **timed(date(2026, 11, 27), 15),
        )
        await session.commit()

    await _alice(session_maker)
    page = await list_calendar_events(limit=5)

    assert page[0]["title"] == "Yesterday's meeting"
    assert sum(event["is_all_day"] for event in page) == 4


@pytest.mark.anyio
async def test_all_day_events_interleave_by_date_and_ties_break_on_id(
    session_maker,
) -> None:
    async with session_maker() as session:
        add_account(session, connection_id=1)
        await session.flush()
        add_calendar(session, calendar_id=1, connection_id=1)
        await session.flush()
        add_event(session, event_id=1, calendar_id=1, **timed(date(2026, 12, 1), 9))
        add_event(
            session,
            event_id=2,
            calendar_id=1,
            title="Holiday",
            start_date=date(2026, 12, 2),
            end_date=date(2026, 12, 3),
        )
        add_event(session, event_id=3, calendar_id=1, **timed(date(2026, 12, 3), 9))
        add_event(session, event_id=4, calendar_id=1, **timed(date(2026, 12, 3), 9))
        await session.commit()

    await _alice(session_maker)
    newest = await list_calendar_events()
    oldest = await list_calendar_events(order="oldest_first")

    assert [event["id"] for event in newest] == [4, 3, 2, 1]
    assert [event["id"] for event in oldest] == [1, 2, 3, 4]


@pytest.mark.anyio
async def test_filters_keep_overlapping_and_all_day_events(session_maker) -> None:
    async with session_maker() as session:
        add_account(session, connection_id=1)
        await session.flush()
        add_calendar(session, calendar_id=1, connection_id=1)
        await session.flush()
        # Alice is in London: 23:30 on 7 December there is still the 7th.
        add_event(
            session,
            event_id=1,
            calendar_id=1,
            title="Late call",
            **timed(date(2026, 12, 7), 23, 30),
        )
        add_event(
            session,
            event_id=2,
            calendar_id=1,
            title="Away",
            start_date=date(2026, 11, 30),
            end_date=date(2026, 12, 2),
        )
        add_event(
            session,
            event_id=3,
            calendar_id=1,
            title="Before",
            **timed(date(2026, 11, 29), 9),
        )
        add_event(
            session,
            event_id=4,
            calendar_id=1,
            title="After",
            **timed(date(2026, 12, 8), 9),
        )
        await session.commit()

    await _alice(session_maker)
    week = await list_calendar_events(start_date="2026-12-01", end_date="2026-12-07")
    # A naive date-time keeps its old meaning, UTC.
    morning = await list_calendar_events(
        start_date="2026-12-08T08:30:00", end_date="2026-12-08T09:30:00"
    )

    assert [event["title"] for event in week] == ["Late call", "Away"]
    assert [event["title"] for event in morning] == ["After"]


@pytest.mark.anyio
async def test_only_selected_calendars_and_their_stale_flags(session_maker) -> None:
    async with session_maker() as session:
        add_account(session, connection_id=1)
        add_account(session, connection_id=2, sync_status="error")
        add_account(session, connection_id=3, user_id=BOB, email="bob@example.org")
        await session.flush()
        add_calendar(session, calendar_id=1, connection_id=1, name="Fresh")
        add_calendar(
            session,
            calendar_id=2,
            connection_id=1,
            name="Old",
            last_synced_at=NOW - timedelta(hours=2),
        )
        add_calendar(session, calendar_id=3, connection_id=1, selected=False)
        add_calendar(session, calendar_id=4, connection_id=2, name="Broken")
        add_calendar(session, calendar_id=5, connection_id=3, name="Bob's")
        await session.flush()
        for calendar_id in range(1, 6):
            add_event(
                session,
                event_id=calendar_id,
                calendar_id=calendar_id,
                **timed(date(2026, 12, 1), 8 + calendar_id),
            )
        await session.commit()

    await _alice(session_maker)
    events = await list_calendar_events(order="oldest_first")

    assert [(e["calendar"]["name"], e["calendar"]["stale"]) for e in events] == [
        ("Fresh", False),
        ("Old", True),
        ("Broken", True),
    ]
    assert events[0]["calendar"] == {
        "id": 1,
        "name": "Fresh",
        "provider": "google",
        "writable": False,
        "stale": False,
    }

    with pytest.raises(ToolError) as unselected:
        await list_calendar_events(calendar_ids=[3])
    assert json.loads(str(unselected.value))["error"]["code"] == "invalid_argument"

    with pytest.raises(ToolError) as foreign:
        await list_calendar_events(calendar_ids=[5])
    error = json.loads(str(foreign.value))["error"]
    assert error["code"] == "not_found"
    assert error["field"] == "calendar_ids"
    assert error["retryable"] is False


@pytest.mark.anyio
async def test_paging_query_status_and_series_filters(session_maker) -> None:
    async with session_maker() as session:
        add_account(session, connection_id=1)
        await session.flush()
        add_calendar(session, calendar_id=1, connection_id=1)
        await session.flush()
        for index in range(5):
            add_event(
                session,
                event_id=index + 1,
                calendar_id=1,
                title="Weekly sync",
                series_key="series-a",
                occurrence_type="occurrence",
                **timed(date(2026, 12, 1) + timedelta(days=7 * index), 10),
            )
        add_event(
            session,
            event_id=10,
            calendar_id=1,
            title="Hold: offsite",
            status="tentative",
            is_organiser=True,
            location_text="Harbour 100% venue",
            **timed(date(2026, 12, 2), 9),
        )
        await session.commit()

    await _alice(session_maker)
    first = await list_calendar_events(limit=2, order="oldest_first")
    second = await list_calendar_events(limit=2, skip=2, order="oldest_first")
    holds = await list_calendar_events(status="tentative")
    by_location = await list_calendar_events(query="100%")
    series = await list_calendar_events(series_id=series_id(1, "series-a"))
    unknown_series = await list_calendar_events(series_id="1-" + "0" * 20)

    assert [e["id"] for e in first] == [1, 10]
    assert [e["id"] for e in second] == [2, 3]
    assert [e["id"] for e in holds] == [10]
    assert holds[0]["is_hold"] is True
    assert [e["id"] for e in by_location] == [10]
    assert sorted(e["id"] for e in series) == [1, 2, 3, 4, 5]
    assert series[0]["recurrence"]["series_id"] == series_id(1, "series-a")
    assert unknown_series == []


@pytest.mark.anyio
async def test_times_are_shown_in_the_first_zone_known(session_maker) -> None:
    async with session_maker() as session:
        add_account(session, connection_id=1)
        await session.flush()
        add_calendar(session, calendar_id=1, connection_id=1, name="No zone")
        add_calendar(
            session,
            calendar_id=2,
            connection_id=1,
            name="New York",
            time_zone="America/New_York",
        )
        await session.flush()
        add_event(
            session,
            event_id=1,
            calendar_id=2,
            start_time_zone="Asia/Tokyo",
            **timed(date(2026, 7, 1), 9),
        )
        add_event(session, event_id=2, calendar_id=2, **timed(date(2026, 7, 1), 10))
        add_event(session, event_id=3, calendar_id=1, **timed(date(2026, 7, 1), 11))
        add_event(
            session,
            event_id=4,
            calendar_id=1,
            start_date=date(2026, 12, 24),
            end_date=date(2026, 12, 26),
        )
        await session.commit()

    await _alice(session_maker)
    events = {e["id"]: e for e in await list_calendar_events()}

    assert events[1]["start"] == {
        "date_time": "2026-07-01T18:00:00+09:00",
        "time_zone": "Asia/Tokyo",
    }
    assert events[1]["time_zone_source"] == "event"
    assert events[2]["start"]["date_time"] == "2026-07-01T06:00:00-04:00"
    assert events[2]["time_zone_source"] == "calendar"
    assert events[3]["start"]["date_time"] == "2026-07-01T12:00:00+01:00"
    assert events[3]["time_zone_source"] == "user"
    assert events[4]["start"] == {"date": "2026-12-24"}
    assert events[4]["end"] == {"date": "2026-12-26"}
    assert events[4]["last_date"] == "2026-12-25"


@pytest.mark.anyio
async def test_a_bad_date_is_a_structured_error(session_maker) -> None:
    await _alice(session_maker)

    with pytest.raises(ToolError) as refused:
        await list_calendar_events(start_date="next tuesday")

    assert json.loads(str(refused.value)) == {
        "error": {
            "code": "invalid_time",
            "message": "start_date must be an ISO 8601 date-time such as "
            "2026-12-01T10:00:00+00:00, got 'next tuesday'.",
            "retryable": False,
            "field": "start_date",
            "details": {},
        }
    }
