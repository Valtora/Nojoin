"""Stale delivery figures are re-measured when the Analytics tab is next read.

A transcript edit marks measured delivery stale. Rather than showing stale
figures until the user presses the button, the read that would show them
queues the measurement, unless the user has turned that off.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_user, get_db
from backend.api.v1.endpoints.transcripts import routes_analytics
from backend.api.v1.endpoints.transcripts.router import router
from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.tests.sqlite_schemas import (
    RECORDINGS_SCHEMA,
    TRANSCRIPTS_SCHEMA,
    USERS_SCHEMA,
)
from backend.utils import rate_limit

MEASURED = {"delivery": {"speakers": {}}, "event_watermark": 3}


@pytest.fixture
def api(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    app = FastAPI()
    app.include_router(router, prefix="/transcripts")

    async def override_db():
        async with maker() as session:
            yield session

    state = SimpleNamespace(user=None, dispatched=[])
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: state.user

    # The derivation needs the canonical tables; the behaviour under test
    # starts after it, so it is replaced by a fixed revision.
    monkeypatch.setattr(
        routes_analytics,
        "_backfill_and_compute",
        lambda _session, _recording: (
            5,
            {"speakers": [], "metrics": {}, "attribution_warning": None},
            {},
        ),
    )

    async def dispatch(name, *, args, context):
        state.dispatched.append((name, tuple(args)))
        return SimpleNamespace(id=f"task-{len(state.dispatched)}")

    monkeypatch.setattr(routes_analytics, "dispatch_task_best_effort", dispatch)
    # No Redis in tests: the claim falls back to the per-process window, which
    # is reset here so one test's claim never bleeds into the next.
    rate_limit._fallback_windows.clear()

    async def no_redis():
        return None

    monkeypatch.setattr(rate_limit, "_get_redis", no_redis)

    async def setup(*, settings: dict | None, status: str = "completed", payload=None):
        async with engine.begin() as conn:
            await conn.execute(text(USERS_SCHEMA))
            await conn.execute(text(RECORDINGS_SCHEMA))
            await conn.execute(text(TRANSCRIPTS_SCHEMA))
        state.user = User(
            id=1,
            username="alice",
            hashed_password="x",
            role="user",
            settings=settings or {},
        )
        async with maker() as session:
            session.add(state.user)
            recording = Recording(
                id=7,
                name="Standup",
                audio_path="/tmp/standup.wav",
                status="PROCESSED",
                user_id=1,
                upload_progress=100,
                processing_progress=100,
            )
            session.add(recording)
            session.add(
                Transcript(
                    recording_id=7,
                    transcript_status="completed",
                    notes_status="pending",
                    analytics_status=status,
                    analytics_payload=MEASURED if payload is None else payload,
                )
            )
            await session.commit()
            await session.refresh(recording)
            return recording

    async def read(recording):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            response = await client.get(f"/transcripts/{recording.public_id}/analytics")
        assert response.status_code == 200, response.text
        return response.json()

    yield SimpleNamespace(setup=setup, read=read, state=state)
    app.dependency_overrides.clear()


def test_a_stale_measurement_is_queued_on_read_and_reported_generating(api):
    async def run():
        recording = await api.setup(settings=None)
        return await api.read(recording)

    body = asyncio.run(run())

    assert body["delivery_stale"] is True
    assert body["delivery_status"] == "generating"
    assert api.state.dispatched == [
        ("backend.worker.tasks.compute_delivery_analytics_task", (7,))
    ]


def test_polling_does_not_queue_the_same_run_again(api):
    async def run():
        recording = await api.setup(settings=None)
        first = await api.read(recording)
        second = await api.read(recording)
        return first, second

    first, second = asyncio.run(run())

    assert first["delivery_status"] == "generating"
    # The claim holds; the stored status is reported until the worker writes.
    assert second["delivery_status"] == "completed"
    assert len(api.state.dispatched) == 1


def test_a_user_who_turned_it_off_keeps_the_stale_figures(api):
    async def run():
        recording = await api.setup(settings={"auto_remeasure_delivery": False})
        return await api.read(recording)

    body = asyncio.run(run())

    assert body["delivery_stale"] is True
    assert body["delivery_status"] == "completed"
    assert api.state.dispatched == []


def test_current_figures_are_left_alone(api):
    async def run():
        recording = await api.setup(
            settings=None, payload={"delivery": {}, "event_watermark": 5}
        )
        return await api.read(recording)

    body = asyncio.run(run())

    assert body["delivery_stale"] is False
    assert api.state.dispatched == []


@pytest.mark.parametrize("status", ["generating", "error"])
def test_a_run_in_progress_or_a_failed_one_is_not_queued_again(api, status):
    async def run():
        recording = await api.setup(settings=None, status=status)
        return await api.read(recording)

    body = asyncio.run(run())

    assert body["delivery_status"] == status
    assert api.state.dispatched == []


def test_never_measured_recordings_are_not_measured_by_a_read(api):
    async def run():
        recording = await api.setup(settings=None, status="pending", payload={})
        return await api.read(recording)

    body = asyncio.run(run())

    assert body["delivery_stale"] is False
    assert api.state.dispatched == []


def test_claim_once_admits_the_first_caller_per_window(monkeypatch):
    async def no_redis():
        return None

    monkeypatch.setattr(rate_limit, "_get_redis", no_redis)
    rate_limit._fallback_windows.clear()

    async def run():
        return [await rate_limit.claim_once("k", 60) for _ in range(3)]

    assert asyncio.run(run()) == [True, False, False]
