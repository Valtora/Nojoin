"""Proton writes through the MCP tools, the helper replaced by a logging fake.

Checks what Nojoin asks the helper for: the hold marker and its title prefix,
no attendee keys and no mail unless notifying was asked for, the never-invited
attendees a confirmed hold hands the event to, iTIP mail of the right method
to the right people, replies by default, and the helper's refusals as the
tools' error codes. Every account is made up.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlmodel import select

from backend.core.encryption import encrypt_secret
from backend.mcp_server.tools_calendar import (
    AttendeeInput,
    cancel_calendar_event,
    create_calendar_event,
    respond_to_calendar_invitation,
    update_calendar_event,
)
from backend.models.calendar import CalendarConnection, CalendarEvent, CalendarSource
from backend.models.user import User
from backend.services.calendar_service import event_view as event_view_module
from backend.services.calendar_service.proton_session import seal_secrets
from backend.tests.calendar_tool_support import ALICE, NOW, bind_identity, sqlite_engine
from backend.tests.proton_fake_helper import helper_event, install_fake_helper
from backend.tests.sqlite_schemas import (
    CALENDAR_ATTENDEE_INVITES_SCHEMA,
    MCP_WRITE_AUDIT_SCHEMA,
)

BOB = AttendeeInput(email="bob@example.org", name="Bob Example")
BOB_ON_EVENT = {
    "name": "Bob Example",
    "email": "bob@example.org",
    "response": "needs_action",
    "role": "required",
    "is_self": False,
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    built = await sqlite_engine(
        (CALENDAR_ATTENDEE_INVITES_SCHEMA, MCP_WRITE_AUDIT_SCHEMA)
    )
    yield built
    await built.dispose()


@pytest.fixture
async def session_maker(engine: AsyncEngine, monkeypatch) -> sessionmaker:
    import backend.core.db as core_db

    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(core_db, "async_session_maker", maker)
    monkeypatch.setattr(event_view_module, "_utc_now", lambda: NOW)
    async with maker() as session:
        session.add(
            CalendarConnection(
                id=1,
                user_id=ALICE,
                provider="proton",
                provider_account_id="proton-user-1",
                email="alice@example.com",
                display_name="Alice Example",
                access_token_encrypted=encrypt_secret("access-1"),
                refresh_token_encrypted=encrypt_secret("refresh-1"),
                provider_secrets_encrypted=seal_secrets("session-uid", "salted"),
                sync_status="success",
                granted_scopes=[],
            )
        )
        await session.flush()
        session.add(
            CalendarSource(
                id=1,
                connection_id=1,
                provider_calendar_id="cal-proton",
                name="Personal",
                is_primary=True,
                is_selected=True,
                time_zone="Europe/London",
                last_synced_at=NOW,
            )
        )
        await session.commit()
    async with maker() as session:
        bind_identity(await session.get(User, ALICE))
    return maker


@pytest.fixture
def helper(tmp_path, monkeypatch):
    return install_fake_helper(tmp_path, monkeypatch)


def _written(*events: dict, **extra) -> dict:
    return {
        "written": True,
        "verified": True,
        "events": list(events),
        "deleted_keys": [],
        "before": [],
        "revision": events[0]["revision"] if events else None,
        "warnings": [],
        "stats": {"occurrences": None, "exceptions_removed": 0},
        "itip": {"ics": "BEGIN:VEVENT\r\nEND:VEVENT", "uid": "x", "sequence": 0},
        **extra,
    }


def _requests(helper, op: str) -> list[dict]:
    return [
        call["request"]["args"]
        for call in helper.calls()
        if call["request"]["op"] == op
    ]


async def _store(session_maker, **fields) -> int:
    async with session_maker() as session:
        event = CalendarEvent(
            id=fields.pop("id", 10),
            calendar_id=1,
            provider_event_id=fields.pop("provider_event_id", "hold@nojoin"),
            title=fields.pop("title", "Hold: Design review"),
            status=fields.pop("status", "tentative"),
            is_organiser=fields.pop("is_organiser", True),
            starts_at=NOW.replace(day=1, month=12, hour=14),
            ends_at=NOW.replace(day=1, month=12, hour=15),
            start_time_zone="Europe/London",
            provider_revision="p1:1:0",
            provider_data=fields.pop("provider_data", {"uid": "hold@nojoin"}),
            **fields,
        )
        session.add(event)
        await session.commit()
        return event.id


@pytest.mark.anyio
async def test_a_hold_is_marked_prefixed_and_sent_to_nobody(
    session_maker, helper
) -> None:
    helper.ok(
        "create_event",
        _written(
            helper_event(
                "made@nojoin",
                title="Hold: Design review",
                is_hold=True,
                status="tentative",
                attendees=[{**BOB_ON_EVENT, "token": "t1"}],
            )
        ),
    )

    result = await create_calendar_event(
        calendar_id=1,
        title="Design review",
        start="2026-12-01T14:00:00Z",
        end="2026-12-01T15:00:00Z",
        status="tentative",
        attendees=[BOB],
    )

    (args,) = _requests(helper, "create_event")
    assert args["event"]["title"] == "Hold: Design review"
    assert args["event"]["hold"] is True
    assert args["event"]["start"] == {
        "date_time": "2026-12-01T14:00:00+00:00",
        "time_zone": "Europe/London",
    }
    assert args["share_with_attendees"] is False
    assert args["uid"].endswith("@nojoin")
    assert "send_itip" not in helper.ops()
    assert result["event"]["is_hold"] is True
    assert result["event"]["title"] == "Hold: Design review"
    assert result["notifications"]["not_emailed"] == [
        {"email": "bob@example.org", "reason": "hold_not_confirmed"}
    ]


@pytest.mark.anyio
async def test_confirming_a_hold_with_notify_invites_its_attendees(
    session_maker, helper
) -> None:
    event_id = await _store(session_maker, attendees=[BOB_ON_EVENT])
    async with session_maker() as session:
        # Nojoin created it and has not invited Bob.
        from backend.models.calendar import CalendarAttendeeInvite

        session.add(
            CalendarAttendeeInvite(
                calendar_source_id=1, event_uid="hold@nojoin", email="bob@example.org"
            )
        )
        await session.commit()
    confirmed = helper_event(
        "hold@nojoin",
        title="Design review",
        attendees=[BOB_ON_EVENT],
        revision="p1:2:1",
    )
    helper.ok("update_event", _written(confirmed))
    helper.ok(
        "send_itip",
        {
            "message_id": "m1",
            "sent": [{"email": "bob@example.org", "scheme": "clear"}],
            "failed": [],
        },
    )

    result = await update_calendar_event(
        event_id, status="confirmed", notify_attendees=True
    )

    (update,) = _requests(helper, "update_event")
    assert update["patch"] == {"hold": False, "title": "Design review"}
    assert update["share_with_attendees"] is True
    assert update["share_with"] == ["bob@example.org"]
    assert update["scope"] == "single"
    (mail,) = _requests(helper, "send_itip")
    assert mail["method"] == "REQUEST"
    assert mail["recipients"] == [{"email": "bob@example.org", "name": "Bob Example"}]
    assert mail["subject"] == "Invitation: Design review @ Tue 1 Dec 14:00"
    assert result["notifications"]["emailed"] == [
        {"email": "bob@example.org", "kind": "invitation"}
    ]
    assert result["event"]["is_hold"] is False


@pytest.mark.anyio
async def test_an_update_without_notify_sends_no_keys_and_no_mail(
    session_maker, helper
) -> None:
    event_id = await _store(
        session_maker,
        title="Design review",
        status="confirmed",
        attendees=[BOB_ON_EVENT],
    )
    helper.ok("update_event", _written(helper_event("hold@nojoin", title="Renamed")))

    await update_calendar_event(event_id, title="Renamed")

    (update,) = _requests(helper, "update_event")
    assert update["patch"] == {"title": "Renamed"}
    assert update["share_with_attendees"] is False
    assert update["share_with"] == []
    assert "send_itip" not in helper.ops()


@pytest.mark.anyio
async def test_cancelling_an_occurrence_mails_a_cancel_for_it(
    session_maker, helper
) -> None:
    event_id = await _store(
        session_maker,
        title="Weekly sync",
        status="confirmed",
        provider_event_id="weekly@nojoin#20261201T140000Z",
        series_key="weekly@nojoin",
        occurrence_type="occurrence",
        provider_data={"uid": "weekly@nojoin"},
        attendees=[BOB_ON_EVENT],
    )
    before = helper_event(
        "weekly@nojoin", recurrence_id="20261201T140000Z", title="Weekly sync"
    )
    helper.ok(
        "cancel_event",
        _written(before=[before], deleted_keys=["weekly@nojoin#20261201T140000Z"]),
    )
    helper.ok("send_itip", {"sent": [{"email": "bob@example.org"}], "failed": []})

    result = await cancel_calendar_event(
        event_id, scope="occurrence", notify_attendees=True
    )

    (cancel,) = _requests(helper, "cancel_event")
    assert cancel["target"] == {
        "uid": "weekly@nojoin",
        "recurrence_id": "20261201T140000Z",
    }
    assert cancel["scope"] == "occurrence"
    (mail,) = _requests(helper, "send_itip")
    assert (mail["method"], mail["recurrence_id"]) == ("CANCEL", "20261201T140000Z")
    assert mail["subject"].startswith("Cancelled: Weekly sync @ ")
    assert result["outcome"] == "cancelled"
    async with session_maker() as session:
        assert await session.get(CalendarEvent, event_id) is None


@pytest.mark.anyio
async def test_a_reply_goes_to_the_organiser_by_default(session_maker, helper) -> None:
    event_id = await _store(
        session_maker,
        title="Planning",
        status="confirmed",
        is_organiser=False,
        my_response="needs_action",
        organiser={"name": "Bob Example", "email": "bob@example.org", "is_self": False},
    )
    answered = helper_event(
        "hold@nojoin",
        title="Planning",
        is_organiser=False,
        my_response="accepted",
        organiser={"name": "Bob Example", "email": "bob@example.org", "is_self": False},
    )
    helper.ok(
        "respond",
        _written(
            answered,
            organiser_sees_answer=True,
            shown_by_provider=[{"name": "Bob Example", "email": "bob@example.org"}],
        ),
    )
    helper.ok("send_itip", {"sent": [{"email": "bob@example.org"}], "failed": []})

    result = await respond_to_calendar_invitation(
        event_id, "accepted", note="Will be there"
    )

    (respond,) = _requests(helper, "respond")
    assert respond["response"] == "accepted"
    assert respond["target"]["recurrence_id"] is None
    (mail,) = _requests(helper, "send_itip")
    assert (mail["method"], mail["response"], mail["note"]) == (
        "REPLY",
        "accepted",
        "Will be there",
    )
    assert mail["subject"] == "Accepted: Planning"
    assert result["notifications"]["shown_by_provider"] == [
        {"email": "bob@example.org"}
    ]
    assert result["event"]["my_response"] == "accepted"


@pytest.mark.anyio
async def test_a_mail_failure_does_not_undo_the_write(session_maker, helper) -> None:
    helper.ok(
        "create_event", _written(helper_event("made@nojoin", attendees=[BOB_ON_EVENT]))
    )
    helper.fail("send_itip", "provider_rejected", provider_code=2026)

    result = await create_calendar_event(
        calendar_id=1,
        title="Design review",
        start="2026-12-01T14:00:00Z",
        end="2026-12-01T15:00:00Z",
        attendees=[BOB],
        notify_attendees=True,
    )

    assert result["outcome"] == "created"
    assert result["notifications"]["emailed"] == []
    assert result["notifications"]["failed"] == [
        {"email": "bob@example.org", "error": "provider_rejected"}
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("helper_code", "error", "code"),
    [
        ("conflict", {"details": {"current_revision": "p1:9:3"}}, "conflict"),
        ("invalid_time", {"field": "event.start"}, "invalid_time"),
        ("not_writable", {}, "not_writable"),
        ("provider_rejected", {"provider_code": 2001}, "provider_rejected"),
        ("invalid_request", {}, "provider_unavailable"),
        ("rate_limited", {"details": {"retry_after_seconds": 9}}, "rate_limited"),
    ],
)
async def test_helper_refusals_become_tool_errors(
    session_maker, helper, helper_code, error, code
) -> None:
    helper.answer(
        "create_event",
        {"ok": False, "error": {"code": helper_code, "message": "refused", **error}},
    )

    with pytest.raises(ToolError) as refused:
        await create_calendar_event(
            calendar_id=1,
            title="Design review",
            start="2026-12-01T14:00:00Z",
            end="2026-12-01T15:00:00Z",
        )

    payload = json.loads(str(refused.value))["error"]
    assert payload["code"] == code
    if helper_code == "invalid_time":
        assert payload["field"] == "start"
    if helper_code == "not_writable":
        assert payload["details"]["reason"] == "calendar_read_only"
    async with session_maker() as session:
        from backend.models.mcp_write_audit import McpWriteAudit

        (row,) = (await session.execute(select(McpWriteAudit))).scalars()
    assert row.error_code == code


@pytest.mark.anyio
async def test_proton_limits_are_checked_before_the_helper_runs(
    session_maker, helper
) -> None:
    with pytest.raises(ToolError) as refused:
        await create_calendar_event(
            calendar_id=1,
            title="Daily stand-up",
            start="2026-12-01T09:00:00Z",
            end="2026-12-01T09:15:00Z",
            recurrence="FREQ=DAILY;COUNT=60",
        )

    payload = json.loads(str(refused.value))["error"]
    assert (payload["code"], payload["field"]) == ("invalid_argument", "recurrence")
    assert helper.calls() == []
