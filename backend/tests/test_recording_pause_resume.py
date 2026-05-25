from __future__ import annotations

import io
import os
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api import deps
from backend.api.deps import get_db
from backend.api.v1.api import api_router

TRUSTED_ORIGIN = "https://nojoin.example.com"
BASE_URL = TRUSTED_ORIGIN

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
    file_size_bytes INTEGER,
    status VARCHAR(32) NOT NULL,
    client_status VARCHAR(32),
    upload_progress INTEGER NOT NULL,
    processing_progress INTEGER NOT NULL,
    processing_step VARCHAR(255),
    processing_started_at DATETIME,
    processing_completed_at DATETIME,
    pipeline_generation VARCHAR(32) DEFAULT 'unified',
    is_archived BOOLEAN NOT NULL,
    is_deleted BOOLEAN NOT NULL,
    user_id INTEGER,
    calendar_event_id INTEGER
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
    meeting_edge_focus TEXT,
    meeting_edge_payload JSON,
    meeting_edge_status VARCHAR(32) NOT NULL DEFAULT 'idle',
    meeting_edge_error_message TEXT,
    meeting_edge_source_signature TEXT,
    speaker_name_suggestions JSON,
    notes_status VARCHAR(32) NOT NULL,
    transcript_status VARCHAR(32) NOT NULL,
    error_message TEXT
)
"""

RECORDING_AUDIO_CHUNKS_SCHEMA = """
CREATE TABLE recording_audio_chunks (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    public_id VARCHAR(36) NOT NULL,
    recording_id INTEGER NOT NULL,
    sequence_no INTEGER NOT NULL,
    source_kind VARCHAR(32) NOT NULL,
    absolute_start_ms INTEGER NOT NULL,
    absolute_end_ms INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    sample_rate_hz INTEGER NOT NULL,
    channel_count INTEGER NOT NULL,
    byte_size INTEGER NOT NULL,
    sha256 VARCHAR(128) NOT NULL,
    storage_path VARCHAR(1024) NOT NULL,
    upload_status VARCHAR(32) NOT NULL,
    idempotency_key VARCHAR(255),
    received_at DATETIME NOT NULL,
    cleanup_eligible_at DATETIME
)
"""

RECORDING_AUDIO_WINDOW_MANIFESTS_SCHEMA = """
CREATE TABLE recording_audio_window_manifests (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    public_id VARCHAR(36) NOT NULL,
    recording_id INTEGER NOT NULL,
    window_index INTEGER NOT NULL,
    source_kind VARCHAR(32) NOT NULL,
    target_window_ms INTEGER NOT NULL,
    hop_ms INTEGER NOT NULL,
    window_start_ms INTEGER NOT NULL,
    window_end_ms INTEGER NOT NULL,
    chunk_start_sequence INTEGER NOT NULL,
    chunk_end_sequence INTEGER NOT NULL,
    status VARCHAR(32) NOT NULL,
    is_partial BOOLEAN NOT NULL,
    is_sealed BOOLEAN NOT NULL,
    processing_run_id INTEGER,
    last_error TEXT
)
"""

RECORDING_SPEAKERS_SCHEMA = """
CREATE TABLE recording_speakers (
    id INTEGER PRIMARY KEY,
    created_at DATETIME,
    updated_at DATETIME,
    public_id VARCHAR(36),
    recording_id INTEGER,
    diarization_label VARCHAR(255),
    name VARCHAR(255),
    local_name VARCHAR(255),
    color VARCHAR(32),
    global_speaker_id INTEGER,
    notes TEXT,
    is_voiceprint_locked BOOLEAN DEFAULT 0
)
"""

RECORDING_TAGS_SCHEMA = """
CREATE TABLE recording_tags (
    id INTEGER PRIMARY KEY,
    recording_id INTEGER,
    tag_id INTEGER
)
"""

CHAT_MESSAGES_SCHEMA = """
CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY,
    created_at DATETIME,
    updated_at DATETIME,
    recording_id INTEGER,
    role VARCHAR(32),
    content TEXT,
    timestamp DATETIME
)
"""

DOCUMENTS_SCHEMA = """
CREATE TABLE documents (
    id INTEGER PRIMARY KEY,
    created_at DATETIME,
    updated_at DATETIME,
    recording_id INTEGER,
    filename VARCHAR(255),
    original_filename VARCHAR(255),
    file_path VARCHAR(1024),
    content_type VARCHAR(255),
    size_bytes INTEGER,
    status VARCHAR(32)
)
"""

CONTEXT_CHUNKS_SCHEMA = """
CREATE TABLE context_chunks (
    id INTEGER PRIMARY KEY,
    created_at DATETIME,
    updated_at DATETIME,
    recording_id INTEGER,
    chunk_index INTEGER,
    text TEXT,
    token_count INTEGER,
    metadata_json TEXT
)
"""


def build_test_user() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        username="alice",
        role="user",
        is_superuser=False,
        force_password_change=False,
        is_active=True,
        token_version=0,
        settings={},
    )


def set_session_cookie(client: AsyncClient) -> None:
    client.cookies.set(
        "access_token",
        "session-token",
        domain="nojoin.example.com",
        path="/",
    )


def make_wav_bytes(*, duration_s: float = 0.25, sample_rate: int = 16000) -> bytes:
    frame_count = int(duration_s * sample_rate)
    pcm_frames = b"\x00\x00" * frame_count
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_frames)
    return buffer.getvalue()


async def insert_recording(
    session_maker: sessionmaker,
    *,
    recording_id: int,
    public_id: str,
    status: str,
    user_id: int = 1,
) -> None:
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, proxy_path, celery_task_id, duration_seconds,
                    file_size_bytes, status, client_status, upload_progress,
                    processing_progress, processing_step, processing_started_at,
                    processing_completed_at, is_archived, is_deleted, user_id,
                    calendar_event_id
                ) VALUES (
                    :id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :name, :public_id,
                    :meeting_uid, :audio_path, NULL, NULL, NULL, NULL, :status,
                    :client_status, 0, 0, NULL, NULL, NULL, 0, 0, :user_id, NULL
                )
                """
            ),
            {
                "id": recording_id,
                "name": f"Recording {recording_id}",
                "public_id": public_id,
                "meeting_uid": f"meeting-{recording_id}",
                "audio_path": f"/tmp/{recording_id}.wav",
                "status": status,
                "client_status": "PAUSED" if status == "PAUSED" else None,
                "user_id": user_id,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO transcripts (
                    id, created_at, updated_at, recording_id, text, segments, notes,
                    user_notes, meeting_edge_focus, meeting_edge_payload,
                    meeting_edge_status, meeting_edge_error_message,
                    meeting_edge_source_signature, speaker_name_suggestions,
                    notes_status, transcript_status, error_message
                ) VALUES (
                    :id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :recording_id,
                    NULL, NULL, NULL, NULL, NULL, NULL, 'idle', NULL, NULL,
                    NULL, 'pending', 'processing', NULL
                )
                """
            ),
            {"id": recording_id, "recording_id": recording_id},
        )
        await session.commit()


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
        await connection.execute(text(RECORDING_AUDIO_CHUNKS_SCHEMA))
        await connection.execute(text(RECORDING_AUDIO_WINDOW_MANIFESTS_SCHEMA))
        await connection.execute(text(RECORDING_SPEAKERS_SCHEMA))
        await connection.execute(text(RECORDING_TAGS_SCHEMA))
        await connection.execute(text(CHAT_MESSAGES_SCHEMA))
        await connection.execute(text(DOCUMENTS_SCHEMA))
        await connection.execute(text(CONTEXT_CHUNKS_SCHEMA))

    try:
        yield session_maker
    finally:
        await engine.dispose()


@pytest.fixture
async def api_app(monkeypatch, tmp_path, test_session_maker: sessionmaker) -> FastAPI:
    monkeypatch.setenv("WEB_APP_URL", TRUSTED_ORIGIN)

    async def override_get_db():
        async with test_session_maker() as session:
            yield session

    fake_user = build_test_user()

    async def fake_get_authenticated_token_details(db, actual_token, *, allowed_token_types, required_scopes_by_type=None):
        if actual_token == "session-token":
            return fake_user, {"sub": fake_user.username, "token_type": "session", "scopes": ["session:web"]}
        if actual_token == "companion-token":
            return fake_user, {"sub": fake_user.username, "token_type": "companion", "scopes": ["companion:init", "recordings:companion"]}
        raise AssertionError(f"Unexpected token: {actual_token}")

    async def fake_get_authenticated_user_from_token(db, actual_token, *, allowed_token_types, required_scopes_by_type=None):
        assert actual_token == "session-token"
        return fake_user

    from backend.api.v1.endpoints import recordings as recordings_module
    from backend.utils import recording_storage

    monkeypatch.setattr(deps, "get_authenticated_token_details", fake_get_authenticated_token_details)
    monkeypatch.setattr(deps, "get_authenticated_user_from_token", fake_get_authenticated_user_from_token)
    monkeypatch.setattr(recordings_module, "recordings_root_dir", lambda: tmp_path)
    monkeypatch.setattr(
        recordings_module,
        "recording_upload_temp_dir",
        lambda recording_id, create=False: _recording_temp_dir(tmp_path, recording_id, create=create),
    )
    monkeypatch.setattr(recording_storage, "recordings_root_dir", lambda create=True: tmp_path)
    monkeypatch.setattr(
        recording_storage,
        "recording_upload_temp_dir",
        lambda recording_id, create=False: _recording_temp_dir(tmp_path, recording_id, create=create),
    )
    monkeypatch.setattr(recordings_module.transcribe_segment_live_task, "delay", lambda *args, **kwargs: None)
    monkeypatch.setattr(recordings_module.process_recording_task, "delay", lambda *args, **kwargs: SimpleNamespace(id="task-1"))
    monkeypatch.setattr(recordings_module.generate_proxy_task, "delay", lambda *args, **kwargs: None)

    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    return app


def _recording_temp_dir(root: Path, recording_id: int, *, create: bool) -> Path:
    path = root / f"upload-{recording_id}"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
async def client(api_app: FastAPI) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=api_app),
        base_url=BASE_URL,
    ) as async_client:
        yield async_client


@pytest.mark.anyio
async def test_session_init_pause_resume_finalize_round_trip(
    client: AsyncClient,
) -> None:
    set_session_cookie(client)

    init_response = await client.post(
        "/api/v1/recordings/init",
        params={"name": "Browser meeting"},
        headers={"Origin": TRUSTED_ORIGIN},
    )

    assert init_response.status_code == 200
    init_payload = init_response.json()
    assert init_payload["upload_token"] is None
    recording_id = init_payload["id"]

    first_segment = await client.post(
        f"/api/v1/recordings/{recording_id}/segment",
        params={"sequence": 0},
        headers={"Origin": TRUSTED_ORIGIN},
        files={"file": ("0.wav", make_wav_bytes(), "audio/wav")},
    )
    assert first_segment.status_code == 200

    pause_response = await client.post(
        f"/api/v1/recordings/{recording_id}/pause",
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert pause_response.status_code == 200
    assert pause_response.json() == {
        "recording_id": recording_id,
        "status": "PAUSED",
        "last_sequence": 0,
    }

    paused_list = await client.get(
        "/api/v1/recordings",
        params={"status": "PAUSED", "user": "me"},
    )
    assert paused_list.status_code == 200
    paused_payload = paused_list.json()
    assert len(paused_payload) == 1
    assert paused_payload[0]["id"] == recording_id
    assert paused_payload[0]["status"] == "PAUSED"

    resume_response = await client.post(
        f"/api/v1/recordings/{recording_id}/resume",
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert resume_response.status_code == 200
    assert resume_response.json() == {
        "recording_id": recording_id,
        "status": "UPLOADING",
        "last_sequence": 0,
    }

    second_segment = await client.post(
        f"/api/v1/recordings/{recording_id}/segment",
        params={"sequence": 1},
        headers={"Origin": TRUSTED_ORIGIN},
        files={"file": ("1.wav", make_wav_bytes(), "audio/wav")},
    )
    assert second_segment.status_code == 200

    finalize_response = await client.post(
        f"/api/v1/recordings/{recording_id}/finalize",
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert finalize_response.status_code == 200
    assert finalize_response.json()["status"] == "QUEUED"


@pytest.mark.anyio
async def test_init_rejects_existing_paused_recording(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    set_session_cookie(client)
    public_id = "paused-recording-public-id"
    await insert_recording(
        test_session_maker,
        recording_id=1001,
        public_id=public_id,
        status="PAUSED",
    )

    response = await client.post(
        "/api/v1/recordings/init",
        params={"name": "Blocked meeting"},
        headers={"Origin": TRUSTED_ORIGIN},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "active_recording_exists",
        "message": "Handle the existing active recording before starting a new one.",
        "recording_id": public_id,
        "status": "PAUSED",
    }


@pytest.mark.anyio
async def test_discard_allows_paused_recordings_and_cleans_temp_dir(
    client: AsyncClient,
    tmp_path,
) -> None:
    set_session_cookie(client)

    init_response = await client.post(
        "/api/v1/recordings/init",
        params={"name": "Discard me"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    recording_id = init_response.json()["id"]

    segment_response = await client.post(
        f"/api/v1/recordings/{recording_id}/segment",
        params={"sequence": 0},
        headers={"Origin": TRUSTED_ORIGIN},
        files={"file": ("0.wav", make_wav_bytes(), "audio/wav")},
    )
    assert segment_response.status_code == 200

    pause_response = await client.post(
        f"/api/v1/recordings/{recording_id}/pause",
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert pause_response.status_code == 200

    discard_response = await client.post(
        f"/api/v1/recordings/{recording_id}/discard",
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert discard_response.status_code == 200
    assert discard_response.json() == {"ok": True}

    upload_dirs = [path for path in tmp_path.iterdir() if path.is_dir()]
    assert upload_dirs == []
    assert not any(tmp_path.glob("*.wav"))
