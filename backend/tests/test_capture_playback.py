"""Capture playback reads a pinned PCM prefix, with real ownership and storage."""

from __future__ import annotations

import io
import struct
import wave
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_user, get_current_user_stream, get_db
from backend.api.v1.api import api_router
from backend.models.recording import ClientStatus, Recording, RecordingStatus
from backend.tests.sqlite_schemas import (
    DOCUMENTS_SCHEMA,
    RECORDING_AUDIO_CHUNK_CURSORS_SCHEMA,
    RECORDING_AUDIO_CHUNKS_SCHEMA,
    TIMELINE_NOTES_SCHEMA,
)
from backend.tests.test_recording_pause_resume import (
    RECORDING_AUDIO_WINDOW_MANIFESTS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDING_TAGS_SCHEMA,
    RECORDINGS_SCHEMA,
    TAGS_SCHEMA,
    TRANSCRIPTS_SCHEMA,
)
from backend.utils import capture_playback
from backend.utils.recording_audio_registration import (
    ChunkStream,
    register_recording_audio_chunk,
)

URL = "/api/v1/recordings/rec-1/capture-stream"


@pytest.fixture
async def session_maker() -> AsyncIterator[sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        for schema in (
            RECORDINGS_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            RECORDING_AUDIO_CHUNKS_SCHEMA,
            RECORDING_AUDIO_CHUNK_CURSORS_SCHEMA,
            RECORDING_AUDIO_WINDOW_MANIFESTS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            RECORDING_TAGS_SCHEMA,
            TAGS_SCHEMA,
            TIMELINE_NOTES_SCHEMA,
            DOCUMENTS_SCHEMA,
        ):
            await connection.execute(text(schema))
    maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        session.add(
            Recording(
                id=1,
                public_id="rec-1",
                meeting_uid="meeting-1",
                name="Meeting",
                audio_path="missing.wav",
                user_id=1,
                status=RecordingStatus.UPLOADING,
                client_status=ClientStatus.RECORDING,
            )
        )
        await session.commit()
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest.fixture
def app(session_maker: sessionmaker) -> FastAPI:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def database() -> AsyncIterator[AsyncSession]:
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    app.dependency_overrides[get_current_user_stream] = lambda: SimpleNamespace(id=1)
    return app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


async def append_chunk(
    maker: sessionmaker,
    directory: Path,
    sequence: int,
    pcm: bytes,
    *,
    legacy_header: bool = False,
) -> Path:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setparams((2, 2, 16_000, 0, "NONE", "not compressed"))
        wav.writeframes(pcm)
    data = buffer.getvalue()
    if legacy_header:
        # A valid extra RIFF chunk, like ffmpeg's LIST metadata, precedes PCM.
        data = data[:12] + b"JUNK\x04\x00\x00\x00test" + data[12:]
        data = data[:4] + struct.pack("<I", len(data) - 8) + data[8:]
    path = directory / f"{sequence}.wav"
    path.write_bytes(data)
    async with maker() as session:
        await session.run_sync(
            lambda sync: register_recording_audio_chunk(
                sync,
                ChunkStream(1, "browser", directory),
                sequence=sequence,
                storage_path=path,
            )
        )
        await session.commit()
    return path


def expected_wav(pcm: bytes) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setparams((2, 2, 16_000, 0, "NONE", "not compressed"))
        wav.writeframes(pcm)
    return buffer.getvalue()


@pytest.mark.anyio
async def test_stream_joins_exact_samples(
    client: AsyncClient,
    session_maker: sessionmaker,
    tmp_path: Path,
) -> None:
    first, second = bytes(range(68)), bytes(reversed(range(124)))
    await append_chunk(session_maker, tmp_path, 0, first)
    await append_chunk(session_maker, tmp_path, 1, second)
    response = await client.get(URL, params={"sequence": 1})
    assert response.status_code == 200
    assert response.content == expected_wav(first + second)
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["accept-ranges"] == "bytes"
    assert int(response.headers["content-length"]) == len(response.content)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "header,expected_range",
    [
        ("bytes=0-0", (0, 0)),
        ("bytes=0-10", (0, 10)),
        ("bytes=40-50", (40, 50)),
        ("bytes=105-120", (105, 120)),
        ("bytes=-7", (229, 235)),
        ("bytes=220-", (220, 235)),
        ("bytes=230-9999", (230, 235)),
        ("bytes=235-235", (235, 235)),
    ],
)
async def test_ranges_cross_the_header_chunks_and_tail(
    client: AsyncClient,
    session_maker: sessionmaker,
    tmp_path: Path,
    header: str,
    expected_range: tuple[int, int],
) -> None:
    start, end = expected_range
    first, second = bytes(range(68)), bytes(range(124))
    await append_chunk(session_maker, tmp_path, 0, first)
    await append_chunk(session_maker, tmp_path, 1, second)
    response = await client.get(URL, params={"sequence": 1}, headers={"Range": header})
    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes {start}-{end}/236"
    assert response.content == expected_wav(first + second)[start : end + 1]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "header",
    [
        "bytes=112-",
        "bytes=9-2",
        "bytes=-0",
        "items=0-5",
        "bytes=0-1,4-5",
        "bytes=-",
        # Too many digits for any file; int() would refuse 4,300 of them.
        f"bytes=0-{'9' * 5000}",
    ],
)
async def test_unsatisfiable_ranges(
    client: AsyncClient,
    session_maker: sessionmaker,
    tmp_path: Path,
    header: str,
) -> None:
    await append_chunk(session_maker, tmp_path, 0, bytes(68))
    response = await client.get(URL, params={"sequence": 0}, headers={"Range": header})
    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */112"


@pytest.mark.anyio
async def test_snapshots_stay_pinned_after_append_and_final_processing(
    client: AsyncClient,
    session_maker: sessionmaker,
    tmp_path: Path,
) -> None:
    first = bytes(range(68))
    await append_chunk(session_maker, tmp_path, 0, first)
    before = await client.get(URL, params={"sequence": 0})
    await append_chunk(session_maker, tmp_path, 1, bytes(128))
    async with session_maker() as session:
        recording = await session.get(Recording, 1)
        recording.status = RecordingStatus.PROCESSED
        recording.proxy_path = str(tmp_path / "proxy.mp3")
        Path(recording.proxy_path).write_bytes(b"mp3")
        await session.commit()
    after = await client.get(URL, params={"sequence": 0})
    assert before.content == after.content == expected_wav(first)
    grown = await client.get(URL, params={"sequence": 1})
    assert grown.content == expected_wav(first + bytes(128))


@pytest.mark.anyio
async def test_empty_unplaced_missing_and_foreign_audio_are_unavailable(
    client: AsyncClient,
    session_maker: sessionmaker,
    tmp_path: Path,
) -> None:
    assert (await client.get(URL, params={"sequence": 0})).status_code == 404
    path = await append_chunk(session_maker, tmp_path, 1, bytes(128))
    assert (await client.get(URL, params={"sequence": 1})).status_code == 404
    await append_chunk(session_maker, tmp_path, 0, bytes(128))
    assert (await client.get(URL, params={"sequence": 2})).status_code == 404
    path.unlink()
    assert (await client.get(URL, params={"sequence": 1})).status_code == 404
    # An ffmpeg-made WAV can carry extra chunks before its PCM.
    await append_chunk(session_maker, tmp_path, 1, bytes(128), legacy_header=True)
    assert (await client.get(URL, params={"sequence": 0})).status_code == 200
    assert (await client.get(URL, params={"sequence": 1})).status_code == 404
    async with session_maker() as session:
        await session.execute(
            text("UPDATE recording_audio_chunks SET sample_rate_hz=48000")
        )
        await session.commit()
    assert (await client.get(URL, params={"sequence": 0})).status_code == 404
    detail = await client.get("/api/v1/recordings/rec-1")
    assert detail.json()["capture_playback_sequence"] is None


@pytest.mark.anyio
async def test_endpoint_requires_stream_auth_and_the_recording_owner(
    app: FastAPI,
    client: AsyncClient,
    session_maker: sessionmaker,
    tmp_path: Path,
) -> None:
    await append_chunk(session_maker, tmp_path, 0, bytes(128))
    app.dependency_overrides[get_current_user_stream] = lambda: SimpleNamespace(id=2)
    assert (await client.get(URL, params={"sequence": 0})).status_code == 404
    assert (
        await client.get(URL.replace("rec-1", "absent"), params={"sequence": 0})
    ).status_code == 404
    del app.dependency_overrides[get_current_user_stream]
    assert (await client.get(URL, params={"sequence": 0})).status_code == 401


@pytest.mark.anyio
async def test_detail_tracks_the_placed_prefix_through_stop(
    client: AsyncClient,
    session_maker: sessionmaker,
    tmp_path: Path,
) -> None:
    detail_url = "/api/v1/recordings/rec-1"
    empty = await client.get(detail_url)
    assert empty.json()["capture_playback_sequence"] == -1
    await append_chunk(session_maker, tmp_path, 1, bytes(128))
    assert (await client.get(detail_url)).json()["capture_playback_sequence"] == -1
    await append_chunk(session_maker, tmp_path, 0, bytes(128))
    placed = await client.get(detail_url)
    assert placed.json()["capture_playback_sequence"] == 1
    async with session_maker() as session:
        recording = await session.get(Recording, 1)
        recording.status = RecordingStatus.QUEUED
        recording.client_status = ClientStatus.IDLE
        await session.commit()
    assert (await client.get(detail_url)).json()["capture_playback_sequence"] == 1


@pytest.mark.anyio
@pytest.mark.parametrize("status", [RecordingStatus.PROCESSED, RecordingStatus.ERROR])
async def test_detail_keeps_capture_playback_until_the_proxy_exists(
    client: AsyncClient,
    session_maker: sessionmaker,
    tmp_path: Path,
    status: RecordingStatus,
) -> None:
    await append_chunk(session_maker, tmp_path, 0, bytes(128))
    proxy = tmp_path / "meeting.mp3"
    async with session_maker() as session:
        recording = await session.get(Recording, 1)
        recording.status = status
        recording.client_status = ClientStatus.IDLE
        recording.proxy_path = str(proxy)
        await session.commit()
    pending = await client.get("/api/v1/recordings/rec-1")
    assert pending.json()["capture_playback_sequence"] == 0
    assert pending.json()["has_proxy"] is False
    proxy.write_bytes(b"mp3")
    ready = await client.get("/api/v1/recordings/rec-1")
    assert ready.json()["capture_playback_sequence"] is None
    assert ready.json()["has_proxy"] is True


@pytest.fixture
def audio_reads(monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], list[int]]:
    opened: list[str] = []
    sizes: list[int] = []
    real_open = capture_playback.aiofiles.open

    @asynccontextmanager
    async def tracked_open(path: str, mode: str) -> AsyncIterator[Any]:
        opened.append(Path(path).name)
        async with real_open(path, mode) as handle:
            real_read = handle.read

            async def read(size: int) -> bytes:
                sizes.append(size)
                return await real_read(size)

            handle.read = read
            yield handle

    monkeypatch.setattr(capture_playback.aiofiles, "open", tracked_open)
    return opened, sizes


@pytest.mark.anyio
async def test_only_intersecting_files_are_opened_with_bounded_reads(
    client: AsyncClient,
    session_maker: sessionmaker,
    tmp_path: Path,
    audio_reads: tuple[list[str], list[int]],
) -> None:
    pcm = bytes(range(256)) * 512
    for sequence in range(3):
        await append_chunk(session_maker, tmp_path, sequence, pcm)
    # A missing unselected file must not trigger a whole-meeting disk scan.
    (tmp_path / "0.wav").unlink()
    (tmp_path / "2.wav").unlink()
    start = 44 + len(pcm)
    response = await client.get(
        URL,
        params={"sequence": 2},
        headers={"Range": f"bytes={start}-{start + len(pcm) - 1}"},
    )
    assert response.status_code == 206
    assert response.content == pcm
    opened, sizes = audio_reads
    assert opened == ["1.wav"]
    assert sizes and max(sizes) <= 64 * 1024


@pytest.mark.anyio
async def test_large_ranges_are_capped_but_full_reads_stream_every_byte(
    client: AsyncClient,
    session_maker: sessionmaker,
    tmp_path: Path,
    audio_reads: tuple[list[str], list[int]],
) -> None:
    pcm = bytes(range(256)) * 12_000
    await append_chunk(session_maker, tmp_path, 0, pcm)
    partial = await client.get(
        URL, params={"sequence": 0}, headers={"Range": "bytes=0-"}
    )
    assert len(partial.content) == 2500 * 1024
    full = await client.get(URL, params={"sequence": 0})
    assert full.status_code == 200
    assert full.content == expected_wav(pcm)
    assert max(audio_reads[1]) <= 64 * 1024
