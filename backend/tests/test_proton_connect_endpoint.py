"""The Proton connect endpoints, driven over HTTP with an async session.

The finishing step answers with the saved connection and its calendars. It
used to read them from an instance that had never loaded them, a lazy load
an async session cannot run, so a sign-in that had worked answered 500.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker

from backend.api.deps import get_current_user, get_db
from backend.api.v1.endpoints import calendar as calendar_endpoints
from backend.models.user import User
from backend.services.calendar_service import proton_connect
from backend.tests.calendar_tool_support import ALICE, sqlite_engine
from backend.tests.proton_fake_helper import (
    calendars_result,
    helper_event,
    install_fake_helper,
)
from backend.tests.sqlite_schemas import CALENDAR_PUSH_CHANNELS_SCHEMA

SESSION = {
    "uid": "session-uid",
    "access_token": "access-1",
    "refresh_token": "refresh-1",
    "expires_in": 86400,
    "scopes": [],
}
UNLOCKED = {
    "session": {
        "uid": "session-uid",
        "access_token": "access-1",
        "key_passphrase": "salted",
    },
    "user": {"id": "proton-user-1", "name": "alice", "email": "alice@example.com"},
    **calendars_result(),
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    built = await sqlite_engine((CALENDAR_PUSH_CHANNELS_SCHEMA,))
    yield built
    await built.dispose()


@pytest.fixture
async def client(
    engine: AsyncEngine, tmp_path, monkeypatch
) -> AsyncIterator[AsyncClient]:
    import backend.core.db as core_db

    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(core_db, "async_session_maker", maker)
    store: dict = {}

    async def save(state, payload):
        store[state] = payload

    async def pop(state):
        return store.pop(state, None)

    async def no_limit(*args, **kwargs):
        return None

    async def no_dispatch(*args, **kwargs):
        return None

    monkeypatch.setattr(proton_connect, "_save_oauth_state", save)
    monkeypatch.setattr(proton_connect, "_pop_oauth_state", pop)
    monkeypatch.setattr(calendar_endpoints, "enforce_rate_limit", no_limit)
    monkeypatch.setattr(
        proton_connect, "dispatch_task_best_effort", no_dispatch, raising=False
    )
    helper = install_fake_helper(tmp_path, monkeypatch)
    helper.ok(
        "login_start",
        {"session": SESSION, "password_mode": 1, "next": "two_factor"},
    )
    helper.ok("login_2fa", {"session": SESSION, "next": "unlock"})
    helper.ok("login_unlock", UNLOCKED)
    helper.ok("list_calendars", calendars_result())
    helper.ok(
        "list_events", {"events": [helper_event("planning@nojoin")], "cursor": "c1"}
    )

    app = FastAPI()
    app.include_router(calendar_endpoints.router, prefix="/api/v1/calendar")

    async def override_db():
        async with maker() as session:
            yield session

    async with maker() as session:
        alice = await session.get(User, ALICE)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: alice
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


@pytest.mark.anyio
async def test_the_finishing_step_answers_with_the_connection(client) -> None:
    credentials = {"username": "alice@example.com", "password": "correct horse"}
    started = await client.post("/api/v1/calendar/proton/connect", json=credentials)
    assert started.status_code == 200, started.text
    assert started.json()["step"] == "two_factor"

    finished = await client.post(
        f"/api/v1/calendar/proton/connect/{started.json()['flow_id']}/totp",
        json={**credentials, "code": "123456"},
    )

    assert finished.status_code == 200, finished.text
    body = finished.json()
    assert body["step"] == "done"
    connection = body["connection"]
    assert connection["provider"] == "proton"
    assert connection["email"] == "alice@example.com"
    assert {c["provider_calendar_id"] for c in connection["calendars"]} == {
        "cal-proton",
        "cal-holidays",
    }
