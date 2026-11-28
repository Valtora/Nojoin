"""The Python side of Proton Calendar: the helper boundary, sessions, connect,
sync and backups, against a fake helper that logs every request it gets.

Nothing here reaches Proton or any network: the helper is a script in the
test's own directory, and every account is made up.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlmodel import select

from backend.core.encryption import decrypt_secret, encrypt_secret
from backend.models.calendar import CalendarConnection, CalendarEvent, CalendarSource
from backend.models.user import User
from backend.services.calendar_service import proton_connect, proton_helper
from backend.services.calendar_service.proton_backend import ProtonBackend
from backend.services.calendar_service.proton_helper import HelperError, call_helper
from backend.services.calendar_service.proton_mapping import (
    with_hold_prefix,
    without_hold_prefix,
)
from backend.services.calendar_service.proton_session import run_op, seal_secrets
from backend.services.calendar_service.sync import (
    disconnect_connection,
    sync_connection_in_session,
)
from backend.services.calendar_service.tool_errors import CalendarToolError
from backend.services.calendar_service.write_backend import write_backend_for
from backend.tests.calendar_tool_support import ALICE, sqlite_engine
from backend.tests.proton_fake_helper import (
    calendars_result,
    helper_event,
    install_fake_helper,
)

SESSION = {
    "uid": "session-uid",
    "access_token": "access-1",
    "refresh_token": "refresh-1",
    "expires_in": 86400,
    "scopes": ["calendar"],
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    built = await sqlite_engine()
    yield built
    await built.dispose()


@pytest.fixture
def session_maker(engine: AsyncEngine, monkeypatch) -> sessionmaker:
    import backend.core.db as core_db

    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(core_db, "async_session_maker", maker)
    return maker


@pytest.fixture
def helper(tmp_path, monkeypatch):
    return install_fake_helper(tmp_path, monkeypatch)


@pytest.fixture
def flows(monkeypatch) -> dict:
    """The connect flow's short-lived store, in memory instead of Redis."""
    store: dict = {}

    async def save(state, payload):
        store[state] = payload

    async def pop(state):
        return store.pop(state, None)

    monkeypatch.setattr(proton_connect, "_save_oauth_state", save)
    monkeypatch.setattr(proton_connect, "_pop_oauth_state", pop)
    return store


async def _proton_connection(session_maker, **overrides) -> int:
    async with session_maker() as session:
        session.add(
            CalendarConnection(
                id=7,
                user_id=ALICE,
                provider="proton",
                provider_account_id="proton-user-1",
                email="alice@example.com",
                access_token_encrypted=encrypt_secret("access-1"),
                refresh_token_encrypted=encrypt_secret("refresh-1"),
                provider_secrets_encrypted=seal_secrets(
                    "session-uid", "salted-passphrase"
                ),
                token_expires_at=overrides.pop("token_expires_at", None),
                sync_status="success",
                granted_scopes=[],
                **overrides,
            )
        )
        await session.flush()
        session.add(
            CalendarSource(
                connection_id=7,
                provider_calendar_id="cal-proton",
                name="Personal",
                is_primary=True,
                is_selected=True,
            )
        )
        await session.commit()
    return 7


# --- the process boundary ---------------------------------------------------


@pytest.mark.anyio
async def test_secrets_travel_on_stdin_only(helper, monkeypatch) -> None:
    monkeypatch.setenv("NOJOIN_SECRET_SHOULD_NOT_LEAK", "leak")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    helper.ok("list_calendars", calendars_result())

    result = await call_helper(
        "list_calendars", session={"uid": "session-uid", "access_token": "access-1"}
    )

    assert result["calendars"][0]["id"] == "cal-proton"
    (call,) = helper.calls()
    assert call["request"]["session"] == {
        "uid": "session-uid",
        "access_token": "access-1",
    }
    assert call["request"]["v"] == 1
    assert "access-1" not in " ".join(call["argv"])
    # LC_CTYPE is the fake's own Python coercing the C locale, not Nojoin's.
    assert set(call["env"]) - {"LC_CTYPE"} <= {
        "HTTPS_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
    }
    assert "access-1" not in json.dumps(call["env"])


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("answer", "op", "outcome_unknown"),
    [
        ({"exit": 3}, "list_calendars", False),
        ({"raw": "not json"}, "create_event", True),
        ({"raw": '{"v": 2, "ok": true}'}, "list_calendars", False),
    ],
)
async def test_a_crash_is_provider_unavailable(
    helper, answer, op, outcome_unknown
) -> None:
    helper.answer(op, answer)

    with pytest.raises(HelperError) as crashed:
        await call_helper(op)

    assert crashed.value.code == "provider_unavailable"
    assert crashed.value.details["outcome_unknown"] is outcome_unknown


@pytest.mark.anyio
async def test_a_timeout_kills_the_helper(helper) -> None:
    helper.answer("update_event", {"sleep": 30, "ok": True, "result": {}})

    started = time.monotonic()
    with pytest.raises(HelperError) as slow:
        await call_helper("update_event", timeout=0.5)

    assert time.monotonic() - started < 5
    assert slow.value.details == {"outcome_unknown": True}


@pytest.mark.anyio
async def test_oversized_output_is_refused(helper, monkeypatch) -> None:
    monkeypatch.setattr(proton_helper, "MAX_OUTPUT_BYTES", 64)
    helper.ok("list_events", {"events": [helper_event("big@nojoin")]})

    with pytest.raises(HelperError) as large:
        await call_helper("list_events")

    assert large.value.code == "provider_unavailable"


@pytest.mark.anyio
async def test_the_helpers_refusal_keeps_its_fields(helper) -> None:
    helper.fail(
        "update_event",
        "conflict",
        details={"current_revision": "p1:9:2"},
        provider_code=2000,
    )

    with pytest.raises(HelperError) as refused:
        await call_helper("update_event")

    assert (refused.value.code, refused.value.provider_code) == ("conflict", 2000)
    assert refused.value.details == {"current_revision": "p1:9:2"}


# --- sessions ---------------------------------------------------------------


@pytest.mark.anyio
async def test_an_expired_token_is_refreshed_committed_then_retried(
    session_maker, helper
) -> None:
    await _proton_connection(session_maker)
    helper.answer(
        "list_calendars",
        {"ok": False, "error": {"code": "access_token_expired", "message": "401"}},
        {"ok": True, "result": calendars_result()},
    )
    helper.ok(
        "refresh",
        {
            "session": {
                **SESSION,
                "access_token": "access-2",
                "refresh_token": "refresh-2",
            }
        },
    )

    async with session_maker() as session:
        connection = await session.get(CalendarConnection, 7)
        await run_op(session, connection, "list_calendars", {}, timeout=5)

    assert helper.ops() == ["list_calendars", "refresh", "list_calendars"]
    refresh, retried = helper.calls()[1:]
    assert refresh["request"]["session"] == {
        "uid": "session-uid",
        "refresh_token": "refresh-1",
    }
    assert retried["request"]["session"]["access_token"] == "access-2"
    async with session_maker() as session:
        stored = await session.get(CalendarConnection, 7)
    assert decrypt_secret(stored.refresh_token_encrypted) == "refresh-2"


@pytest.mark.anyio
async def test_a_refused_refresh_asks_for_a_reconnect(session_maker, helper) -> None:
    await _proton_connection(session_maker, token_expires_at=datetime(2000, 1, 1))
    helper.fail("refresh", "auth_expired", details={"reason": "refresh_refused"})

    async with session_maker() as session:
        connection = await session.get(CalendarConnection, 7)
        with pytest.raises(CalendarToolError) as refused:
            await run_op(session, connection, "get_event", {}, timeout=5)

    assert refused.value.code == "auth_expired"
    assert helper.ops() == ["refresh"]
    async with session_maker() as session:
        assert (await session.get(CalendarConnection, 7)).sync_status == (
            "reauthorisation_required"
        )


# --- connecting -------------------------------------------------------------


def _unlocked() -> dict:
    return {
        "session": {
            "uid": "session-uid",
            "access_token": "access-1",
            "key_passphrase": "salted",
        },
        "user": {
            "id": "proton-user-1",
            "name": "alice",
            "display_name": "Alice Example",
            "email": "alice@example.com",
        },
        "addresses": [],
        **calendars_result(),
        "warnings": [],
    }


@pytest.mark.anyio
async def test_connect_with_two_factor_saves_the_session_and_queues_the_sync(
    session_maker, helper, flows, monkeypatch
) -> None:
    queued: list = []

    async def dispatch(name, *, args=None, **kwargs):
        assert name == "backend.worker.tasks.sync_calendar_connection_task"
        queued.append(args)
        return object()

    monkeypatch.setattr(proton_connect, "dispatch_task_best_effort", dispatch)
    helper.ok(
        "login_start",
        {
            "session": SESSION,
            "user_id": "proton-user-1",
            "password_mode": 1,
            "next": "two_factor",
        },
    )
    helper.ok("login_2fa", {"session": SESSION, "next": "unlock"})
    helper.ok("login_unlock", _unlocked())
    helper.ok("list_calendars", calendars_result())
    helper.ok(
        "list_events",
        {"events": [helper_event("planning@nojoin")], "cursor": "cursor-1"},
    )
    async with session_maker() as session:
        alice = await session.get(User, ALICE)
        first = await proton_connect.start(
            session, alice, username="alice@example.com", password="correct horse"
        )
        assert first.step == "two_factor"
        done = await proton_connect.two_factor(
            session,
            alice,
            first.flow_id,
            code="123456",
            username="alice@example.com",
            password="correct horse",
        )

    assert done.step == "done"
    assert flows == {}
    connection = done.connection
    assert (connection.provider, connection.provider_account_id) == (
        "proton",
        "proton-user-1",
    )
    secrets = json.loads(decrypt_secret(connection.provider_secrets_encrypted))
    assert secrets == {"uid": "session-uid", "key_passphrase": "salted"}
    assert decrypt_secret(connection.refresh_token_encrypted) == "refresh-1"
    async with session_maker() as session:
        sources = {
            row.provider_calendar_id: row
            for row in (await session.execute(select(CalendarSource))).scalars()
        }
        events = list((await session.execute(select(CalendarEvent))).scalars())
    assert sources["cal-proton"].is_selected and sources["cal-proton"].is_primary
    assert sources["cal-holidays"].is_read_only
    # The first sync is queued on worker-io; the sign-in does not wait for it.
    assert events == []
    assert queued == [[connection.id]]
    assert connection.sync_status == "syncing"
    assert connection.sync_progress_done == 0
    assert "list_events" not in helper.ops()
    # The password went to the helper and nowhere else Nojoin keeps.
    unlock_call = helper.calls()[2]["request"]
    assert unlock_call["args"]["password"] == "correct horse"
    assert "correct horse" not in json.dumps(list(flows.values()))


@pytest.mark.anyio
async def test_human_verification_and_wrong_codes_come_back_to_the_modal(
    session_maker, helper, flows
) -> None:
    helper.fail(
        "login_start",
        "human_verification_required",
        details={
            "methods": ["captcha"],
            "token": "hv-token",
            "verify_url": "https://verify.proton.me/?methods=captcha&token=hv-token",
        },
    )
    async with session_maker() as session:
        alice = await session.get(User, ALICE)
        with pytest.raises(proton_connect.ProtonConnectError) as check:
            await proton_connect.start(session, alice, username="alice", password="pw")
    assert check.value.code == "human_verification_required"
    assert check.value.details["verify_url"].startswith("https://verify.proton.me/")

    helper.ok(
        "login_start", {"session": SESSION, "password_mode": 2, "next": "two_factor"}
    )
    helper.fail("login_2fa", "two_factor_failed")
    async with session_maker() as session:
        alice = await session.get(User, ALICE)
        step = await proton_connect.start(
            session,
            alice,
            username="alice",
            password="pw",
            verification={"token": "hv-token", "type": "captcha"},
        )
        with pytest.raises(proton_connect.ProtonConnectError) as wrong:
            await proton_connect.two_factor(
                session,
                alice,
                step.flow_id,
                code="000000",
                username="alice",
                password="pw",
            )
    assert helper.calls()[1]["request"]["args"]["human_verification"] == {
        "token": "hv-token",
        "type": "captcha",
    }
    assert wrong.value.code == "two_factor_failed"
    # A wrong code keeps the flow, so the next one can be tried.
    assert len(flows) == 1


@pytest.mark.anyio
async def test_disconnect_ends_the_proton_session(session_maker, helper) -> None:
    await _proton_connection(session_maker)
    helper.ok("logout", {"ended": True})

    async with session_maker() as session:
        alice = await session.get(User, ALICE)
        await disconnect_connection(session, alice, 7)

    assert helper.ops() == ["logout"]
    async with session_maker() as session:
        assert await session.get(CalendarConnection, 7) is None


# --- sync -------------------------------------------------------------------


@pytest.mark.anyio
async def test_sync_replaces_a_changed_series_in_place(session_maker, helper) -> None:
    await _proton_connection(session_maker)
    weekly = [
        helper_event(
            "weekly@nojoin",
            recurrence_id=f"2026120{day}T140000Z",
            starts_at=f"2026-12-0{day}T14:00:00Z",
            ends_at=f"2026-12-0{day}T15:00:00Z",
            recurrence_rule="FREQ=WEEKLY;COUNT=2",
        )
        for day in (1, 8)
    ]
    helper.ok("list_calendars", calendars_result())
    helper.ok("list_events", {"events": weekly, "cursor": "cursor-1"})
    async with session_maker() as session:
        await sync_connection_in_session(session, 7)
        ids = {
            row.provider_event_id: row.id
            for row in (await session.execute(select(CalendarEvent))).scalars()
        }
    assert len(ids) == 2

    renamed = [{**weekly[0], "title": "Weekly sync, renamed", "revision": "p1:2:1"}]
    helper.ok(
        "changes",
        {
            "cursor": "cursor-2",
            "more": False,
            "refresh_required": False,
            "series": [{"uid": "weekly@nojoin", "events": renamed}],
            "deleted_event_ids": [],
        },
    )
    async with session_maker() as session:
        await sync_connection_in_session(session, 7)
        rows = list((await session.execute(select(CalendarEvent))).scalars())
        source = (
            await session.execute(
                select(CalendarSource).where(
                    CalendarSource.provider_calendar_id == "cal-proton"
                )
            )
        ).scalar_one()
        connection = await session.get(CalendarConnection, 7)

    assert [(row.id, row.title) for row in rows] == [
        (ids[weekly[0]["provider_event_id"]], "Weekly sync, renamed")
    ]
    assert source.sync_cursor == "cursor-2"
    assert connection.sync_status == "success"
    # One full fetch: the 25-month window in chunks of at most 93 days.
    assert helper.ops().count("list_events") >= 8


@pytest.mark.anyio
async def test_a_rate_limited_sync_says_so(session_maker, helper) -> None:
    await _proton_connection(session_maker)
    helper.fail("list_calendars", "rate_limited", details={"retry_after_seconds": 30})

    async with session_maker() as session:
        await sync_connection_in_session(session, 7)
        connection = await session.get(CalendarConnection, 7)

    assert connection.sync_status == "error"
    assert connection.sync_error.startswith("Proton asked Nojoin to slow down")


# --- the backend and backups ------------------------------------------------


def test_proton_has_a_writable_backend() -> None:
    backend = write_backend_for("proton")

    assert isinstance(backend, ProtonBackend)
    capabilities = backend.capabilities(None, None)  # type: ignore[arg-type]
    assert capabilities.writable and capabilities.holds == "marker"


def test_the_hold_prefix() -> None:
    assert with_hold_prefix("Design review") == "Hold: Design review"
    assert with_hold_prefix("Hold: Design review") == "Hold: Design review"
    assert without_hold_prefix("Hold: Design review") == "Design review"
    assert without_hold_prefix("Design review") == "Design review"


def test_backups_leave_proton_sign_ins_out() -> None:
    from backend.core.backup.records import (
        _prepare_calendar_connection_for_restore,
        _serialise_calendar_connections,
    )

    proton = CalendarConnection(
        id=1,
        user_id=ALICE,
        provider="proton",
        provider_account_id="proton-user-1",
        access_token_encrypted=encrypt_secret("access-1"),
        refresh_token_encrypted=encrypt_secret("refresh-1"),
        provider_secrets_encrypted=seal_secrets("session-uid", "salted"),
        sync_status="success",
        granted_scopes=[],
    )
    google = CalendarConnection(
        id=2,
        user_id=ALICE,
        provider="google",
        provider_account_id="google-1",
        access_token_encrypted=encrypt_secret("google-access"),
        sync_status="success",
        granted_scopes=[],
    )

    archived_proton, archived_google = _serialise_calendar_connections([proton, google])

    assert "provider_secrets_encrypted" not in archived_proton
    assert archived_proton["access_token"] is None
    assert archived_proton["refresh_token"] is None
    assert archived_proton["sync_status"] == "reauthorisation_required"
    assert "salted" not in json.dumps(archived_proton)
    assert archived_google["access_token"] == "google-access"

    # An older archive that did carry a Proton token still restores without it.
    restored = _prepare_calendar_connection_for_restore(
        {**archived_proton, "access_token": "access-1", "sync_status": "success"}
    )
    assert restored["access_token_encrypted"] is None
    assert restored["sync_status"] == "reauthorisation_required"


@pytest.mark.anyio
async def test_four_helpers_at_most_run_at_once(helper, monkeypatch) -> None:
    helper.answer("version", {"sleep": 0.3, "ok": True, "result": {"protocol": 1}})
    started = time.monotonic()

    await asyncio.gather(*(call_helper("version") for _ in range(8)))

    # Eight calls of 0.3 s, four at a time, take two rounds.
    assert time.monotonic() - started >= 0.55
    assert timedelta(seconds=time.monotonic() - started) < timedelta(seconds=10)


def test_connect_refusal_names_protons_code() -> None:
    """A refusal shows Proton's code, so it is not mistaken for an outage."""
    refused = proton_connect._connect_error(
        HelperError(
            "provider_rejected",
            "Proton refused the request: Operation not allowed",
            provider_code=8004,
            http_status=422,
        )
    )
    assert refused.code == "provider_rejected"
    assert refused.message == "Proton refused the sign-in request. Proton's code: 8004."

    outage = proton_connect._connect_error(HelperError("provider_unavailable", "down"))
    assert (
        outage.message
        == "Nojoin could not connect to Proton. Try again in a few minutes."
    )


@pytest.mark.anyio
async def test_sync_reports_its_progress_and_clears_it(
    session_maker, helper, monkeypatch
) -> None:
    from backend.services.calendar_service import proton_sync

    await _proton_connection(session_maker)
    helper.ok("list_calendars", calendars_result())
    helper.ok(
        "list_events", {"events": [helper_event("planning@nojoin")], "cursor": "c1"}
    )
    seen: list[tuple[int, int]] = []
    save = proton_sync._Progress.save

    async def recording_save(self):
        await save(self)
        seen.append(
            (self.connection.sync_progress_done, self.connection.sync_progress_total)
        )

    monkeypatch.setattr(proton_sync._Progress, "save", recording_save)

    async with session_maker() as session:
        await sync_connection_in_session(session, 7)
        connection = await session.get(CalendarConnection, 7)

    windows = helper.ops().count("list_events")
    assert seen[0] == (0, windows)
    assert seen[-1] == (windows, windows)
    assert [done for done, _ in seen] == list(range(windows + 1))
    assert (connection.sync_progress_done, connection.sync_progress_total) == (
        None,
        None,
    )
    assert connection.sync_status == "success"
    assert connection.last_synced_at is not None


@pytest.mark.anyio
async def test_a_second_sync_of_the_same_account_stands_aside(
    session_maker, helper
) -> None:
    from backend.services.calendar_service.write_audit import (
        advisory_lock,
        sync_lock_key,
    )

    await _proton_connection(session_maker)
    helper.ok("list_calendars", calendars_result())
    held, release = asyncio.Event(), asyncio.Event()

    async def first_sync() -> None:
        async with advisory_lock(sync_lock_key(7), wait=1):
            held.set()
            await release.wait()

    holder = asyncio.create_task(first_sync())
    await held.wait()
    try:
        async with session_maker() as session:
            await sync_connection_in_session(session, 7)
    finally:
        release.set()
        await holder

    assert helper.ops() == []
