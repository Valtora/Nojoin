"""list_calendars, get_calendar_event and get_free_busy.

Google and Microsoft calendars are listed as read-only for assistants, an
event reads in full with its linked recordings, a refresh reads the provider
and forgets an event it no longer has, and free/busy merges busy time, keeps
holds tentative and says when its answer may be incomplete.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker

from backend.mcp_server.tools_calendar import (
    get_calendar_event,
    get_free_busy,
    list_calendars,
)
from backend.models.calendar import CalendarEvent
from backend.models.recording import Recording, RecordingStatus
from backend.models.user import User
from backend.services.calendar_service import event_refresh
from backend.services.calendar_service import event_view as event_view_module
from backend.services.calendar_service.models_dto import (
    CalendarReauthorisationRequired,
    ProviderEventRecord,
)
from backend.services.calendar_service.tool_failures import calendar_tool_error
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

DAY = date(2026, 12, 1)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(params=["sqlite", "postgres"])
async def engine(request: pytest.FixtureRequest) -> AsyncIterator[AsyncEngine]:
    if request.param == "postgres":
        built = await postgres_engine(
            request.getfixturevalue("postgres_test_url"), "mcp_calendar_reads_test"
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


def _error(refused: pytest.ExceptionInfo) -> dict:
    return json.loads(str(refused.value))["error"]


async def _two_accounts(session_maker) -> None:
    async with session_maker() as session:
        add_account(session, connection_id=1, email="alice@example.com")
        add_account(
            session, connection_id=2, provider="microsoft", email="alice@work.example"
        )
        add_account(session, connection_id=3, user_id=BOB, email="bob@example.org")
        await session.flush()
        add_calendar(session, calendar_id=1, connection_id=1, name="Zebra")
        add_calendar(
            session,
            calendar_id=2,
            connection_id=1,
            name="Personal",
            primary=True,
            time_zone="Europe/London",
        )
        add_calendar(
            session, calendar_id=3, connection_id=1, name="Shared", selected=False
        )
        add_calendar(session, calendar_id=4, connection_id=2, name="Work", primary=True)
        add_calendar(session, calendar_id=5, connection_id=3, name="Bob's")
        await session.flush()
        add_event(session, event_id=1, calendar_id=2, **timed(DAY, 9))
        add_event(session, event_id=2, calendar_id=2, **timed(DAY, 11))
        await session.commit()


@pytest.mark.anyio
async def test_list_calendars_orders_by_account_and_says_why_not_writable(
    session_maker,
) -> None:
    await _two_accounts(session_maker)
    await _alice(session_maker)

    calendars = await list_calendars()
    everything = await list_calendars(include_unselected=True)

    assert [c["name"] for c in calendars] == ["Personal", "Zebra", "Work"]
    assert [c["name"] for c in everything] == ["Personal", "Shared", "Zebra", "Work"]
    personal = calendars[0]
    assert personal == {
        "id": 2,
        "name": "Personal",
        "provider": "google",
        "account": "alice@example.com",
        "connection_id": 1,
        "is_primary": True,
        "selected": True,
        "writable": False,
        "write_unavailable_reason": "provider_read_only",
        "time_zone": "Europe/London",
        "colour": None,
        "sync_status": "success",
        "sync_error": None,
        "last_synced_at": "2026-11-28T09:55:00+00:00",
        "stale": False,
        "synced_range": {"from": "2025-11-01", "to": "2027-12-01"},
        "event_count": 2,
        "capabilities": {
            "create": False,
            "update": False,
            "cancel": False,
            "respond": False,
            "holds": None,
            "invitations": None,
            "occurrence_edits": False,
        },
    }
    assert calendars[2]["write_unavailable_reason"] == "provider_read_only"
    assert everything[1]["stale"] is True


@pytest.mark.anyio
async def test_get_calendar_event_reads_in_full(session_maker) -> None:
    attendees = [
        {
            "name": f"Guest {index} Example",
            "email": f"guest{index}@example.org",
            "response": "needs_action",
            "role": "required",
            "is_self": False,
        }
        for index in range(12)
    ]
    async with session_maker() as session:
        add_account(session, connection_id=1)
        await session.flush()
        add_calendar(session, calendar_id=1, connection_id=1)
        await session.flush()
        add_event(
            session,
            event_id=1,
            calendar_id=1,
            description="Agenda " * 100,
            attendees=attendees,
            organiser={
                "name": "Bob Example",
                "email": "bob@example.org",
                "is_self": False,
            },
            is_organiser=False,
            my_response="accepted",
            source_url="https://calendar.example.com/e/1",
            **timed(DAY, 9),
        )
        add_event(session, event_id=2, calendar_id=1, **timed(DAY, 12))
        await session.flush()
        for recording_id, (name, deleted) in enumerate(
            [("Planning", False), ("Binned", True)], start=1
        ):
            session.add(
                Recording(
                    id=recording_id,
                    public_id=f"rec-{recording_id}",
                    meeting_uid=f"meeting-{recording_id}",
                    name=name,
                    audio_path=f"/nonexistent/{recording_id}.webm",
                    status=RecordingStatus.PROCESSED,
                    user_id=ALICE,
                    calendar_event_id=1,
                    is_deleted=deleted,
                )
            )
        await session.commit()

    await _alice(session_maker)
    event = await get_calendar_event(1)

    assert event["description"] == "Agenda " * 100
    assert event["description_truncated"] is False
    assert event["attendee_count"] == 12
    assert len(event["attendees"]) == 12
    assert event["my_response"] == "accepted"
    assert event["can_respond"] is False
    assert event["source_url"] == "https://calendar.example.com/e/1"
    assert event["linked_recordings"] == [{"id": "rec-1", "name": "Planning"}]

    other = await get_calendar_event(2)
    assert other["linked_recordings"] == []

    with pytest.raises(ToolError) as refused:
        await get_calendar_event(999)
    assert _error(refused)["code"] == "not_found"


def _record(**overrides) -> ProviderEventRecord:
    values = {
        "remote_id": "evt-1",
        "title": "Design review, moved",
        "status": "confirmed",
        "is_all_day": False,
        "starts_at": datetime(2026, 12, 1, 14, 0),
        "ends_at": datetime(2026, 12, 1, 15, 0),
        "start_date": None,
        "end_date": None,
        "provider_revision": "etag-2",
    }
    values.update(overrides)
    return ProviderEventRecord(**values)


@pytest.mark.anyio
async def test_refresh_reads_the_provider_and_forgets_a_deleted_event(
    session_maker, monkeypatch
) -> None:
    async with session_maker() as session:
        add_account(session, connection_id=1)
        await session.flush()
        add_calendar(session, calendar_id=1, connection_id=1)
        await session.flush()
        add_event(session, event_id=1, calendar_id=1, **timed(DAY, 9))
        await session.commit()
    answers: list[ProviderEventRecord | None] = [_record(), None]

    async def fake_fetch(db, connection, source, provider_event_id):
        assert provider_event_id == "evt-1"
        return answers.pop(0)

    monkeypatch.setattr(event_refresh, "fetch_oauth_event", fake_fetch)
    await _alice(session_maker)

    fresh = await get_calendar_event(1, refresh=True)
    assert fresh["title"] == "Design review, moved"
    assert fresh["revision"] == "etag-2"
    assert fresh["starts_at"] == "2026-12-01T14:00:00"

    with pytest.raises(ToolError) as gone:
        await get_calendar_event(1, refresh=True)
    assert _error(gone)["code"] == "not_found"
    async with session_maker() as session:
        assert await session.get(CalendarEvent, 1) is None


def _status_error(status: int, headers: dict | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://calendar.example.com/events/1")
    response = httpx.Response(status, request=request, headers=headers or {}, json={})
    return httpx.HTTPStatusError("failed", request=request, response=response)


@pytest.mark.parametrize(
    ("exc", "code", "retryable"),
    [
        (_status_error(404), "not_found", False),
        (_status_error(410), "not_found", False),
        (_status_error(412), "conflict", False),
        (_status_error(429, {"Retry-After": "30"}), "rate_limited", True),
        (_status_error(503), "provider_unavailable", True),
        (_status_error(401), "auth_expired", False),
        (_status_error(400), "provider_rejected", False),
        (httpx.ConnectTimeout("slow"), "provider_unavailable", True),
        (CalendarReauthorisationRequired("refused"), "auth_expired", False),
    ],
)
def test_provider_failures_map_to_codes(exc, code, retryable) -> None:
    error = calendar_tool_error(exc)

    assert error is not None
    assert (error.code, error.retryable) == (code, retryable)
    if code == "rate_limited":
        assert error.details == {"retry_after_seconds": 30}


def test_other_failures_are_not_refusals() -> None:
    assert calendar_tool_error(RuntimeError("bug")) is None


async def _diary(session_maker) -> None:
    async with session_maker() as session:
        add_account(session, connection_id=1)
        await session.flush()
        add_calendar(session, calendar_id=1, connection_id=1)
        add_calendar(session, calendar_id=2, connection_id=1, name="Work")
        await session.flush()
        # 09:00-10:00 and 09:30-10:30 merge; the hold at 14:00 stays tentative.
        add_event(session, event_id=1, calendar_id=1, **timed(DAY, 9))
        add_event(session, event_id=2, calendar_id=2, **timed(DAY, 9, 60), title="x")
        session.add(
            CalendarEvent(
                id=3,
                calendar_id=2,
                provider_event_id="evt-3",
                title="Overlap",
                starts_at=datetime(2026, 12, 1, 9, 30),
                ends_at=datetime(2026, 12, 1, 10, 30),
            )
        )
        add_event(
            session,
            event_id=4,
            calendar_id=1,
            title="Hold: workshop",
            status="tentative",
            is_organiser=True,
            **timed(DAY, 14),
        )
        add_event(
            session,
            event_id=5,
            calendar_id=1,
            title="Declined",
            is_organiser=False,
            my_response="declined",
            **timed(DAY, 16),
        )
        add_event(session, event_id=6, calendar_id=1, show_as="free", **timed(DAY, 12))
        add_event(
            session,
            event_id=7,
            calendar_id=1,
            title="Holiday",
            start_date=DAY,
            end_date=DAY + timedelta(days=1),
        )
        await session.commit()


@pytest.mark.anyio
async def test_free_busy_merges_and_keeps_holds_tentative(session_maker) -> None:
    await _diary(session_maker)
    await _alice(session_maker)

    answer = await get_free_busy(
        "2026-12-01T09:00:00Z", "2026-12-01T18:00:00Z", time_zone="UTC"
    )

    assert answer["complete"] is True
    assert answer["window"] == {
        "start": "2026-12-01T09:00:00+00:00",
        "end": "2026-12-01T18:00:00+00:00",
        "time_zone": "UTC",
    }
    assert answer["busy"] == [
        {
            "start": "2026-12-01T09:00:00+00:00",
            "end": "2026-12-01T10:30:00+00:00",
            "tentative": False,
            "calendar_ids": [1, 2],
            "event_ids": [1, 2, 3],
        },
        {
            "start": "2026-12-01T14:00:00+00:00",
            "end": "2026-12-01T15:00:00+00:00",
            "tentative": True,
            "calendar_ids": [1],
            "event_ids": [4],
        },
    ]
    assert [gap["minutes"] for gap in answer["free"]] == [210, 180]

    no_holds = await get_free_busy(
        "2026-12-01T09:00:00Z", "2026-12-01T18:00:00Z", include_holds=False
    )
    assert [block["event_ids"] for block in no_holds["busy"]] == [[1, 2, 3]]

    with_all_day = await get_free_busy(
        "2026-12-01T09:00:00Z", "2026-12-01T18:00:00Z", include_all_day=True
    )
    assert len(with_all_day["busy"]) == 1
    assert with_all_day["free"] == []

    long_gaps = await get_free_busy(
        "2026-12-01T09:00:00Z", "2026-12-01T18:00:00Z", min_free_minutes=200
    )
    assert [gap["minutes"] for gap in long_gaps["free"]] == [210]


@pytest.mark.anyio
async def test_free_busy_writes_the_answer_in_the_users_zone_by_default(
    session_maker,
) -> None:
    await _diary(session_maker)
    await _alice(session_maker)

    # A bare date is the whole day in the answer's zone.
    answer = await get_free_busy("2026-12-01", "2026-12-01")

    assert answer["window"] == {
        "start": "2026-12-01T00:00:00+00:00",
        "end": "2026-12-02T00:00:00+00:00",
        "time_zone": "Europe/London",
    }


@pytest.mark.anyio
async def test_free_busy_says_when_it_may_be_incomplete(session_maker) -> None:
    async with session_maker() as session:
        add_account(session, connection_id=1)
        await session.flush()
        add_calendar(session, calendar_id=1, connection_id=1)
        add_calendar(
            session,
            calendar_id=2,
            connection_id=1,
            name="Old",
            last_synced_at=NOW - timedelta(hours=3),
        )
        await session.commit()
    await _alice(session_maker)

    fresh_only = await get_free_busy(
        "2026-12-01T09:00:00Z", "2026-12-01T18:00:00Z", calendar_ids=[1]
    )
    with_stale = await get_free_busy("2026-12-01T09:00:00Z", "2026-12-01T18:00:00Z")
    past_range = await get_free_busy(
        "2027-11-25T09:00:00Z", "2027-12-05T18:00:00Z", calendar_ids=[1]
    )

    assert fresh_only["complete"] is True
    assert with_stale["complete"] is False
    assert past_range["complete"] is False


@pytest.mark.anyio
async def test_free_busy_refuses_long_windows_and_naive_times(session_maker) -> None:
    await _alice(session_maker)

    with pytest.raises(ToolError) as too_long:
        await get_free_busy("2026-12-01T00:00:00Z", "2027-02-02T00:00:00Z")
    with pytest.raises(ToolError) as naive:
        await get_free_busy("2026-12-01T09:00:00", "2026-12-01T18:00:00")
    with pytest.raises(ToolError) as backwards:
        await get_free_busy("2026-12-01T18:00:00Z", "2026-12-01T09:00:00Z")

    assert (_error(too_long)["code"], _error(too_long)["field"]) == (
        "invalid_argument",
        "end",
    )
    assert _error(naive)["code"] == "invalid_time"
    assert "Europe/London" in _error(naive)["message"]
    assert _error(backwards)["code"] == "invalid_time"
