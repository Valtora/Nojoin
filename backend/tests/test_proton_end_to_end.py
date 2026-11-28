"""Proton end to end: the real helper against the fake Proton server.

Design section 12.3 without a stack: the nojoin-proton binary runs as Nojoin
runs it, against nojoin-proton-fake (proton/cmd/nojoin-proton-fake), an
in-memory Proton with made-up accounts that does real SRP, key handling and
card checks, on loopback. Connect, sync, create a hold with an attendee,
confirm it with notify on, answer the seeded invitation, and cancel.

Skips unless NOJOIN_TEST_PROTON_HELPER and NOJOIN_TEST_PROTON_FAKE name the
two binaries (``go build ./cmd/...`` in proton/); docs/DEVELOPMENT.md has the
commands.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlmodel import select

from backend.mcp_server.tools_calendar import (
    AttendeeInput,
    cancel_calendar_event,
    create_calendar_event,
    list_calendar_events,
    list_calendars,
    respond_to_calendar_invitation,
    update_calendar_event,
)
from backend.models.calendar import CalendarEvent
from backend.models.user import User
from backend.services.calendar_service import proton_connect
from backend.tests.calendar_tool_support import ALICE, bind_identity, sqlite_engine
from backend.tests.sqlite_schemas import (
    CALENDAR_ATTENDEE_INVITES_SCHEMA,
    MCP_WRITE_AUDIT_SCHEMA,
)

HELPER = os.getenv("NOJOIN_TEST_PROTON_HELPER")
FAKE = os.getenv("NOJOIN_TEST_PROTON_FAKE")
pytestmark = pytest.mark.skipif(
    not (HELPER and FAKE),
    reason="needs the nojoin-proton and nojoin-proton-fake binaries",
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def fake_proton(monkeypatch) -> Iterator[dict]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen(
        [str(FAKE), "-listen", f"127.0.0.1:{port}"],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        account = json.loads(process.stdout.readline())
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
                break
            except OSError:
                continue
        monkeypatch.setenv("NOJOIN_PROTON_HELPER", str(HELPER))
        monkeypatch.setenv("NOJOIN_PROTON_API_BASE_URL", account["base_url"])
        yield account
    finally:
        process.kill()
        process.wait()


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    built = await sqlite_engine(
        (CALENDAR_ATTENDEE_INVITES_SCHEMA, MCP_WRITE_AUDIT_SCHEMA)
    )
    yield built
    await built.dispose()


@pytest.fixture
def session_maker(engine: AsyncEngine, monkeypatch) -> sessionmaker:
    import backend.core.db as core_db

    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(core_db, "async_session_maker", maker)
    store: dict = {}

    async def save(state, payload):
        store[state] = payload

    async def pop(state):
        return store.pop(state, None)

    monkeypatch.setattr(proton_connect, "_save_oauth_state", save)
    monkeypatch.setattr(proton_connect, "_pop_oauth_state", pop)
    return maker


@pytest.mark.anyio
async def test_connect_sync_write_answer_and_cancel(session_maker, fake_proton) -> None:
    async with session_maker() as session:
        alice = await session.get(User, ALICE)
        step = await proton_connect.start(
            session,
            alice,
            username=fake_proton["username"],
            password=fake_proton["password"],
        )
    assert step.step == "done"
    assert step.connection is not None and step.connection.sync_status == "success"
    bind_identity(alice)

    (calendar,) = [c for c in await list_calendars() if c["provider"] == "proton"]
    assert calendar["writable"] is True
    assert calendar["capabilities"]["holds"] == "marker"

    hold = await create_calendar_event(
        calendar_id=calendar["id"],
        title="Design review",
        start="2026-12-01T14:00:00Z",
        end="2026-12-01T15:00:00Z",
        time_zone="Europe/London",
        status="tentative",
        attendees=[
            AttendeeInput(email=fake_proton["proton_guest"], name="Bob Example")
        ],
        idempotency_key="e2e-hold-0001",
    )
    assert hold["verified"] is True
    assert hold["event"]["is_hold"] is True
    assert hold["event"]["title"] == "Hold: Design review"
    assert hold["notifications"]["emailed"] == []

    confirmed = await update_calendar_event(
        hold["event"]["id"], status="confirmed", notify_attendees=True
    )
    assert confirmed["event"]["is_hold"] is False
    assert confirmed["event"]["title"] == "Design review"
    assert [m["kind"] for m in confirmed["notifications"]["emailed"]] == ["invitation"]

    invitations = [
        event for event in await list_calendar_events(limit=100) if event["can_respond"]
    ]
    assert invitations, "the fake seeds an invitation from another Proton user"
    answered = await respond_to_calendar_invitation(invitations[0]["id"], "accepted")
    assert answered["event"]["my_response"] == "accepted"
    assert [m["kind"] for m in answered["notifications"]["emailed"]] == ["reply"]

    cancelled = await cancel_calendar_event(hold["event"]["id"], notify_attendees=True)
    assert cancelled["outcome"] == "cancelled"
    assert [m["kind"] for m in cancelled["notifications"]["emailed"]] == [
        "cancellation"
    ]
    async with session_maker() as session:
        assert await session.get(CalendarEvent, hold["event"]["id"]) is None
        left = list((await session.execute(select(CalendarEvent))).scalars())
    assert [row.title for row in left] == [invitations[0]["title"]]
