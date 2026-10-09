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


async def _stream(
    monkeypatch, recording: SimpleNamespace, headers: dict[str, str] | None = None
):
    async def owned(db, recording_id, user_id):
        return recording

    monkeypatch.setattr(routes_query, "_get_owned_recording", owned)
    request = SimpleNamespace(headers=headers or {})
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


# --- What the stream sends -----------------------------------------------------
#
# Larger than the 2.5 MB chunk a range request is answered with, like the proxy
# of any recording over about 2 min 40 s.
PROXY_SIZE = 3_000_000


def _proxy_bytes() -> bytes:
    return (bytes(range(251)) * (PROXY_SIZE // 251 + 1))[:PROXY_SIZE]


def _recording_with_proxy(tmp_path: Path, content: bytes) -> SimpleNamespace:
    recording = _recording(tmp_path, audio=b"webm", proxy=True)
    Path(recording.proxy_path).write_bytes(content)
    return recording


@pytest.mark.anyio
async def test_stream_sends_the_whole_file_with_200_when_no_range_is_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The audio export asks without Range. A 206 carrying the first chunk saved
    # only the first 2 min 40 s of a longer recording.
    content = _proxy_bytes()

    response = await _stream(monkeypatch, _recording_with_proxy(tmp_path, content))
    chunks = [chunk async for chunk in response.body_iterator]

    assert response.status_code == 200
    assert "content-range" not in response.headers
    assert response.headers["content-length"] == str(PROXY_SIZE)
    assert b"".join(chunks) == content
    # Read a bounded piece at a time, never the whole file at once.
    assert max(len(chunk) for chunk in chunks) <= 64 * 1024


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("range_header", "start", "end"),
    [
        ("bytes=0-", 0, 2_559_999),
        ("bytes=2900000-", 2_900_000, PROXY_SIZE - 1),
        ("bytes=-1000", PROXY_SIZE - 1000, PROXY_SIZE - 1),
    ],
)
async def test_stream_still_answers_a_range_with_one_chunk_and_206(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    range_header: str,
    start: int,
    end: int,
) -> None:
    content = _proxy_bytes()

    response = await _stream(
        monkeypatch,
        _recording_with_proxy(tmp_path, content),
        headers={"range": range_header},
    )
    body = b"".join([chunk async for chunk in response.body_iterator])

    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes {start}-{end}/{PROXY_SIZE}"
    assert body == content[start : end + 1]


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
