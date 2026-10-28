"""Naming a live speaker while a stop is being committed, on PostgreSQL.

The final pass reads the name events once, when it starts. A name that read
the status before the stop committed, and committed after the final pass read
the events, would be lost without a word. The naming route therefore checks
the status under the Transcript fence: a name sent while the stop is
being committed waits for it and is refused. SQLite has no row locks, so this
runs against a real server.

Skips unless NOJOIN_TEST_POSTGRES_URL names a server. CI sets it for the
backend suite.
"""

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import select

from backend.api.v1.endpoints.speakers.routes_live_names import (
    LiveSpeakerNameUpdate,
    name_live_speaker,
)
from backend.core.db import driver_urls
from backend.models.recording import Recording, RecordingStatus
from backend.models.speaker_name_event import SpeakerNameEvent
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.tests.live_naming_fixtures import ONE, seed_live_lines
from backend.tests.postgres_setup import fresh_schema

SCHEMA = "live_speaker_naming_test"
# Long enough for a blocked statement to show as blocked, short enough to
# keep the suite quick. A statement that is not blocked returns in a few ms.
BLOCK_PROBE_SECONDS = 0.5


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def sessions(postgres_test_url: str):
    importlib.import_module("backend.models.registry")
    engine = create_async_engine(
        driver_urls(postgres_test_url)[0],
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    async with engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.run_sync(lambda sync: fresh_schema(sync, SCHEMA))
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def _seed(sessions) -> tuple[int, int, str]:
    async with sessions() as db:
        user = User(username="alice", hashed_password="x")
        db.add(user)
        await db.flush()
        recording = Recording(
            name="Live meeting",
            audio_path="/data/recordings/live.wav",
            user_id=user.id,
            status=RecordingStatus.UPLOADING,
        )
        db.add(recording)
        await db.flush()
        db.add(Transcript(recording_id=recording.id, text=""))
        await db.commit()
        await db.run_sync(lambda sync: seed_live_lines(sync, recording.id))
        return user.id, recording.id, recording.public_id


async def _name(sessions, user_id: int, public_id: str) -> dict:
    async with sessions() as db:
        read = await name_live_speaker(
            recording_id=public_id,
            label=ONE,
            update=LiveSpeakerNameUpdate(name="Alex"),
            db=db,
            current_user=SimpleNamespace(id=user_id),
        )
        return read.live_speaker_names


async def _event_count(sessions) -> int:
    async with sessions() as db:
        return (await db.execute(select(func.count(SpeakerNameEvent.id)))).scalar()


@pytest.mark.anyio
async def test_a_name_sent_during_the_stop_waits_for_it_and_is_refused(sessions):
    user_id, recording_id, public_id = await _seed(sessions)

    async with sessions() as stop_db:
        await stop_db.execute(
            select(Transcript)
            .where(Transcript.recording_id == recording_id)
            .with_for_update()
        )
        recording = await stop_db.get(Recording, recording_id)
        recording.status = RecordingStatus.QUEUED
        await stop_db.flush()

        naming = asyncio.ensure_future(_name(sessions, user_id, public_id))
        await asyncio.sleep(BLOCK_PROBE_SECONDS)
        assert not naming.done(), "the name did not wait for the stop"

        await stop_db.commit()

    with pytest.raises(HTTPException) as refused:
        await naming
    assert refused.value.status_code == 409
    assert await _event_count(sessions) == 0


@pytest.mark.anyio
async def test_a_name_sent_while_capturing_is_recorded(sessions):
    user_id, _, public_id = await _seed(sessions)

    assert await _name(sessions, user_id, public_id) == {ONE: "Alex"}
    assert await _event_count(sessions) == 1
