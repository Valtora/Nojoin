"""Real FastAPI dependency teardown before SSE bodies and error responses."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api import deps
from backend.api.v1.endpoints.transcripts import routes_events
from backend.services import transcript_events as events
from backend.tests.test_transcript_events_publication import (
    events_engine as events_engine,
)
from backend.tests.test_transcript_events_route import RouteContext
from backend.tests.test_transcript_events_route import (
    route_context as route_context,
)
from backend.tests.test_transcript_events_route import (
    stream_client as stream_client,
)
from backend.tests.transcript_events_fakes import FakeStreamClient

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class SessionLifetimes:
    active: set[object] = field(default_factory=set)
    entered: set[object] = field(default_factory=set)
    finalised: set[object] = field(default_factory=set)


def application(
    context: RouteContext, monkeypatch: pytest.MonkeyPatch
) -> tuple[FastAPI, SessionLifetimes]:
    lifetimes = SessionLifetimes()

    async def sessions() -> AsyncIterator[AsyncSession]:
        marker = object()
        lifetimes.entered.add(marker)
        lifetimes.active.add(marker)
        try:
            yield context.db
        finally:
            await context.db.close()
            lifetimes.active.remove(marker)
            lifetimes.finalised.add(marker)

    async def authenticate(*args: Any, **kwargs: Any) -> Any:
        return context.user

    monkeypatch.setattr(deps, "get_session", sessions)
    monkeypatch.setattr(routes_events, "get_session", sessions, raising=False)
    monkeypatch.setattr(deps, "get_authenticated_user_from_token", authenticate)
    app = FastAPI()
    app.include_router(routes_events.router, prefix="/api/v1/transcripts")
    return app, lifetimes


@pytest.mark.parametrize("wrong_owner", [False, True])
async def test_all_database_dependencies_finalise_before_first_response_body(
    route_context: RouteContext,
    stream_client: FakeStreamClient,
    monkeypatch: pytest.MonkeyPatch,
    wrong_owner: bool,
) -> None:
    if wrong_owner:
        route_context.user.id = 8
    app, lifetimes = application(route_context, monkeypatch)
    first_body = asyncio.Event()
    statuses: list[int] = []
    observed: list[tuple[set[object], set[object], set[object], bool, bytes]] = []

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            statuses.append(message["status"])
        if message["type"] == "http.response.body" and message.get("body"):
            observed.append(
                (
                    lifetimes.active.copy(),
                    lifetimes.entered.copy(),
                    lifetimes.finalised.copy(),
                    route_context.db.in_transaction(),
                    message["body"],
                )
            )
            first_body.set()

    async def receive() -> dict[str, Any]:
        await first_body.wait()
        return {"type": "http.disconnect"}

    async with asyncio.timeout(2):
        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": f"/api/v1/transcripts/{route_context.public_id}/events",
                "query_string": b"",
                "root_path": "",
                "headers": [(b"authorization", b"Bearer probe")],
                "client": ("127.0.0.1", 1234),
                "server": ("127.0.0.1", 80),
            },
            receive,
            send,
        )

    assert statuses == [404 if wrong_owner else 200]
    assert observed
    active, entered, finalised, in_transaction, body = observed[0]
    assert entered
    assert not active, "A database dependency survived into the response body"
    assert finalised == entered
    assert not in_transaction
    if wrong_owner:
        assert b"Recording not found" in body
        assert stream_client.subscription.subscribed is None
    else:
        assert body == events.sequence_frame(1).encode()
        assert stream_client.subscription.acknowledged
        assert stream_client.subscription.unsubscribed
        assert stream_client.subscription.closed
        assert stream_client.closed
