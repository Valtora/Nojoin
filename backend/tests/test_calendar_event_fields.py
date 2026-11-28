"""Event fields beyond title and times: zones, ranges, people and series.

Covers the Google and Microsoft normalisation of organiser, attendee
responses, holds, busy or free, series identity and revisions; the Windows
zone map; and the range every stored event carries for ordering and overlap.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import select

from backend.models.calendar import (
    CalendarConnection,
    CalendarEvent,
    CalendarSource,
    fill_event_range,
)
from backend.models.user import User
from backend.services.calendar_service import (
    _normalise_google_event,
    _normalise_microsoft_event,
)
from backend.services.calendar_service.models_dto import ProviderEventRecord
from backend.services.calendar_service.persistence import (
    _apply_provider_event_to_model,
    _reconcile_calendar_events,
)
from backend.services.calendar_service.provider_fields import mark_own_attendee
from backend.services.calendar_service.zones import (
    _WINDOWS_ZONES_PATH,
    event_range,
    iana_zone_name,
)
from backend.tests.sqlite_schemas import (
    CALENDAR_CONNECTIONS_SCHEMA,
    CALENDAR_EVENTS_SCHEMA,
    CALENDAR_SOURCES_SCHEMA,
    USERS_SCHEMA,
)

# --- Google -----------------------------------------------------------------


def _google_invitation(**overrides):
    item = {
        "id": "g-1_20261201T100000Z",
        "etag": '"3181159875584000"',
        "summary": "Design review",
        "status": "confirmed",
        "start": {"dateTime": "2026-12-01T10:00:00Z", "timeZone": "Europe/London"},
        "end": {"dateTime": "2026-12-01T11:00:00Z", "timeZone": "Europe/London"},
        "organizer": {"email": "bob@example.org", "displayName": "Bob Example"},
        "attendees": [
            {
                "email": "bob@example.org",
                "displayName": "Bob Example",
                "organizer": True,
                "responseStatus": "accepted",
            },
            {
                "email": "alice@example.com",
                "displayName": "Alice Example",
                "self": True,
                "responseStatus": "tentative",
            },
            {
                "email": "carol@example.com",
                "optional": True,
                "responseStatus": "needsAction",
            },
        ],
    }
    item.update(overrides)
    return item


def test_google_invitation_keeps_the_organiser_and_every_response() -> None:
    event = _normalise_google_event(_google_invitation())

    assert event is not None
    assert event.organiser == {
        "name": "Bob Example",
        "email": "bob@example.org",
        "is_self": False,
    }
    assert event.is_organiser is False
    assert event.my_response == "tentative"
    assert [
        (a["email"], a["response"], a["role"], a["is_self"]) for a in event.attendees
    ] == [
        ("bob@example.org", "accepted", "required", False),
        ("alice@example.com", "tentative", "required", True),
        ("carol@example.com", "needs_action", "optional", False),
    ]
    assert event.start_time_zone == "Europe/London"
    assert event.end_time_zone == "Europe/London"
    assert event.provider_revision == '"3181159875584000"'
    assert event.show_as == "busy"
    assert event.status == "confirmed"
    assert event.series_key is None
    assert event.occurrence_type == "single"


def test_google_own_event_has_no_answer_of_its_own() -> None:
    event = _normalise_google_event(
        _google_invitation(
            organizer={"email": "alice@example.com", "self": True},
            status="tentative",
            transparency="transparent",
        )
    )

    assert event is not None
    assert event.is_organiser is True
    assert event.my_response is None
    assert event.status == "tentative"
    assert event.show_as == "free"


def test_google_occurrences_name_their_series_and_spot_a_moved_one() -> None:
    on_slot = _normalise_google_event(
        _google_invitation(
            recurringEventId="g-1",
            originalStartTime={
                "dateTime": "2026-12-01T10:00:00Z",
                "timeZone": "Europe/London",
            },
        )
    )
    moved = _normalise_google_event(
        _google_invitation(
            recurringEventId="g-1",
            originalStartTime={"dateTime": "2026-12-01T09:00:00Z"},
        )
    )

    assert on_slot is not None and moved is not None
    assert on_slot.series_key == "g-1"
    assert on_slot.original_start == datetime(2026, 12, 1, 10, 0)
    assert on_slot.occurrence_type == "occurrence"
    assert moved.occurrence_type == "exception"
    assert moved.original_start == datetime(2026, 12, 1, 9, 0)


def test_google_all_day_occurrence_keeps_its_original_date() -> None:
    event = _normalise_google_event(
        {
            "id": "g-2_20261225",
            "summary": "Bank holiday",
            "start": {"date": "2026-12-25"},
            "end": {"date": "2026-12-26"},
            "recurringEventId": "g-2",
            "originalStartTime": {"date": "2026-12-25"},
        }
    )

    assert event is not None
    assert event.original_start_date == date(2026, 12, 25)
    assert event.original_start is None
    assert event.occurrence_type == "occurrence"


# --- Microsoft --------------------------------------------------------------


def _microsoft_event(**overrides):
    item = {
        "id": "m-1",
        "changeKey": "DwAAABYAAAA",
        "subject": "Design review",
        "isCancelled": False,
        "isAllDay": False,
        "type": "singleInstance",
        "start": {"dateTime": "2026-07-01T09:00:00.0000000"},
        "end": {"dateTime": "2026-07-01T10:00:00.0000000"},
        "originalStartTimeZone": "GMT Standard Time",
        "originalEndTimeZone": "GMT Standard Time",
        "showAs": "busy",
        "isOrganizer": False,
        "organizer": {
            "emailAddress": {"name": "Bob Example", "address": "bob@example.org"}
        },
        "responseStatus": {"response": "tentativelyAccepted"},
        "attendees": [
            {
                "type": "required",
                "status": {"response": "accepted"},
                "emailAddress": {"name": "Bob Example", "address": "bob@example.org"},
            },
            {
                "type": "optional",
                "status": {"response": "tentativelyAccepted"},
                "emailAddress": {
                    "name": "Alice Example",
                    "address": "Alice@Example.com",
                },
            },
            {
                "type": "resource",
                "status": {"response": "accepted"},
                "emailAddress": {"name": "Room 1", "address": "room@example.org"},
            },
        ],
    }
    item.update(overrides)
    return item


def test_microsoft_invitation_keeps_the_organiser_and_every_response() -> None:
    event = _normalise_microsoft_event(_microsoft_event())

    assert event is not None
    assert event.organiser == {
        "name": "Bob Example",
        "email": "bob@example.org",
        "is_self": False,
    }
    assert event.is_organiser is False
    assert event.my_response == "tentative"
    # A tentative answer to someone else's meeting is not the user's hold.
    assert event.status == "confirmed"
    assert [(a["email"], a["response"], a["role"]) for a in event.attendees] == [
        ("bob@example.org", "accepted", "required"),
        ("Alice@Example.com", "tentative", "optional"),
    ]
    assert event.start_time_zone == "Europe/London"
    assert event.provider_revision == "DwAAABYAAAA"
    assert event.occurrence_type == "single"


def test_microsoft_marks_the_users_own_entry_by_address() -> None:
    event = _normalise_microsoft_event(_microsoft_event())
    assert event is not None

    mark_own_attendee([event], "alice@example.com")

    assert [a["is_self"] for a in event.attendees] == [False, True]


def test_microsoft_own_tentative_event_is_a_hold() -> None:
    event = _normalise_microsoft_event(
        _microsoft_event(
            showAs="tentative",
            isOrganizer=True,
            responseStatus={"response": "organizer"},
        )
    )

    assert event is not None
    assert event.status == "tentative"
    assert event.is_organiser is True
    assert event.my_response is None
    assert event.show_as == "busy"


def test_microsoft_free_event_and_series_fields() -> None:
    event = _normalise_microsoft_event(
        _microsoft_event(
            showAs="free",
            type="exception",
            seriesMasterId="m-master",
            originalStart="2026-07-01T08:00:00Z",
        )
    )

    assert event is not None
    assert event.show_as == "free"
    assert event.series_key == "m-master"
    assert event.occurrence_type == "exception"
    assert event.original_start == datetime(2026, 7, 1, 8, 0)


def test_microsoft_all_day_occurrence_takes_its_date_in_the_event_zone() -> None:
    # Midnight on 1 July in London is 23:00 UTC on 30 June.
    event = _normalise_microsoft_event(
        _microsoft_event(
            isAllDay=True,
            type="occurrence",
            seriesMasterId="m-master",
            start={"dateTime": "2026-07-01T00:00:00.0000000"},
            end={"dateTime": "2026-07-02T00:00:00.0000000"},
            originalStart="2026-06-30T23:00:00Z",
        )
    )

    assert event is not None
    assert event.original_start_date == date(2026, 7, 1)
    assert event.original_start is None


# --- Zones ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("GMT Standard Time", "Europe/London"),
        ("W. Europe Standard Time", "Europe/Berlin"),
        ("Eastern Standard Time", "America/New_York"),
        ("Europe/Paris", "Europe/Paris"),
        ("tzone://Microsoft/Utc", "UTC"),
        ("UTC", "UTC"),
        ("Customized Time Zone", None),
        ("", None),
        (None, None),
    ],
)
def test_iana_zone_name(value, expected) -> None:
    assert iana_zone_name(value) == expected


def test_every_vendored_windows_zone_resolves() -> None:
    zones = json.loads(_WINDOWS_ZONES_PATH.read_text(encoding="utf-8"))["zones"]

    assert len(zones) > 100
    unresolved = [name for name in zones if iana_zone_name(name) is None]
    assert unresolved == []
    # Canonical names only: a zone database without tzdata's backward links
    # (Ubuntu's tzdata without tzdata-legacy) has no CLDR legacy names.
    legacy = {"America/Buenos_Aires", "Asia/Calcutta", "Europe/Kiev", "America/Godthab"}
    assert legacy.isdisjoint(zones.values())
    assert zones["India Standard Time"] == "Asia/Kolkata"


# --- Ranges -----------------------------------------------------------------


def _record(**overrides) -> ProviderEventRecord:
    values = {
        "remote_id": "evt-1",
        "title": "Design review",
        "status": "confirmed",
        "is_all_day": False,
        "starts_at": datetime(2026, 7, 1, 9, 0),
        "ends_at": datetime(2026, 7, 1, 10, 0),
        "start_date": None,
        "end_date": None,
    }
    values.update(overrides)
    return ProviderEventRecord(**values)


def test_timed_event_range_is_its_times() -> None:
    assert event_range(_record(), "Europe/London") == (
        datetime(2026, 7, 1, 9, 0),
        datetime(2026, 7, 1, 10, 0),
    )


def test_all_day_range_runs_midnight_to_midnight_in_the_zone() -> None:
    holiday = _record(
        is_all_day=True,
        starts_at=None,
        ends_at=None,
        start_date=date(2026, 8, 31),
        end_date=date(2026, 9, 1),
    )

    # British Summer Time: midnight in London is 23:00 UTC the day before.
    assert event_range(holiday, "Europe/London") == (
        datetime(2026, 8, 30, 23, 0),
        datetime(2026, 8, 31, 23, 0),
    )
    assert event_range(holiday, None) == (
        datetime(2026, 8, 31, 0, 0),
        datetime(2026, 9, 1, 0, 0),
    )


def test_fill_event_range_keeps_a_zone_midnight_and_fixes_a_stale_one() -> None:
    event = CalendarEvent(
        calendar_id=1,
        provider_event_id="evt-1",
        title="Holiday",
        is_all_day=True,
        start_date=date(2026, 8, 31),
        end_date=date(2026, 9, 1),
        range_start=datetime(2026, 8, 30, 23, 0),
        range_end=datetime(2026, 8, 31, 23, 0),
    )
    fill_event_range(event)
    assert (event.range_start, event.range_end) == (
        datetime(2026, 8, 30, 23, 0),
        datetime(2026, 8, 31, 23, 0),
    )

    # The event moved a week; the old range no longer belongs to its dates.
    event.start_date = date(2026, 9, 7)
    event.end_date = date(2026, 9, 8)
    fill_event_range(event)
    assert (event.range_start, event.range_end) == (
        datetime(2026, 9, 7, 0, 0),
        datetime(2026, 9, 8, 0, 0),
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def session_maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        for schema in (
            USERS_SCHEMA,
            CALENDAR_CONNECTIONS_SCHEMA,
            CALENDAR_SOURCES_SCHEMA,
            CALENDAR_EVENTS_SCHEMA,
        ):
            await conn.execute(text(schema))
    yield sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def _seed_calendar(session: AsyncSession) -> int:
    session.add(User(id=1, username="alice", hashed_password="x", role="user"))
    session.add(
        CalendarConnection(
            id=1, user_id=1, provider="google", provider_account_id="acct-1"
        )
    )
    session.add(
        CalendarSource(
            id=1, connection_id=1, provider_calendar_id="cal-1", name="Personal"
        )
    )
    await session.commit()
    return 1


@pytest.mark.anyio
async def test_an_event_written_without_a_range_gets_one(session_maker) -> None:
    async with session_maker() as session:
        calendar_id = await _seed_calendar(session)
        session.add(
            CalendarEvent(
                calendar_id=calendar_id,
                provider_event_id="evt-timed",
                title="Planning",
                starts_at=datetime(2026, 12, 1, 10, 0),
                ends_at=datetime(2026, 12, 1, 11, 0),
            )
        )
        session.add(
            CalendarEvent(
                calendar_id=calendar_id,
                provider_event_id="evt-all-day",
                title="Holiday",
                is_all_day=True,
                start_date=date(2026, 12, 25),
            )
        )
        await session.commit()

        rows = {
            row.provider_event_id: (row.range_start, row.range_end)
            for row in (await session.execute(select(CalendarEvent))).scalars()
        }

    assert rows == {
        "evt-timed": (datetime(2026, 12, 1, 10, 0), datetime(2026, 12, 1, 11, 0)),
        "evt-all-day": (datetime(2026, 12, 25), datetime(2026, 12, 26)),
    }


@pytest.mark.anyio
async def test_sync_stores_all_day_ranges_in_the_calendar_zone(session_maker) -> None:
    holiday = _record(
        remote_id="evt-holiday",
        is_all_day=True,
        starts_at=None,
        ends_at=None,
        start_date=date(2026, 8, 31),
        end_date=date(2026, 9, 1),
        series_key="series-1",
        occurrence_type="occurrence",
        show_as="free",
    )
    async with session_maker() as session:
        calendar_id = await _seed_calendar(session)
        await _reconcile_calendar_events(
            session,
            calendar_id,
            [holiday],
            datetime(2025, 8, 1),
            range_zone="Europe/London",
        )
        await session.commit()
        stored = (await session.execute(select(CalendarEvent))).scalar_one()

    assert stored.range_start == datetime(2026, 8, 30, 23, 0)
    assert stored.range_end == datetime(2026, 8, 31, 23, 0)
    assert stored.series_key == "series-1"
    assert stored.occurrence_type == "occurrence"
    assert stored.show_as == "free"


def test_apply_copies_every_new_field() -> None:
    record = _record(
        start_time_zone="Europe/London",
        end_time_zone="Europe/London",
        organiser={"name": "Bob Example", "email": "bob@example.org", "is_self": False},
        is_organiser=False,
        my_response="accepted",
        recurrence_rule="FREQ=WEEKLY;BYDAY=TU",
        provider_revision="rev-1",
        provider_data={"master": "abc"},
        original_start=datetime(2026, 7, 1, 9, 0),
    )
    event = _apply_provider_event_to_model(
        CalendarEvent(calendar_id=1, provider_event_id="x", title="x"), record
    )

    assert event.provider_event_id == "evt-1"
    assert event.start_time_zone == "Europe/London"
    assert event.organiser == record.organiser
    assert event.my_response == "accepted"
    assert event.recurrence_rule == "FREQ=WEEKLY;BYDAY=TU"
    assert event.provider_revision == "rev-1"
    assert event.provider_data == {"master": "abc"}
    assert event.original_start == datetime(2026, 7, 1, 9, 0)
    assert (event.range_start, event.range_end) == (record.starts_at, record.ends_at)
    # The zone the stored times are read back in round-trips.
    assert (
        event.starts_at.replace(tzinfo=ZoneInfo("UTC"))
        .astimezone(ZoneInfo("Europe/London"))
        .hour
        == 10
    )
