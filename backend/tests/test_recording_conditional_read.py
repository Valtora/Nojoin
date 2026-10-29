"""A finished recording's page polls the recording read with If-None-Match.

The ETag covers the status and the transcript change counter, so the poll is a
304 until one of them moves, and then a full read.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.tests.sqlite_schemas import (
    DOCUMENTS_SCHEMA,
    RECORDING_AUDIO_CHUNK_CURSORS_SCHEMA,
    TIMELINE_NOTES_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
)
from backend.tests.test_recording_pause_resume import (
    RECORDING_SPEAKERS_SCHEMA,
    RECORDING_TAGS_SCHEMA,
    RECORDINGS_SCHEMA,
    TAGS_SCHEMA,
    TRANSCRIPTS_SCHEMA,
)
from backend.utils.transcript_change import change_sequence_bump

URL = "/api/v1/recordings/rec-1"


@pytest.fixture
async def session_maker() -> AsyncIterator[sessionmaker]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        for schema in (
            RECORDINGS_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            RECORDING_TAGS_SCHEMA,
            TAGS_SCHEMA,
            TIMELINE_NOTES_SCHEMA,
            DOCUMENTS_SCHEMA,
            RECORDING_AUDIO_CHUNK_CURSORS_SCHEMA,
            TRANSCRIPT_UTTERANCES_SCHEMA,
        ):
            await connection.execute(text(schema))
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        session.add(
            Recording(
                id=1,
                public_id="rec-1",
                meeting_uid="meeting-1",
                name="Weekly sync",
                audio_path="meeting.webm",
                status=RecordingStatus.PROCESSED,
                user_id=1,
            )
        )
        session.add(
            Transcript(
                recording_id=1,
                segments=[],
                notes_status="pending",
                transcript_status="completed",
            )
        )
        await session.commit()
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest.fixture
async def client(session_maker: sessionmaker) -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as async_client:
        yield async_client


@pytest.mark.anyio
async def test_the_read_is_a_304_until_the_transcript_changes(
    session_maker: sessionmaker, client: AsyncClient
) -> None:
    first = await client.get(URL)
    etag = first.headers["etag"]
    unchanged = await client.get(URL, headers={"If-None-Match": etag})
    async with session_maker() as session:
        await session.execute(change_sequence_bump(1))
        await session.commit()
    changed = await client.get(URL, headers={"If-None-Match": etag})

    assert first.status_code == 200
    # Browsers must not revalidate ordinary reads with this narrow validator.
    assert first.headers["cache-control"] == "no-store"
    assert unchanged.status_code == 304
    assert changed.status_code == 200
    assert changed.json()["transcript"]["change_sequence"] == 1
    assert changed.headers["etag"] != etag


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["recording", "notes"])
async def test_a_status_change_ends_the_304s(
    session_maker: sessionmaker, client: AsyncClient, change: str
) -> None:
    etag = (await client.get(URL)).headers["etag"]
    async with session_maker() as session:
        if change == "recording":
            recording = await session.get(Recording, 1)
            assert recording is not None
            recording.status = RecordingStatus.PROCESSING
        else:
            # Notes generation started from another tab: the page then polls.
            transcript = await session.get(Transcript, 1)
            assert transcript is not None
            transcript.notes_status = "generating"
        await session.commit()

    response = await client.get(URL, headers={"If-None-Match": etag})

    assert response.status_code == 200
    assert response.headers["etag"] != etag


@pytest.mark.anyio
async def test_a_notes_edit_elsewhere_ends_the_304s(client: AsyncClient) -> None:
    etag = (await client.get(URL)).headers["etag"]

    write = await client.put(
        "/api/v1/transcripts/rec-1/notes", json={"notes": "Edited elsewhere"}
    )
    response = await client.get(URL, headers={"If-None-Match": etag})

    assert write.status_code == 200
    assert response.status_code == 200
    assert response.json()["transcript"]["notes"] == "Edited elsewhere"
