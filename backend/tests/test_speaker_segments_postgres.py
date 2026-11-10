"""The voiceprint sample lines follow the People meeting count rule."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import Session

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.core.db import driver_urls
from backend.models.recording import Recording, RecordingStatus
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.tests.postgres_setup import fresh_schema

SCHEMA = "speaker_segments_test"
JANUARY = datetime(2026, 1, 1, 12)
# Later than every counted meeting, so the newest-first scan reaches it first.
LATER = datetime(2030, 1, 1, 12)


@dataclass
class SamplesDatabase:
    engine: Engine
    sessions: async_sessionmaker[AsyncSession]
    owner: User
    other: User
    person: GlobalSpeaker


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def samples(postgres_test_url: str) -> AsyncIterator[SamplesDatabase]:
    async_url, sync_url = driver_urls(postgres_test_url)
    engine = create_engine(
        sync_url, connect_args={"options": f"-csearch_path={SCHEMA},public"}
    )
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    fresh_schema(engine, SCHEMA)
    with Session(engine, expire_on_commit=False) as session:
        owner = User(username="samples-owner", hashed_password="x")
        other = User(username="samples-other", hashed_password="x")
        session.add_all([owner, other])
        session.flush()
        person = GlobalSpeaker(name="Alice Example", user_id=owner.id)
        session.add(person)
        session.commit()
    async_engine = create_async_engine(
        async_url, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )
    try:
        yield SamplesDatabase(
            engine,
            async_sessionmaker(async_engine, expire_on_commit=False),
            owner,
            other,
            person,
        )
    finally:
        await async_engine.dispose()
        engine.dispose()


@pytest.fixture
async def client(samples: SamplesDatabase) -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def database() -> AsyncIterator[AsyncSession]:
        async with samples.sessions() as session:
            yield session

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: samples.owner
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


def _meeting(
    session: Session, owner: User, name: str, created_at: datetime = JANUARY
) -> Recording:
    """A processed meeting whose transcript has two short lines by SPEAKER_00."""
    recording = Recording(
        name=name,
        audio_path=f"/tmp/samples-{name}.wav",
        user_id=owner.id,
        status=RecordingStatus.PROCESSED,
        created_at=created_at,
    )
    session.add(recording)
    session.flush()
    session.add(
        Transcript(
            recording_id=recording.id,
            segments=[
                {"start": 0, "end": 4, "speaker": "SPEAKER_00", "text": "Morning."},
                {"start": 5, "end": 9, "speaker": "SPEAKER_00", "text": "Agreed."},
            ],
        )
    )
    return recording


def _link(
    session: Session, recording: Recording, person: GlobalSpeaker
) -> RecordingSpeaker:
    speaker = RecordingSpeaker(
        recording_id=recording.id,
        global_speaker_id=person.id,
        diarization_label="SPEAKER_00",
        name=person.name,
    )
    session.add(speaker)
    session.flush()
    return speaker


async def _sample_meetings(client: AsyncClient, person: GlobalSpeaker) -> list[str]:
    """The meeting of each sample line offered for recalibrating this person."""
    response = await client.get(f"/api/v1/speakers/{person.id}/segments")
    assert response.status_code == 200, response.text
    return [segment["recording_id"] for segment in response.json()]


@pytest.mark.anyio
async def test_samples_come_only_from_the_users_own_recordings(
    samples: SamplesDatabase, client: AsyncClient
) -> None:
    with Session(samples.engine) as session:
        owned = _meeting(session, samples.owner, "owned")
        _link(session, owned, samples.person)
        other_users = _meeting(session, samples.other, "other-users", LATER)
        _link(session, other_users, samples.person)
        expected = [owned.public_id] * 2
        session.commit()
    assert await _sample_meetings(client, samples.person) == expected


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("merged_into", "status"),
    [(True, "active"), (False, "merged"), (False, "inactive"), (False, "unknown")],
)
async def test_samples_follow_the_meeting_count_rule(
    samples: SamplesDatabase,
    client: AsyncClient,
    merged_into: bool,
    status: str,
) -> None:
    with Session(samples.engine) as session:
        counted = _meeting(session, samples.owner, "counted")
        _link(session, counted, samples.person)
        uncounted = _meeting(session, samples.owner, "uncounted", LATER)
        speaker = _link(session, uncounted, samples.person)
        if merged_into:
            target = RecordingSpeaker(
                recording_id=uncounted.id,
                diarization_label="SPEAKER_01",
                name="Ben Sample",
            )
            session.add(target)
            session.flush()
            speaker.merged_into_id = target.id
        speaker.speaker_status = status
        session.add(speaker)
        expected = [counted.public_id] * 2
        session.commit()
    assert await _sample_meetings(client, samples.person) == expected
