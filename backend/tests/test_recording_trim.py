"""Tests for non-destructive recording trimming (MR-C).

Covers the pure filter_segments_for_trim helper, the PATCH
/recordings/{id}/trim endpoint, and trim-aware transcript export.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.api.v1.endpoints.transcripts import (
    _format_transcript_text,
    filter_segments_for_trim,
)


# --- filter_segments_for_trim unit tests ------------------------------------


def _seg(start: float, end: float, text: str = "x"):
    return {"start": start, "end": end, "text": text, "speaker": "SPEAKER_00"}


def test_filter_no_trim_passes_through():
    """No trim bounds returns the segments unchanged."""
    segments = [_seg(0, 5), _seg(5, 10)]
    assert filter_segments_for_trim(segments, None, None) is segments


def test_filter_trailing_crop():
    """A trim end drops segments that start after the window."""
    segments = [_seg(0, 5), _seg(5, 10), _seg(10, 15)]
    result = filter_segments_for_trim(segments, None, 8.0)
    assert result == [_seg(0, 5), _seg(5, 10)]


def test_filter_leading_crop():
    """A trim start drops segments that end before the window."""
    segments = [_seg(0, 5), _seg(5, 10), _seg(10, 15)]
    result = filter_segments_for_trim(segments, 6.0, None)
    assert result == [_seg(5, 10), _seg(10, 15)]


def test_filter_both_bounds():
    """Both bounds keep only the segments overlapping the window."""
    segments = [_seg(0, 5), _seg(5, 10), _seg(10, 15), _seg(15, 20)]
    result = filter_segments_for_trim(segments, 6.0, 12.0)
    assert result == [_seg(5, 10), _seg(10, 15)]


def test_filter_boundary_straddling_segment_kept():
    """A segment straddling either boundary is partially inside, so kept."""
    segments = [_seg(0, 7), _seg(7, 14)]
    # Window [5, 10): the first segment ends at 7 > 5, the second starts at 7 < 10.
    result = filter_segments_for_trim(segments, 5.0, 10.0)
    assert result == segments


# --- endpoint fixtures (mirrors test_reprocess.py) --------------------------


RECORDINGS_SCHEMA = """
CREATE TABLE recordings (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    name VARCHAR(255) NOT NULL,
    public_id VARCHAR(36) NOT NULL,
    meeting_uid VARCHAR(36) NOT NULL,
    audio_path VARCHAR(1024) NOT NULL,
    proxy_path VARCHAR(1024),
    celery_task_id VARCHAR(255),
    duration_seconds FLOAT,
    trim_start_s FLOAT,
    trim_end_s FLOAT,
    file_size_bytes INTEGER,
    status VARCHAR(32) NOT NULL,
    client_status VARCHAR(32),
    upload_progress INTEGER NOT NULL,
    processing_progress INTEGER NOT NULL,
    processing_step VARCHAR(255),
    processing_started_at DATETIME,
    processing_completed_at DATETIME,
    is_archived BOOLEAN NOT NULL,
    is_deleted BOOLEAN NOT NULL,
    user_id INTEGER
)
"""

TRANSCRIPTS_SCHEMA = """
CREATE TABLE transcripts (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    recording_id INTEGER NOT NULL UNIQUE,
    text TEXT,
    segments JSON,
    notes TEXT,
    user_notes TEXT,
    notes_status VARCHAR(32) NOT NULL,
    transcript_status VARCHAR(32) NOT NULL,
    error_message TEXT
)
"""

CHAT_MESSAGES_SCHEMA = """
CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    recording_id INTEGER NOT NULL,
    role VARCHAR(32) NOT NULL,
    content TEXT
)
"""

CONTEXT_CHUNKS_SCHEMA = """
CREATE TABLE context_chunks (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    recording_id INTEGER NOT NULL,
    document_id INTEGER,
    content TEXT,
    embedding JSON
)
"""

RECORDING_SPEAKERS_SCHEMA = """
CREATE TABLE recording_speakers (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    recording_id INTEGER NOT NULL,
    global_speaker_id INTEGER,
    diarization_label VARCHAR(255),
    local_name VARCHAR(255),
    name VARCHAR(255),
    snippet_start FLOAT,
    snippet_end FLOAT,
    voice_snippet_path VARCHAR(1024)
)
"""


def build_test_user(user_id: int = 1, username: str = "alice"):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=user_id,
        username=username,
        force_password_change=False,
    )


@pytest.fixture
async def test_session_maker() -> sessionmaker:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.execute(text(RECORDINGS_SCHEMA))
        await connection.execute(text(TRANSCRIPTS_SCHEMA))
        await connection.execute(text(CHAT_MESSAGES_SCHEMA))
        await connection.execute(text(CONTEXT_CHUNKS_SCHEMA))
        await connection.execute(text(RECORDING_SPEAKERS_SCHEMA))

    try:
        yield session_maker
    finally:
        await engine.dispose()


@pytest.fixture
async def api_app() -> FastAPI:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    return app


@pytest.fixture
async def client(api_app: FastAPI, test_session_maker: sessionmaker) -> AsyncClient:
    async def override_get_db():
        async with test_session_maker() as session:
            yield session

    api_app.dependency_overrides[get_db] = override_get_db
    api_app.dependency_overrides[get_current_user] = lambda: build_test_user()

    transport = ASGITransport(app=api_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client

    api_app.dependency_overrides.clear()


async def _insert_recording(
    session_maker: sessionmaker,
    *,
    recording_id: int = 401,
    public_id: str = "trim-rec-public-id",
    user_id: int = 1,
    status: str = "PROCESSED",
    duration_seconds: float = 120.0,
) -> None:
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, duration_seconds, status, upload_progress,
                    processing_progress, is_archived, is_deleted, user_id
                ) VALUES (
                    :id, :now, :now, :name, :public_id, :meeting_uid,
                    :audio_path, :duration, :status, 0, 100, 0, 0, :user_id
                )
                """
            ),
            {
                "id": recording_id,
                "now": "2026-05-16 00:00:00",
                "name": "Trimmable meeting",
                "public_id": public_id,
                "meeting_uid": f"meeting-uid-{recording_id}",
                "audio_path": f"/tmp/recording-{recording_id}.wav",
                "duration": duration_seconds,
                "status": status,
                "user_id": user_id,
            },
        )
        await session.commit()


# --- endpoint tests ---------------------------------------------------------


@pytest.mark.anyio
async def test_set_trim_on_processed_recording(
    client: AsyncClient, test_session_maker: sessionmaker
) -> None:
    """PATCH /trim stores both offsets and echoes them back."""
    await _insert_recording(test_session_maker, recording_id=401, public_id="rec-401")

    response = await client.patch(
        "/api/v1/recordings/rec-401/trim",
        json={"trim_start_s": 10.0, "trim_end_s": 90.0},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["trim_start_s"] == 10.0
    assert body["trim_end_s"] == 90.0


@pytest.mark.anyio
async def test_clear_trim_restores_full_recording(
    client: AsyncClient, test_session_maker: sessionmaker
) -> None:
    """Sending both offsets as NULL clears the trim."""
    await _insert_recording(test_session_maker, recording_id=402, public_id="rec-402")

    await client.patch(
        "/api/v1/recordings/rec-402/trim",
        json={"trim_start_s": 10.0, "trim_end_s": 90.0},
    )
    response = await client.patch(
        "/api/v1/recordings/rec-402/trim",
        json={"trim_start_s": None, "trim_end_s": None},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["trim_start_s"] is None
    assert body["trim_end_s"] is None


@pytest.mark.anyio
async def test_trim_negative_start_rejected(
    client: AsyncClient, test_session_maker: sessionmaker
) -> None:
    """A negative trim_start_s is rejected with 422."""
    await _insert_recording(test_session_maker, recording_id=403, public_id="rec-403")

    response = await client.patch(
        "/api/v1/recordings/rec-403/trim",
        json={"trim_start_s": -1.0, "trim_end_s": 90.0},
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_trim_end_beyond_duration_rejected(
    client: AsyncClient, test_session_maker: sessionmaker
) -> None:
    """A trim_end_s past the recording duration is rejected with 422."""
    await _insert_recording(
        test_session_maker, recording_id=404, public_id="rec-404", duration_seconds=120.0
    )

    response = await client.patch(
        "/api/v1/recordings/rec-404/trim",
        json={"trim_start_s": 0.0, "trim_end_s": 999.0},
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_trim_window_too_short_rejected(
    client: AsyncClient, test_session_maker: sessionmaker
) -> None:
    """A trimmed window shorter than 1 second is rejected with 422."""
    await _insert_recording(test_session_maker, recording_id=405, public_id="rec-405")

    response = await client.patch(
        "/api/v1/recordings/rec-405/trim",
        json={"trim_start_s": 10.0, "trim_end_s": 10.5},
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_trim_start_not_before_end_rejected(
    client: AsyncClient, test_session_maker: sessionmaker
) -> None:
    """A trim_start_s that is not strictly before trim_end_s is rejected with 422."""
    await _insert_recording(test_session_maker, recording_id=406, public_id="rec-406")

    response = await client.patch(
        "/api/v1/recordings/rec-406/trim",
        json={"trim_start_s": 80.0, "trim_end_s": 40.0},
    )

    assert response.status_code == 422


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["UPLOADING", "QUEUED", "PROCESSING", "ERROR"])
async def test_trim_non_processed_recording_rejected(
    client: AsyncClient, test_session_maker: sessionmaker, status: str
) -> None:
    """Trimming a recording that is not PROCESSED is rejected with 409."""
    await _insert_recording(
        test_session_maker, recording_id=407, public_id="rec-407", status=status
    )

    response = await client.patch(
        "/api/v1/recordings/rec-407/trim",
        json={"trim_start_s": 10.0, "trim_end_s": 90.0},
    )

    assert response.status_code == 409


@pytest.mark.anyio
async def test_trim_other_users_recording_rejected(
    client: AsyncClient, test_session_maker: sessionmaker
) -> None:
    """Trimming a recording owned by another user is rejected with 404."""
    await _insert_recording(
        test_session_maker, recording_id=408, public_id="rec-408", user_id=999
    )

    response = await client.patch(
        "/api/v1/recordings/rec-408/trim",
        json={"trim_start_s": 10.0, "trim_end_s": 90.0},
    )

    assert response.status_code == 404


# --- export filtering test --------------------------------------------------


def test_export_text_omits_out_of_trim_segment():
    """An out-of-trim segment is absent from the exported txt output."""
    segments = [
        {"start": 0.0, "end": 5.0, "text": "inside one", "speaker": "SPEAKER_00"},
        {"start": 5.0, "end": 10.0, "text": "inside two", "speaker": "SPEAKER_00"},
        {"start": 100.0, "end": 110.0, "text": "trailing dead air", "speaker": "SPEAKER_00"},
    ]
    speaker_map = {"SPEAKER_00": "Alice"}

    filtered = filter_segments_for_trim(segments, None, 20.0)
    output = _format_transcript_text(filtered, speaker_map)

    assert "inside one" in output
    assert "inside two" in output
    assert "trailing dead air" not in output

    # Clearing the trim restores the full transcript.
    full_output = _format_transcript_text(
        filter_segments_for_trim(segments, None, None), speaker_map
    )
    assert "trailing dead air" in full_output
