"""Whether a recording has anything playable, and what the player is told.

A recording restored from a backup taken without audio (or whose audio was lost)
has neither its file nor a playback proxy. The stream endpoint used to answer 202
"Audio proxy is being prepared" for it forever, and the page polled for a proxy
nothing would ever make.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.api.v1.endpoints.recordings import helpers, routes_query
from backend.models.recording import Recording, RecordingStatus
from backend.tests.test_recording_pause_resume import (
    RECORDING_SPEAKERS_SCHEMA,
    RECORDING_TAGS_SCHEMA,
    RECORDINGS_SCHEMA,
    TAGS_SCHEMA,
    TRANSCRIPTS_SCHEMA,
)


def _recording(tmp_path: Path, *, audio: bytes | None, proxy: bool) -> SimpleNamespace:
    audio_path = tmp_path / "meeting.webm"
    proxy_path = tmp_path / "meeting.mp3"
    if audio is not None:
        audio_path.write_bytes(audio)
    if proxy:
        proxy_path.write_bytes(b"mp3")
    return SimpleNamespace(
        id=1,
        user_id=1,
        audio_path=str(audio_path),
        proxy_path=str(proxy_path) if proxy else None,
    )


async def _stream(monkeypatch, recording: SimpleNamespace):
    async def owned(db, recording_id, user_id):
        return recording

    monkeypatch.setattr(routes_query, "_get_owned_recording", owned)
    request = SimpleNamespace(headers={})
    return await routes_query.stream_recording(
        "rec-1", request, db=None, current_user=SimpleNamespace(id=1)
    )


@pytest.mark.anyio
async def test_stream_says_the_audio_is_gone_when_nothing_can_make_a_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(HTTPException) as raised:
        await _stream(monkeypatch, _recording(tmp_path, audio=None, proxy=False))

    assert raised.value.status_code == 404
    assert "not available" in raised.value.detail


@pytest.mark.anyio
async def test_stream_still_asks_to_wait_while_a_proxy_can_be_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(HTTPException) as raised:
        await _stream(monkeypatch, _recording(tmp_path, audio=b"webm", proxy=False))

    assert raised.value.status_code == 202


@pytest.mark.parametrize(
    ("audio", "proxy", "has_audio"),
    [
        (None, False, False),
        (b"webm", False, True),
        (None, True, True),
        # An empty master, left by an interrupted write, cannot make a proxy.
        (b"", False, False),
    ],
)
def test_has_audio_reports_whether_anything_playable_exists(
    tmp_path: Path, audio: bytes | None, proxy: bool, has_audio: bool
) -> None:
    recording = _recording(tmp_path, audio=audio, proxy=proxy)

    assert helpers._recording_has_audio(recording) is has_audio


# --- The read endpoints carry the flag -----------------------------------------
#
# The model defaults has_audio to None ("not checked"), so a read that stops
# computing it would quietly tell the player nothing and fall back to waiting.


@pytest.fixture
async def session_maker() -> AsyncIterator[sessionmaker]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Only what the two reads load, in the schema the pause/resume API tests
    # already keep for SQLite (the models' JSONB columns have no SQLite form).
    async with engine.begin() as connection:
        for schema in (
            RECORDINGS_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            RECORDING_TAGS_SCHEMA,
            TAGS_SCHEMA,
        ):
            await connection.execute(text(schema))
    try:
        yield sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
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
@pytest.mark.parametrize("audio_on_disk", [True, False])
async def test_list_and_detail_reads_say_whether_the_audio_exists(
    tmp_path: Path,
    session_maker: sessionmaker,
    client: AsyncClient,
    audio_on_disk: bool,
) -> None:
    audio_path = tmp_path / "meeting.webm"
    if audio_on_disk:
        audio_path.write_bytes(b"webm")
    async with session_maker() as session:
        session.add(
            Recording(
                id=1,
                public_id="rec-1",
                meeting_uid="meeting-1",
                name="Weekly sync",
                audio_path=str(audio_path),
                status=RecordingStatus.PROCESSED,
                user_id=1,
            )
        )
        await session.commit()

    detail = await client.get("/api/v1/recordings/rec-1")
    listed = await client.get("/api/v1/recordings/")

    assert detail.status_code == 200
    assert detail.json()["has_audio"] is audio_on_disk
    assert listed.status_code == 200
    assert [item["has_audio"] for item in listed.json()] == [audio_on_disk]
