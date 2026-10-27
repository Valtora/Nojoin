"""Endpoint ownership, startup ordering and actual ASGI disconnect lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from starlette.requests import ClientDisconnect

from backend.api.v1.endpoints.transcripts import routes_events
from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.services import transcript_events as events
from backend.services import transcript_events_publish as publisher
from backend.tests.test_transcript_events_publication import (
    events_engine as events_engine,
)
from backend.tests.test_transcript_events_publish import AsyncPublisher
from backend.tests.transcript_events_fakes import FakeStreamClient
from backend.utils import transcript_publication

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class TrackingSession(AsyncSession):
    close_count: int = 0
    before_sequence_read: Callable[[], None] | None = None

    async def close(self) -> None:
        self.close_count += 1
        await super().close()

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        if (
            "SELECT transcripts.change_sequence" in str(statement)
            and self.before_sequence_read
        ):
            self.before_sequence_read()
        return await super().execute(statement, *args, **kwargs)


@dataclass
class RouteContext:
    db: TrackingSession
    recording_id: int
    public_id: str
    user: User


@pytest.fixture
async def route_context(events_engine: Engine) -> AsyncIterator[RouteContext]:
    engine = create_async_engine(
        str(events_engine.url).replace("sqlite:", "sqlite+aiosqlite:")
    )
    async with TrackingSession(engine, expire_on_commit=False) as db:
        recording = Recording(
            name="Capture",
            audio_path="/tmp/events.wav",
            status=RecordingStatus.UPLOADING,
            user_id=7,
        )
        db.add(recording)
        await db.flush()
        db.add(Transcript(recording_id=recording.id, text="", segments=[]))
        await db.commit()
        db.close_count = 0
        yield RouteContext(
            db,
            recording.id,
            recording.public_id,
            User(id=7, username="owner", hashed_password="unused"),
        )
    await engine.dispose()


@pytest.fixture
def stream_client(monkeypatch: pytest.MonkeyPatch) -> FakeStreamClient:
    client = FakeStreamClient()
    monkeypatch.setattr(events.aioredis, "from_url", lambda *args, **kwargs: client)
    return client


async def test_auth_db_closed_before_subscription_and_sequence_read_after_ack(
    route_context: RouteContext, stream_client: FakeStreamClient, events_engine: Engine
) -> None:
    db = route_context.db
    original_subscribe = stream_client.subscription.subscribe

    async def subscribe(channel: str) -> None:
        assert db.close_count == 1
        assert not db.in_transaction()
        await original_subscribe(channel)
        # A commit between authentication and the subscription acknowledgement
        # must appear in the initial hint, without retaining the old DB read.
        with events_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE transcripts SET change_sequence=42 WHERE recording_id=:id"
                ),
                {"id": route_context.recording_id},
            )

    def before_read() -> None:
        assert stream_client.subscription.acknowledged

    stream_client.subscription.subscribe = subscribe
    db.before_sequence_read = before_read
    response = await routes_events.stream_transcript_events(
        route_context.public_id, db, route_context.user
    )
    assert db.close_count == 2
    assert not db.in_transaction()
    assert await anext(response.body_iterator) == events.sequence_frame(42)
    stream_client.subscription.hint(43)
    assert await anext(response.body_iterator) == events.sequence_frame(43)
    assert db.close_count == 2
    assert not db.in_transaction()
    assert response.headers["cache-control"] == "no-cache, no-store"
    assert response.headers["x-accel-buffering"] == "no"
    await response.body_iterator.aclose()


async def test_missing_credentials_and_wrong_owner_do_not_subscribe(
    route_context: RouteContext, stream_client: FakeStreamClient
) -> None:
    app = FastAPI()
    app.include_router(routes_events.router, prefix="/api/v1/transcripts")

    async def database() -> AsyncIterator[AsyncSession]:
        yield route_context.db

    async def other_user() -> User:
        return User(id=8, username="other", hashed_password="unused")

    app.dependency_overrides[routes_events.get_stream_db] = database
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/api/v1/transcripts/{route_context.public_id}/events"
        )
        assert response.status_code == 401
        app.dependency_overrides[routes_events.get_stream_current_user] = other_user
        response = await client.get(
            f"/api/v1/transcripts/{route_context.public_id}/events"
        )
        assert response.status_code == 404
    assert stream_client.subscription.subscribed is None


async def test_missing_transcript_closes_redis_and_db(
    route_context: RouteContext, stream_client: FakeStreamClient
) -> None:
    await route_context.db.execute(text("DELETE FROM transcripts"))
    await route_context.db.commit()
    with pytest.raises(HTTPException) as failure:
        await routes_events.stream_transcript_events(
            route_context.public_id, route_context.db, route_context.user
        )
    assert failure.value.status_code == 404
    assert not route_context.db.in_transaction()
    assert stream_client.subscription.unsubscribed
    assert stream_client.closed


async def test_redis_startup_failure_returns_503_with_auth_db_released(
    route_context: RouteContext,
    stream_client: FakeStreamClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream_client.subscription.auto_ack = False
    monkeypatch.setattr(events, "SUBSCRIBE_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(HTTPException) as failure:
        await routes_events.stream_transcript_events(
            route_context.public_id, route_context.db, route_context.user
        )
    assert failure.value.status_code == 503
    assert route_context.db.close_count == 1
    assert not route_context.db.in_transaction()
    assert stream_client.closed
    assert stream_client.subscription.closed


async def test_initial_read_failure_closes_subscription_before_response(
    route_context: RouteContext, stream_client: FakeStreamClient
) -> None:
    def failed_read() -> None:
        raise RuntimeError("Initial sequence read failed")

    route_context.db.before_sequence_read = failed_read
    with pytest.raises(RuntimeError, match="Initial sequence read failed"):
        await routes_events.stream_transcript_events(
            route_context.public_id, route_context.db, route_context.user
        )
    assert route_context.db.close_count == 2
    assert not route_context.db.in_transaction()
    assert stream_client.closed
    assert stream_client.subscription.closed


@pytest.mark.parametrize("fail_on", ["http.response.start", "http.response.body"])
async def test_actual_asgi_send_failure_always_closes_redis(
    route_context: RouteContext, stream_client: FakeStreamClient, fail_on: str
) -> None:
    response = await routes_events.stream_transcript_events(
        route_context.public_id, route_context.db, route_context.user
    )

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == fail_on:
            raise OSError("Browser disconnected")

    async def receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    with pytest.raises(ClientDisconnect):
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    assert stream_client.subscription.unsubscribed
    assert stream_client.subscription.closed
    assert stream_client.closed
    assert not route_context.db.in_transaction()


async def test_actual_asgi_disconnect_cancels_wait_and_closes_redis(
    route_context: RouteContext, stream_client: FakeStreamClient
) -> None:
    response = await routes_events.stream_transcript_events(
        route_context.public_id, route_context.db, route_context.user
    )
    initial_frame = asyncio.Event()

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body":
            initial_frame.set()

    async def receive() -> dict[str, Any]:
        await initial_frame.wait()
        return {"type": "http.disconnect"}

    await response({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send)
    assert stream_client.subscription.unsubscribed
    assert stream_client.subscription.closed
    assert stream_client.closed
    assert not route_context.db.in_transaction()


async def test_actual_async_session_commit_publishes_without_blocking(
    route_context: RouteContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = AsyncPublisher()
    monkeypatch.setattr(publisher.aioredis, "from_url", lambda *args, **kwargs: client)
    monkeypatch.setattr(
        transcript_publication,
        "publish_transcript_changes",
        publisher.publish_transcript_changes,
    )
    transcript = await route_context.db.get(Transcript, 1)
    assert transcript is not None
    transcript.text = "Changed via async commit"
    commit = asyncio.create_task(route_context.db.commit())
    # Wait until the transport has started; the DB flush can yield first.
    async with asyncio.timeout(1):
        while not client.arguments:
            if commit.done():
                await commit
                pytest.fail("Async commit did not publish a transcript hint")
            await asyncio.sleep(0)
    assert not commit.done()
    await commit
    assert client.arguments[2:] == (
        publisher.transcript_channel(route_context.recording_id),
        '{"sequence": 2}',
    )
    assert client.closed
