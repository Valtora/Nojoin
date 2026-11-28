"""The four calendar write tools against the read-only backend and a test one.

A fake backend registered for "proton" stands in for a writable provider and
records every call, so these tests can assert what the neutral layer sends,
what it never sends (mail when nobody asked, a write on a dry run), the change
log it keeps, idempotency, locking and every structured error code. Names are
made up.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlmodel import select

from backend.core.encryption import decrypt_secret
from backend.core.security import MCP_READ_SCOPE
from backend.mcp_server.tools_calendar import (
    AttendeeInput,
    cancel_calendar_event,
    create_calendar_event,
    respond_to_calendar_invitation,
    update_calendar_event,
)
from backend.models.calendar import (
    CalendarAttendeeInvite,
    CalendarConnection,
    CalendarEvent,
)
from backend.models.mcp_write_audit import McpWriteAudit
from backend.models.user import User
from backend.services.calendar_service import event_view as event_view_module
from backend.services.calendar_service import write_steps, writes
from backend.services.calendar_service.capabilities import WriteCapabilities
from backend.services.calendar_service.models_dto import ProviderEventRecord
from backend.services.calendar_service.persistence import _reconcile_calendar_events
from backend.services.calendar_service.tool_errors import ERROR_CODES, CalendarToolError
from backend.services.calendar_service.write_audit import (
    ConnectionTokenStore,
    advisory_lock,
    connection_lock_key,
    prune_audit,
)
from backend.services.calendar_service.write_backend import (
    EventDraft,
    EventPatch,
    FieldProblem,
    NotificationReport,
    WriteOutcome,
    register_write_backend,
    unregister_write_backend,
)
from backend.services.calendar_service.write_inputs import draft_record, event_record
from backend.tests.calendar_tool_support import (
    ALICE,
    NOW,
    add_account,
    add_calendar,
    add_event,
    bind_identity,
    postgres_engine,
    sqlite_engine,
    timed,
)
from backend.tests.sqlite_schemas import (
    CALENDAR_ATTENDEE_INVITES_SCHEMA,
    MCP_WRITE_AUDIT_SCHEMA,
)

BOB = AttendeeInput(email="bob@example.org", name="Bob Example")
CAROL = AttendeeInput(email="carol@example.com", name="Carol Example", optional=True)
PROTON_CALENDAR = 1
GOOGLE_CALENDAR = 2


@dataclass
class FakeBackend:
    """A writable provider that records every call it gets."""

    provider: str = "proton"
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    mails: list[Any] = field(default_factory=list)
    landed: dict[str, ProviderEventRecord] = field(default_factory=dict)
    fail_with: CalendarToolError | None = None
    capabilities_value: WriteCapabilities = field(
        default_factory=lambda: WriteCapabilities(
            writable=True,
            create=True,
            update=True,
            cancel=True,
            respond=True,
            holds="marker",
            invitations="email",
            occurrence_edits=True,
        )
    )

    def capabilities(self, connection, source) -> WriteCapabilities:
        return self.capabilities_value

    def validate(self, source, draft: EventDraft | EventPatch) -> list[FieldProblem]:
        description = (
            draft.description
            if isinstance(draft, EventDraft)
            else draft.changes.get("description")
        )
        if description and len(description) > 3000:
            return [
                FieldProblem("description", "Proton keeps at most 3,000 characters.")
            ]
        return []

    def _fail(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    async def get_event(self, ctx, ref) -> ProviderEventRecord | None:
        self.calls.append(("get_event", {"uid": ref.uid}))
        return self.landed.get(ref.uid or "")

    async def create_event(
        self, ctx, draft, *, uid, share_with_attendees
    ) -> WriteOutcome:
        self.calls.append(
            (
                "create_event",
                {"draft": draft, "uid": uid, "share": share_with_attendees},
            )
        )
        self._fail()
        record = draft_record(draft, uid, uid=uid)
        record.provider_revision = "p1:1:0"
        self.landed[uid] = record
        return WriteOutcome(events=[record], revision="p1:1:0")

    async def _stored(self, ctx, ref) -> ProviderEventRecord:
        row = await ctx.db.get(CalendarEvent, ref.event_id)
        return event_record(row)

    async def update_event(  # noqa: PLR0913 - the backend interface
        self, ctx, ref, patch, *, scope, share_with_attendees, expected_revision
    ) -> WriteOutcome:
        self.calls.append(
            (
                "update_event",
                {"patch": patch, "scope": scope, "share": share_with_attendees},
            )
        )
        self._fail()
        record = await self._stored(ctx, ref)
        for name, attribute in (("title", "title"), ("status", "status")):
            if name in patch.changes:
                setattr(record, attribute, patch.changes[name])
        record.provider_revision = "p1:2:1"
        return WriteOutcome(events=[record], revision="p1:2:1")

    async def cancel_event(self, ctx, ref, *, scope, expected_revision) -> WriteOutcome:
        self.calls.append(("cancel_event", {"scope": scope}))
        self._fail()
        return WriteOutcome(events=[], deleted_keys=[ref.provider_event_id])

    async def respond(self, ctx, ref, response, *, expected_revision) -> WriteOutcome:
        self.calls.append(("respond", {"response": response}))
        self._fail()
        record = await self._stored(ctx, ref)
        record.my_response = response
        return WriteOutcome(events=[record], revision="p1:3:1")

    async def send_notifications(self, ctx, plan) -> NotificationReport:
        self.calls.append(
            ("send_notifications", {"kinds": [m.kind for m in plan.mails]})
        )
        self.mails.append(plan)
        return NotificationReport(emailed=list(plan.mails))

    def called(self, name: str) -> list[dict[str, Any]]:
        return [args for call, args in self.calls if call == name]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(params=["sqlite", "postgres"])
async def engine(request: pytest.FixtureRequest) -> AsyncIterator[AsyncEngine]:
    if request.param == "postgres":
        built = await postgres_engine(
            request.getfixturevalue("postgres_test_url"), "mcp_calendar_writes_test"
        )
    else:
        built = await sqlite_engine(
            (CALENDAR_ATTENDEE_INVITES_SCHEMA, MCP_WRITE_AUDIT_SCHEMA)
        )
    try:
        yield built
    finally:
        await built.dispose()


@pytest.fixture
async def backend() -> AsyncIterator[FakeBackend]:
    fake = FakeBackend()
    register_write_backend(fake)
    yield fake
    unregister_write_backend("proton")


@pytest.fixture
async def session_maker(engine: AsyncEngine, monkeypatch, backend) -> sessionmaker:
    import backend.core.db as core_db

    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(core_db, "async_session_maker", maker)
    monkeypatch.setattr(event_view_module, "_utc_now", lambda: NOW)
    async with maker() as session:
        add_account(session, connection_id=1, provider="proton")
        add_account(session, connection_id=2, provider="google")
        await session.flush()
        add_calendar(
            session,
            calendar_id=PROTON_CALENDAR,
            connection_id=1,
            time_zone="Europe/London",
        )
        add_calendar(session, calendar_id=GOOGLE_CALENDAR, connection_id=2)
        await session.commit()
    return maker


async def _alice(session_maker, scopes=None) -> User:
    async with session_maker() as session:
        user = await session.get(User, ALICE)
    assert user is not None
    if scopes is None:
        bind_identity(user)
    else:
        bind_identity(user, scopes)
    return user


async def _refused(call) -> dict[str, Any]:
    with pytest.raises(ToolError) as refused:
        await call
    return json.loads(str(refused.value))["error"]


async def _audit(session_maker) -> list[McpWriteAudit]:
    async with session_maker() as session:
        return list(
            (
                await session.execute(select(McpWriteAudit).order_by(McpWriteAudit.id))
            ).scalars()
        )


async def _seed_event(session_maker, **fields) -> int:
    async with session_maker() as session:
        add_event(
            session,
            event_id=fields.pop("event_id", 50),
            calendar_id=fields.pop("calendar_id", PROTON_CALENDAR),
            provider_revision=fields.pop("provider_revision", "p1:0:0"),
            **{"is_organiser": True, **timed(date(2026, 12, 1), 10), **fields},
        )
        await session.commit()
    return 50


# --- defaults: nobody is emailed unless asked, replies are ------------------


def test_notify_defaults() -> None:
    for tool in (create_calendar_event, update_calendar_event, cancel_calendar_event):
        assert inspect.signature(tool).parameters["notify_attendees"].default is False
    respond = inspect.signature(respond_to_calendar_invitation).parameters
    assert respond["notify_organiser"].default is True
    assert "IS EMAILED by default" in (respond_to_calendar_invitation.__doc__ or "")


@pytest.mark.anyio
async def test_create_with_attendees_tells_nobody_by_default(
    session_maker, backend
) -> None:
    await _alice(session_maker)

    result = await create_calendar_event(
        calendar_id=PROTON_CALENDAR,
        title="Design review",
        start="2026-12-01T14:00:00+00:00",
        end="2026-12-01T15:00:00+00:00",
        attendees=[BOB, CAROL, AttendeeInput(email="Alice@Example.com")],
    )

    assert result["outcome"] == "created"
    assert result["verified"] is True
    assert backend.called("create_event")[0]["share"] is False
    assert backend.called("send_notifications") == []
    notifications = result["notifications"]
    assert notifications["notify_requested"] is False
    assert notifications["anyone_notified"] is False
    assert notifications["emailed"] == []
    assert notifications["not_emailed"] == [
        {"email": "bob@example.org", "reason": "notify_attendees_off"},
        {"email": "carol@example.com", "reason": "notify_attendees_off"},
    ]
    assert notifications["summary"].startswith("Nobody was told.")
    assert "own address was left out" in result["warnings"][0]
    event = result["event"]
    assert event["title"] == "Design review"
    assert [a["email"] for a in event["attendees"]] == [
        "bob@example.org",
        "carol@example.com",
    ]
    assert event["calendar"]["writable"] is True
    async with session_maker() as session:
        invites = list(
            (await session.execute(select(CalendarAttendeeInvite))).scalars()
        )
    assert sorted(i.email for i in invites) == ["bob@example.org", "carol@example.com"]
    assert all(i.invited_at is None for i in invites)
    (row,) = await _audit(session_maker)
    assert (row.outcome, row.action, row.tool) == (
        "succeeded",
        "create",
        "create_calendar_event",
    )
    assert result["audit_id"] == row.id


@pytest.mark.anyio
async def test_notify_on_invites_and_records_it(session_maker, backend) -> None:
    await _alice(session_maker)

    result = await create_calendar_event(
        calendar_id=PROTON_CALENDAR,
        title="Design review",
        start="2026-12-01T14:00:00+00:00",
        end="2026-12-01T15:00:00+00:00",
        attendees=[BOB],
        notify_attendees=True,
    )

    assert backend.called("create_event")[0]["share"] is True
    assert backend.called("send_notifications") == [{"kinds": ["invitation"]}]
    assert result["notifications"]["emailed"] == [
        {"email": "bob@example.org", "kind": "invitation"}
    ]
    assert result["notifications"]["anyone_notified"] is True
    async with session_maker() as session:
        (invite,) = (await session.execute(select(CalendarAttendeeInvite))).scalars()
    assert invite.invited_at is not None


@pytest.mark.anyio
async def test_reply_is_emailed_by_default_and_shows_in_a_dry_run(
    session_maker, backend
) -> None:
    event_id = await _seed_event(
        session_maker,
        is_organiser=False,
        my_response="needs_action",
        organiser={"name": "Bob Example", "email": "bob@example.org", "is_self": False},
        attendees=[
            {"name": "Alice Example", "email": "alice@example.com", "is_self": True}
        ],
    )
    await _alice(session_maker)

    preview = await respond_to_calendar_invitation(event_id, "accepted", dry_run=True)
    assert preview["outcome"] == "would_respond"
    assert preview["notifications"]["would_email"] == [
        {"email": "bob@example.org", "kind": "reply"}
    ]
    assert backend.calls == []

    answered = await respond_to_calendar_invitation(
        event_id, "accepted", note="See you there"
    )
    assert answered["outcome"] == "responded"
    assert backend.called("send_notifications") == [{"kinds": ["reply"]}]
    assert backend.mails[0].note == "See you there"
    assert answered["event"]["my_response"] == "accepted"

    quiet = await respond_to_calendar_invitation(
        event_id, "declined", notify_organiser=False
    )
    assert quiet["notifications"]["not_emailed"] == [
        {"email": "bob@example.org", "reason": "notify_organiser_off"}
    ]
    assert len(backend.called("send_notifications")) == 1


# --- time zones and holds ---------------------------------------------------


@pytest.mark.anyio
async def test_weekly_series_keeps_its_zone_across_the_clock_change(
    session_maker, backend
) -> None:
    await _alice(session_maker)

    result = await create_calendar_event(
        calendar_id=PROTON_CALENDAR,
        title="Weekly sync",
        start="2026-10-20T10:00:00",
        end="2026-10-20T11:00:00",
        time_zone="Europe/London",
        recurrence="FREQ=WEEKLY;BYDAY=TU;COUNT=6",
    )

    draft = backend.called("create_event")[0]["draft"]
    assert draft.time_zone == "Europe/London"
    assert draft.start.at.isoformat() == "2026-10-20T10:00:00+01:00"
    assert draft.recurrence == "FREQ=WEEKLY;BYDAY=TU;COUNT=6"
    assert result["scope"] == "series"
    assert result["event"]["start"]["date_time"] == "2026-10-20T10:00:00+01:00"


@pytest.mark.anyio
async def test_all_day_end_is_exclusive_and_defaults_to_one_day(
    session_maker, backend
) -> None:
    await _alice(session_maker)

    one_day = await create_calendar_event(
        calendar_id=PROTON_CALENDAR, title="Away", start="2026-12-24", all_day=True
    )
    two_days = await create_calendar_event(
        calendar_id=PROTON_CALENDAR,
        title="Christmas",
        start="2026-12-24",
        end="2026-12-26",
        all_day=True,
    )

    assert one_day["event"]["end"] == {"date": "2026-12-25"}
    assert one_day["event"]["last_date"] == "2026-12-24"
    assert two_days["event"]["last_date"] == "2026-12-25"


@pytest.mark.anyio
async def test_a_hold_cannot_be_sent_until_confirmed(session_maker, backend) -> None:
    await _alice(session_maker)

    refused = await _refused(
        create_calendar_event(
            calendar_id=PROTON_CALENDAR,
            title="Hold: workshop",
            start="2026-12-01T14:00:00Z",
            end="2026-12-01T15:00:00Z",
            status="tentative",
            attendees=[BOB],
            notify_attendees=True,
        )
    )
    assert (refused["code"], refused["field"]) == (
        "invalid_argument",
        "notify_attendees",
    )

    hold = await create_calendar_event(
        calendar_id=PROTON_CALENDAR,
        title="Hold: workshop",
        start="2026-12-01T14:00:00Z",
        end="2026-12-01T15:00:00Z",
        status="tentative",
        attendees=[BOB],
    )
    assert hold["event"]["is_hold"] is True
    assert hold["notifications"]["not_emailed"] == [
        {"email": "bob@example.org", "reason": "hold_not_confirmed"}
    ]

    confirmed = await update_calendar_event(
        hold["event"]["id"], status="confirmed", notify_attendees=True
    )
    assert confirmed["outcome"] == "updated"
    assert confirmed["notifications"]["emailed"] == [
        {"email": "bob@example.org", "kind": "invitation"}
    ]
    assert backend.called("update_event")[0]["share"] is True


# --- dry runs, idempotency, change log --------------------------------------


@pytest.mark.anyio
async def test_dry_run_calls_no_backend_and_logs_nothing(
    session_maker, backend
) -> None:
    event_id = await _seed_event(session_maker)
    await _alice(session_maker)

    created = await create_calendar_event(
        calendar_id=PROTON_CALENDAR,
        title="Design review",
        start="2026-12-01T14:00:00Z",
        end="2026-12-01T15:00:00Z",
        attendees=[BOB],
        notify_attendees=True,
        dry_run=True,
    )
    updated = await update_calendar_event(event_id, title="Renamed", dry_run=True)
    cancelled = await cancel_calendar_event(event_id, dry_run=True)

    assert backend.calls == []
    assert await _audit(session_maker) == []
    assert created["outcome"] == "would_create"
    assert created["audit_id"] is None
    assert created["notifications"]["would_email"] == [
        {"email": "bob@example.org", "kind": "invitation"}
    ]
    assert created["notifications"]["anyone_notified"] is False
    assert updated["changes"] == [
        {"field": "title", "before": "Design review", "after": "Renamed"}
    ]
    assert cancelled["outcome"] == "would_cancel"
    async with session_maker() as session:
        assert (await session.get(CalendarEvent, event_id)).title == "Design review"


@pytest.mark.anyio
async def test_a_keyed_create_replays_instead_of_creating_twice(
    session_maker, backend
) -> None:
    await _alice(session_maker)
    arguments = {
        "calendar_id": PROTON_CALENDAR,
        "title": "Design review",
        "start": "2026-12-01T14:00:00Z",
        "end": "2026-12-01T15:00:00Z",
        "idempotency_key": "retry-key-001",
    }

    first = await create_calendar_event(**arguments)
    second = await create_calendar_event(**arguments)
    conflict = await _refused(create_calendar_event(**{**arguments, "title": "Other"}))

    assert len(backend.called("create_event")) == 1
    assert backend.called("create_event")[0]["uid"] == writes.derived_uid(
        ALICE, "retry-key-001"
    )
    assert second["idempotent_replay"] is True
    assert second["event"]["id"] == first["event"]["id"]
    assert conflict["code"] == "idempotency_conflict"


@pytest.mark.anyio
async def test_an_uncertain_keyed_create_is_found_by_its_uid(
    session_maker, backend
) -> None:
    await _alice(session_maker)
    arguments = {
        "calendar_id": PROTON_CALENDAR,
        "title": "Design review",
        "start": "2026-12-01T14:00:00Z",
        "end": "2026-12-01T15:00:00Z",
        "idempotency_key": "retry-key-002",
    }
    backend.fail_with = CalendarToolError(
        "provider_unavailable", "timed out", details={"outcome_unknown": True}
    )

    refused = await _refused(create_calendar_event(**arguments))
    assert refused["details"]["outcome_unknown"] is True
    (row,) = await _audit(session_maker)
    assert row.outcome == "uncertain"

    # The write had landed before the timeout; the retry finds it by UID.
    uid = writes.derived_uid(ALICE, "retry-key-002")
    draft = backend.called("create_event")[0]["draft"]
    backend.landed[uid] = draft_record(draft, uid, uid=uid)
    backend.fail_with = None
    retried = await create_calendar_event(**arguments)

    assert len(backend.called("create_event")) == 1
    assert backend.called("get_event") == [{"uid": uid}]
    assert retried["outcome"] == "created"
    assert retried["idempotent_replay"] is True
    (row,) = await _audit(session_maker)
    assert row.outcome == "succeeded"


@pytest.mark.anyio
async def test_an_unkeyed_timeout_says_to_list_before_retrying(
    session_maker, backend
) -> None:
    await _alice(session_maker)
    backend.fail_with = CalendarToolError(
        "provider_unavailable", "timed out", details={"outcome_unknown": True}
    )

    refused = await _refused(
        create_calendar_event(
            calendar_id=PROTON_CALENDAR,
            title="Design review",
            start="2026-12-01T14:00:00Z",
            end="2026-12-01T15:00:00Z",
        )
    )

    assert refused["code"] == "provider_unavailable"
    assert refused["retryable"] is True
    assert "List the calendar" in refused["message"]


@pytest.mark.anyio
async def test_change_log_records_each_outcome(session_maker, backend) -> None:
    event_id = await _seed_event(session_maker)
    await _alice(session_maker)

    await update_calendar_event(event_id, title="Renamed")
    await _refused(
        update_calendar_event(event_id, title="Again", expected_revision="stale")
    )
    backend.fail_with = CalendarToolError("provider_rejected", "no")
    await _refused(update_calendar_event(event_id, title="Third"))
    backend.fail_with = None
    cancelled = await cancel_calendar_event(event_id)

    rows = await _audit(session_maker)
    assert [(r.action, r.outcome, r.error_code) for r in rows] == [
        ("update", "succeeded", None),
        ("update", "refused", "conflict"),
        ("update", "failed", "provider_rejected"),
        ("cancel", "succeeded", None),
    ]
    assert rows[0].changes == [
        {"field": "title", "before": "Design review", "after": "Renamed"},
        {"field": "revision", "before": "p1:0:0", "after": "p1:2:1"},
    ]
    # A cancel keeps the event's whole last state to recreate it by hand.
    assert rows[3].changes["title"] == "Renamed"
    assert rows[3].changes["status"] == "cancelled"
    assert rows[3].event_title == "Renamed"
    assert cancelled["event"]["status"] == "cancelled"
    async with session_maker() as session:
        assert await session.get(CalendarEvent, event_id) is None


@pytest.mark.anyio
async def test_cancel_warns_that_earlier_invitees_keep_it(
    session_maker, backend
) -> None:
    # Not created through Nojoin, so whoever made it invited Bob.
    event_id = await _seed_event(
        session_maker,
        attendees=[{"name": "Bob Example", "email": "bob@example.org"}],
    )
    await _alice(session_maker)

    quiet = await cancel_calendar_event(event_id)

    assert quiet["notifications"]["not_emailed"] == [
        {"email": "bob@example.org", "reason": "notify_attendees_off"}
    ]
    assert "still has this event" in quiet["warnings"][0]
    assert backend.called("send_notifications") == []


@pytest.mark.anyio
async def test_the_change_log_api_and_its_retention(session_maker, backend) -> None:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from backend.api.deps import get_current_user, get_db
    from backend.api.v1.endpoints.calendar import router

    event_id = await _seed_event(session_maker)
    user = await _alice(session_maker)
    await update_calendar_event(event_id, title="Renamed")

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/calendar")

    async def override_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        page = (await client.get("/api/v1/calendar/assistant-changes")).json()
        other = (
            await client.get("/api/v1/calendar/assistant-changes?connection_id=2")
        ).json()

    assert page["total"] == 1
    (item,) = page["items"]
    assert (item["tool"], item["action"], item["outcome"]) == (
        "update_calendar_event",
        "update",
        "succeeded",
    )
    assert item["event_title"] == "Renamed"
    assert item["calendar_name"] == "Personal"
    assert item["notifications_summary"].startswith("Nobody was told")
    assert other["total"] == 0

    async with session_maker() as session:
        (row,) = (await session.execute(select(McpWriteAudit))).scalars()
        row.created_at = NOW - timedelta(days=10)
        session.add(row)
        await session.commit()
        assert await prune_audit(session, now=NOW) == 0
        assert await prune_audit(session, now=NOW + timedelta(days=400)) == 1


# --- locks, tokens, the reconcile guard -------------------------------------


@pytest.mark.anyio
async def test_a_held_account_lock_answers_busy(
    session_maker, backend, monkeypatch
) -> None:
    event_id = await _seed_event(session_maker)
    await _alice(session_maker)
    monkeypatch.setattr(write_steps, "LOCK_WAIT_SECONDS", 0.3)

    async with advisory_lock(connection_lock_key(1), wait=1):
        refused = await _refused(update_calendar_event(event_id, title="Renamed"))

    assert (refused["code"], refused["retryable"]) == ("busy", True)
    assert backend.called("update_event") == []
    assert [r.outcome for r in await _audit(session_maker)] == ["refused"]


@pytest.mark.anyio
async def test_rotated_tokens_are_committed_at_once(session_maker) -> None:
    await ConnectionTokenStore(1).save(
        access_token="access-2",
        refresh_token="refresh-2",
        expires_at=NOW,
        secrets={"uid": "session-uid"},
    )

    async with session_maker() as session:
        connection = await session.get(CalendarConnection, 1)
    assert decrypt_secret(connection.refresh_token_encrypted) == "refresh-2"
    assert json.loads(decrypt_secret(connection.provider_secrets_encrypted)) == {
        "uid": "session-uid"
    }


@pytest.mark.anyio
async def test_a_row_written_during_a_full_fetch_is_kept(session_maker) -> None:
    await _seed_event(session_maker)
    async with session_maker() as session:
        await _reconcile_calendar_events(
            session,
            PROTON_CALENDAR,
            [],
            datetime(2025, 11, 1),
            fetch_started_at=datetime(2000, 1, 1),
        )
        await session.commit()
        assert await session.get(CalendarEvent, 50) is not None

        await _reconcile_calendar_events(
            session,
            PROTON_CALENDAR,
            [],
            datetime(2025, 11, 1),
            fetch_started_at=datetime(2100, 1, 1),
        )
        await session.commit()
        assert await session.get(CalendarEvent, 50) is None


# --- every structured error code --------------------------------------------


def _create(**overrides):
    arguments = {
        "calendar_id": PROTON_CALENDAR,
        "title": "Design review",
        "start": "2026-12-01T14:00:00Z",
        "end": "2026-12-01T15:00:00Z",
        **overrides,
    }
    return create_calendar_event(**arguments)


def _provider_failure(code: str) -> Callable[[FakeBackend, int], Any]:
    def call(backend: FakeBackend, event_id: int):
        backend.fail_with = CalendarToolError(code, f"{code} from the provider")
        return update_calendar_event(event_id, title="Renamed")

    return call


def _http_failure(status: int) -> Callable[[FakeBackend, int], Any]:
    import httpx

    def call(backend: FakeBackend, event_id: int):
        request = httpx.Request("PUT", "https://calendar.example.com/e")
        response = httpx.Response(status, request=request, json={})

        async def failing(*args, **kwargs):
            raise httpx.HTTPStatusError("failed", request=request, response=response)

        backend.update_event = failing  # type: ignore[method-assign]
        return update_calendar_event(event_id, title="Renamed")

    return call


ERROR_CASES: dict[str, Callable[[FakeBackend, int], Any]] = {
    "invalid_argument": lambda b, e: update_calendar_event(e),
    "invalid_time": lambda b, e: _create(
        start="2026-03-29T01:30:00", time_zone="Europe/London"
    ),
    "ambiguous_time": lambda b, e: _create(
        start="2026-10-25T01:30:00",
        end="2026-10-25T03:00:00",
        time_zone="Europe/London",
    ),
    "scope_required": lambda b, e: update_calendar_event(e + 1, title="Renamed"),
    "unsupported": lambda b, e: update_calendar_event(
        e + 1, scope="series", start="2026-12-08T10:00:00Z"
    ),
    "not_found": lambda b, e: update_calendar_event(999, title="Renamed"),
    "not_writable": lambda b, e: _create(calendar_id=GOOGLE_CALENDAR),
    "not_organiser": lambda b, e: cancel_calendar_event(e + 2),
    "not_attendee": lambda b, e: respond_to_calendar_invitation(e, "accepted"),
    "conflict": lambda b, e: update_calendar_event(
        e, title="Renamed", expected_revision="x"
    ),
    "idempotency_conflict": None,  # covered by the keyed-create test
    "write_scope_missing": None,  # covered below
    "auth_expired": _http_failure(401),
    "rate_limited": _http_failure(429),
    "busy": None,  # covered by the lock test
    "provider_unavailable": _http_failure(503),
    "provider_rejected": _provider_failure("provider_rejected"),
}


def test_every_error_code_has_a_case() -> None:
    assert set(ERROR_CASES) == set(ERROR_CODES)


@pytest.mark.anyio
@pytest.mark.parametrize("code", [code for code, case in ERROR_CASES.items() if case])
async def test_each_error_code_is_one_json_object(session_maker, backend, code) -> None:
    event_id = await _seed_event(session_maker)
    await _seed_event(
        session_maker,
        event_id=51,
        provider_event_id="weekly#1",
        series_key="weekly",
        occurrence_type="occurrence",
        original_start=datetime(2026, 12, 1, 10, 0),
    )
    await _seed_event(
        session_maker,
        event_id=52,
        provider_event_id="invite",
        is_organiser=False,
        attendees=[
            {"name": "Alice Example", "email": "alice@example.com", "is_self": True}
        ],
    )
    await _alice(session_maker)

    error = await _refused(ERROR_CASES[code](backend, event_id))

    assert error["code"] == code
    assert set(error) == {"code", "message", "retryable", "field", "details"}
    assert error["retryable"] is ERROR_CODES[code]
    assert "Traceback" not in error["message"]
    if code == "not_writable":
        assert error["details"] == {"reason": "provider_read_only"}


@pytest.mark.anyio
async def test_a_read_only_grant_cannot_write(session_maker, backend, caplog) -> None:
    await _alice(session_maker, scopes=frozenset({MCP_READ_SCOPE}))

    error = await _refused(_create(attendees=[BOB]))

    assert error["code"] == "write_scope_missing"
    assert backend.calls == []
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "write_scope_missing" in logged
    assert "bob@example.org" not in logged


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"is_read_only": True}, "calendar_read_only"),
        ({"is_selected": False}, "not_selected"),
        ("reconnect", "reconnect_required"),
    ],
)
async def test_not_writable_says_why(session_maker, backend, change, reason) -> None:
    from backend.models.calendar import CalendarSource

    async with session_maker() as session:
        if change == "reconnect":
            connection = await session.get(CalendarConnection, 1)
            connection.sync_status = "reauthorisation_required"
            session.add(connection)
        else:
            source = await session.get(CalendarSource, PROTON_CALENDAR)
            for name, value in change.items():
                setattr(source, name, value)
            session.add(source)
        await session.commit()
    await _alice(session_maker)

    error = await _refused(_create())

    assert error["code"] == "not_writable"
    assert error["details"] == {"reason": reason}
    assert backend.calls == []


@pytest.mark.anyio
async def test_provider_limits_come_from_the_backend(session_maker, backend) -> None:
    await _alice(session_maker)

    error = await _refused(_create(description="x" * 3001))

    assert (error["code"], error["field"]) == ("invalid_argument", "description")
    assert await _audit(session_maker) == []
