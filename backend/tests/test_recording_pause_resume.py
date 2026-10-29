from __future__ import annotations

import io
import json
import wave
from datetime import datetime, timedelta
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
from backend.tests.sqlite_schemas import (
    RECORDING_AUDIO_CHUNK_CURSORS_SCHEMA,
    TIMELINE_NOTES_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
)

TRUSTED_ORIGIN = "https://nojoin.example.com"
BASE_URL = TRUSTED_ORIGIN

RECORDINGS_SCHEMA = """
CREATE TABLE recordings (
    max_speakers INTEGER,
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
    last_activity_at DATETIME,
    capture_epoch INTEGER NOT NULL DEFAULT 0,
    consent_notice_outcome VARCHAR(16),
    transcription_language VARCHAR(16),
    user_id INTEGER,
    calendar_event_id INTEGER
)
"""

TRANSCRIPTS_SCHEMA = """
CREATE TABLE transcripts (
    change_sequence BIGINT NOT NULL DEFAULT 0,
    settling_state JSON,
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    recording_id INTEGER NOT NULL UNIQUE,
    text TEXT,
    segments JSON,
    language_detection JSON,
    notes TEXT,
    meeting_edge_focus TEXT,
    meeting_edge_payload JSON,
    meeting_edge_status VARCHAR(32) NOT NULL DEFAULT 'idle',
    meeting_edge_error_message TEXT,
    meeting_edge_source_signature TEXT,
    speaker_name_suggestions JSON,
    notes_template_id INTEGER,
    notes_template_sections TEXT,
    notes_status VARCHAR(32) NOT NULL,
    notes_stale_documents BOOLEAN DEFAULT 0,
    transcript_status VARCHAR(32) NOT NULL,
    error_message TEXT,
    analytics_payload JSON,
    analytics_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    analytics_ai_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    analytics_ai_error_message TEXT,
    analytics_error_message TEXT
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
    cleanup_eligible_at DATETIME,
    UNIQUE(recording_id, sequence_no),
    UNIQUE(recording_id, idempotency_key)
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
    asr_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    asr_processing_run_id INTEGER,
    asr_last_error TEXT,
    diarization_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    diarization_processing_run_id INTEGER,
    diarization_config_hash VARCHAR(255),
    diarization_window_result_id INTEGER,
    diarization_last_error TEXT,
    is_partial BOOLEAN NOT NULL,
    is_sealed BOOLEAN NOT NULL,
    processing_run_id INTEGER,
    last_error TEXT,
    UNIQUE(recording_id, window_index)
)
"""

RECORDING_SPEAKERS_SCHEMA = """
CREATE TABLE recording_speakers (
    embedding_version INTEGER,
    embedding_speech_seconds FLOAT,
    id INTEGER PRIMARY KEY,
    created_at DATETIME,
    updated_at DATETIME,
    public_id VARCHAR(36),
    recording_id INTEGER,
    diarization_label VARCHAR(255),
    name VARCHAR(255),
    local_name VARCHAR(255),
    speaker_status VARCHAR(32) DEFAULT 'active',
    speaker_kind VARCHAR(32) DEFAULT 'automated',
    processing_run_id INTEGER,
    last_speaker_correction_event_id INTEGER,
    last_diarization_window_result_id INTEGER,
    first_seen_ms INTEGER,
    last_seen_ms INTEGER,
    identity_confidence FLOAT,
    identity_locked BOOLEAN DEFAULT 0,
    name_last_edit_source VARCHAR(32),
    name_source VARCHAR(16),
    snippet_start FLOAT,
    snippet_end FLOAT,
    voice_snippet_path VARCHAR(1024),
    embedding JSON,
    color VARCHAR(32),
    global_speaker_id INTEGER,
    merged_into_id INTEGER,
    notes TEXT,
    is_voiceprint_locked BOOLEAN DEFAULT 0
)
"""

RECORDING_TAGS_SCHEMA = """
CREATE TABLE recording_tags (
    id INTEGER PRIMARY KEY,
    created_at DATETIME,
    updated_at DATETIME,
    public_id VARCHAR(36),
    recording_id INTEGER,
    tag_id INTEGER
)
"""

TAGS_SCHEMA = """
CREATE TABLE tags (
    id INTEGER PRIMARY KEY,
    created_at DATETIME,
    updated_at DATETIME,
    public_id VARCHAR(36),
    name VARCHAR(255),
    color VARCHAR(32),
    user_id INTEGER,
    parent_id INTEGER
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
    status VARCHAR(32),
    note_id INTEGER,
    use_in_notes BOOLEAN NOT NULL DEFAULT 0
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
                    meeting_edge_focus, meeting_edge_payload,
                    meeting_edge_status, meeting_edge_error_message,
                    meeting_edge_source_signature, speaker_name_suggestions,
                    notes_status, transcript_status, error_message
                ) VALUES (
                    :id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :recording_id,
                    NULL, NULL, NULL, NULL, NULL, 'idle', NULL, NULL,
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
        await connection.execute(text(RECORDING_AUDIO_CHUNK_CURSORS_SCHEMA))
        await connection.execute(text(RECORDING_SPEAKERS_SCHEMA))
        await connection.execute(text(RECORDING_TAGS_SCHEMA))
        await connection.execute(text(TAGS_SCHEMA))
        await connection.execute(text(CHAT_MESSAGES_SCHEMA))
        await connection.execute(text(DOCUMENTS_SCHEMA))
        await connection.execute(text(CONTEXT_CHUNKS_SCHEMA))
        await connection.execute(text(TIMELINE_NOTES_SCHEMA))
        await connection.execute(text(TRANSCRIPT_UTTERANCES_SCHEMA))

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

    async def fake_get_authenticated_token_details(
        db, actual_token, *, allowed_token_types, required_scopes_by_type=None
    ):
        if actual_token == "session-token":
            return fake_user, {
                "sub": fake_user.username,
                "token_type": "session",
                "scopes": ["session:web"],
            }
        raise AssertionError(f"Unexpected token: {actual_token}")

    async def fake_get_authenticated_user_from_token(
        db, actual_token, *, allowed_token_types, required_scopes_by_type=None
    ):
        assert actual_token == "session-token"
        return fake_user

    from backend.api.v1.endpoints import recordings as recordings_module
    from backend.utils import recording_storage

    monkeypatch.setattr(
        deps, "get_authenticated_token_details", fake_get_authenticated_token_details
    )
    monkeypatch.setattr(
        deps,
        "get_authenticated_user_from_token",
        fake_get_authenticated_user_from_token,
    )
    monkeypatch.setattr(recordings_module, "recordings_root_dir", lambda: tmp_path)
    monkeypatch.setattr(
        recordings_module,
        "recording_upload_temp_dir",
        lambda recording_id, create=False: _recording_temp_dir(
            tmp_path, recording_id, create=create
        ),
    )
    monkeypatch.setattr(
        recording_storage, "recordings_root_dir", lambda create=True: tmp_path
    )
    monkeypatch.setattr(
        recording_storage,
        "recording_upload_temp_dir",
        lambda recording_id, create=False: _recording_temp_dir(
            tmp_path, recording_id, create=create
        ),
    )
    monkeypatch.setattr(
        recordings_module.celery_app,
        "send_task",
        lambda *args, **kwargs: SimpleNamespace(id="task-1"),
    )

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
@pytest.mark.parametrize("requested", ["off", "auto"])
async def test_capture_eligibility_policy_and_lifecycle_are_persisted(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
    requested: str,
) -> None:
    from sqlmodel import select

    from backend.models.transcript import Transcript
    from backend.utils.config_manager import config_manager

    original_get = config_manager.get
    settings = {"live_text_settling": requested}
    monkeypatch.setattr(
        config_manager,
        "get",
        lambda key, default=None: settings.get(key, original_get(key, default)),
    )
    set_session_cookie(client)
    response = await client.post(
        "/api/v1/recordings/init", headers={"Origin": TRUSTED_ORIGIN}
    )
    assert response.status_code == 200
    public_id = response.json()["id"]

    async def state() -> dict | None:
        async with test_session_maker() as session:
            return (
                (await session.execute(select(Transcript))).scalar_one().settling_state
            )

    initial = await state()
    if requested == "off":
        assert initial is None
        return
    assert initial["policy"] == {"enabled": None}
    assert initial["capture_epoch"] == 0
    assert not initial["sealed"]
    response = await client.post(
        f"/api/v1/recordings/{public_id}/pause", headers={"Origin": TRUSTED_ORIGIN}
    )
    assert response.status_code == 200
    assert (await state())["phase"] == "paused"
    response = await client.post(
        f"/api/v1/recordings/{public_id}/resume", headers={"Origin": TRUSTED_ORIGIN}
    )
    assert response.status_code == 200
    resumed = await state()
    assert resumed["capture_epoch"] == 1
    assert resumed["generation"] != initial["generation"]
    assert resumed["policy"] == {"enabled": None}
    response = await client.post(
        f"/api/v1/recordings/{public_id}/segment",
        params={"sequence": 0, "epoch": 1},
        headers={"Origin": TRUSTED_ORIGIN},
        files={"file": ("0.wav", make_wav_bytes(), "audio/wav")},
    )
    assert response.status_code == 200
    response = await client.post(
        f"/api/v1/recordings/{public_id}/finalize",
        params={"epoch": 1},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert response.status_code == 200
    sealed = await state()
    assert sealed["sealed"]
    assert sealed["phase"] == "sealed"
    assert sealed["generation"] == resumed["generation"]


@pytest.mark.anyio
async def test_session_init_pause_resume_finalize_round_trip(
    client: AsyncClient,
    test_session_maker: sessionmaker,
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
    assert init_payload["capture_epoch"] == 0
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
        "capture_epoch": 0,
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
    # A resume without an epoch takes the capture over.
    assert resume_response.json() == {
        "recording_id": recording_id,
        "status": "UPLOADING",
        "last_sequence": 0,
        "capture_epoch": 1,
    }

    async with test_session_maker() as session:
        client_status_row = (
            await session.execute(
                text(
                    "SELECT client_status FROM recordings WHERE public_id = :public_id"
                ),
                {"public_id": recording_id},
            )
        ).one()
    assert client_status_row[0] == "RECORDING"

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
    assert finalize_response.json()["client_status"] == "IDLE"

    paused_after_finalize = await client.get(
        "/api/v1/recordings",
        params={"status": "PAUSED", "user": "me"},
    )
    assert paused_after_finalize.status_code == 200
    assert paused_after_finalize.json() == []

    second_init_response = await client.post(
        "/api/v1/recordings/init",
        params={"name": "Second browser meeting"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert second_init_response.status_code == 200
    assert second_init_response.json()["id"] != recording_id


@pytest.mark.anyio
async def test_finalize_accepts_paused_recording_without_resuming(
    client: AsyncClient,
    monkeypatch,
) -> None:
    """A paused capture finalizes directly, no resume round trip (issue #166).

    When the browser runtime is gone the client cannot resume without
    re-prompting the share picker, so requiring UPLOADING here left the
    recording stranded in PAUSED with no route to processing.
    """
    from backend.api.v1.endpoints import recordings as recordings_module

    metrics: list[dict] = []
    monkeypatch.setattr(
        recordings_module,
        "record_pipeline_metric",
        lambda **kwargs: metrics.append(kwargs) or {},
    )

    set_session_cookie(client)

    init_response = await client.post(
        "/api/v1/recordings/init",
        params={"name": "Abandoned meeting"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert init_response.status_code == 200
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
    assert pause_response.json()["status"] == "PAUSED"

    finalize_response = await client.post(
        f"/api/v1/recordings/{recording_id}/finalize",
        headers={"Origin": TRUSTED_ORIGIN},
    )

    assert finalize_response.status_code == 200
    assert finalize_response.json()["status"] == "QUEUED"
    assert finalize_response.json()["client_status"] == "IDLE"

    stages = [metric["stage"] for metric in metrics]
    assert "recording_finalized_from_paused" in stages
    assert "capture_coverage" in stages


@pytest.mark.anyio
async def test_finalize_emits_capture_coverage_metric(
    client: AsyncClient,
    monkeypatch,
) -> None:
    """Coverage is the only server-side signal that a suspended tab lost audio."""
    from backend.api.v1.endpoints import recordings as recordings_module

    metrics: list[dict] = []
    monkeypatch.setattr(
        recordings_module,
        "record_pipeline_metric",
        lambda **kwargs: metrics.append(kwargs) or {},
    )

    set_session_cookie(client)

    init_response = await client.post(
        "/api/v1/recordings/init",
        params={"name": "Coverage meeting"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    recording_id = init_response.json()["id"]

    for sequence in (0, 1):
        segment_response = await client.post(
            f"/api/v1/recordings/{recording_id}/segment",
            params={"sequence": sequence},
            headers={"Origin": TRUSTED_ORIGIN},
            files={"file": (f"{sequence}.wav", make_wav_bytes(), "audio/wav")},
        )
        assert segment_response.status_code == 200

    finalize_response = await client.post(
        f"/api/v1/recordings/{recording_id}/finalize",
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert finalize_response.status_code == 200

    coverage = next(
        metric for metric in metrics if metric["stage"] == "capture_coverage"
    )
    payload = coverage["payload"]
    assert payload["chunk_count"] == 2
    assert payload["captured_seconds"] > 0
    # A recording that finalized straight through was never paused.
    assert "recording_finalized_from_paused" not in [
        metric["stage"] for metric in metrics
    ]


@pytest.mark.anyio
async def test_finalize_rejects_paused_recording_with_a_sequence_gap(
    client: AsyncClient,
) -> None:
    """Accepting PAUSED must not weaken the completeness gates."""
    set_session_cookie(client)

    init_response = await client.post(
        "/api/v1/recordings/init",
        params={"name": "Gappy meeting"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    recording_id = init_response.json()["id"]

    # Sequence 1 never arrives, so the recording is not safe to concatenate.
    for sequence in (0, 2):
        segment_response = await client.post(
            f"/api/v1/recordings/{recording_id}/segment",
            params={"sequence": sequence},
            headers={"Origin": TRUSTED_ORIGIN},
            files={"file": (f"{sequence}.wav", make_wav_bytes(), "audio/wav")},
        )
        assert segment_response.status_code == 200

    pause_response = await client.post(
        f"/api/v1/recordings/{recording_id}/pause",
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert pause_response.status_code == 200

    finalize_response = await client.post(
        f"/api/v1/recordings/{recording_id}/finalize",
        headers={"Origin": TRUSTED_ORIGIN},
    )

    assert finalize_response.status_code == 409
    assert finalize_response.json()["detail"] == (
        "Recording upload is still in progress; finalize after all segment "
        "uploads complete."
    )


@pytest.mark.anyio
async def test_finalize_still_rejects_a_closed_recording(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    set_session_cookie(client)
    public_id = "processed-recording-public-id"
    await insert_recording(
        test_session_maker,
        recording_id=2002,
        public_id=public_id,
        status="PROCESSED",
    )

    finalize_response = await client.post(
        f"/api/v1/recordings/{public_id}/finalize",
        headers={"Origin": TRUSTED_ORIGIN},
    )

    assert finalize_response.status_code == 409
    assert finalize_response.json()["detail"] == (
        "Recording is no longer accepting capture uploads"
    )


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


@pytest.mark.anyio
async def test_discard_cancels_processing_recording_and_revokes_task(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A discard issued while the pipeline is running must revoke the Celery task
    and remove the recording, so the user does not have to cancel then delete."""
    from backend.api.v1.endpoints import recordings as recordings_module

    revoked: list[tuple[str, bool]] = []

    def fake_revoke(task_id, terminate=False, **kwargs):
        revoked.append((task_id, terminate))

    monkeypatch.setattr(recordings_module.celery_app.control, "revoke", fake_revoke)

    await insert_recording(
        test_session_maker,
        recording_id=4242,
        public_id="discard-processing",
        status="PROCESSING",
    )
    async with test_session_maker() as session:
        await session.execute(
            text("UPDATE recordings SET celery_task_id = :tid WHERE id = :id"),
            {"tid": "task-9", "id": 4242},
        )
        await session.commit()

    set_session_cookie(client)

    discard_response = await client.post(
        "/api/v1/recordings/discard-processing/discard",
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert discard_response.status_code == 200
    assert discard_response.json() == {"ok": True}

    # The running pipeline task is revoked with terminate=True before deletion.
    assert revoked == [("task-9", True)]

    async with test_session_maker() as session:
        remaining = (
            await session.execute(
                text("SELECT COUNT(*) FROM recordings WHERE id = :id"),
                {"id": 4242},
            )
        ).scalar_one()
        assert remaining == 0


@pytest.mark.anyio
async def test_discard_rejects_completed_recording(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    """Terminal recordings are removed through the delete flow, not discard."""
    await insert_recording(
        test_session_maker,
        recording_id=4343,
        public_id="discard-completed",
        status="PROCESSED",
    )

    set_session_cookie(client)

    discard_response = await client.post(
        "/api/v1/recordings/discard-completed/discard",
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert discard_response.status_code == 400


@pytest.mark.anyio
async def test_get_recording_hides_in_flight_transcript_content(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.api.v1.endpoints import recordings as recordings_module

    monkeypatch.setattr(
        recordings_module,
        "filter_recording_speakers_for_public_read",
        lambda *args, **kwargs: [],
    )

    set_session_cookie(client)
    await insert_recording(
        test_session_maker,
        recording_id=10,
        public_id="recording-live",
        status="UPLOADING",
    )
    await insert_recording(
        test_session_maker,
        recording_id=11,
        public_id="recording-final",
        status="PROCESSED",
    )

    transcript_segments = [
        {
            "id": "seg-1",
            "start": 0.0,
            "end": 1.2,
            "speaker": "SPEAKER_00",
            "text": "Interim transcript text.",
            "provisional": True,
            "segment_source": "live",
        }
    ]
    meeting_edge_payload = {"summary": "Ask about the missing owner."}

    async with test_session_maker() as session:
        await session.execute(
            text(
                """
                UPDATE transcripts
                SET text = :text,
                    segments = :segments,
                    meeting_edge_payload = :meeting_edge_payload,
                    meeting_edge_status = 'ready'
                WHERE recording_id IN (10, 11)
                """
            ),
            {
                "text": "Interim transcript text.",
                "segments": json.dumps(transcript_segments),
                "meeting_edge_payload": json.dumps(meeting_edge_payload),
            },
        )
        await session.commit()

    live_response = await client.get(
        "/api/v1/recordings/recording-live",
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert live_response.status_code == 200
    live_payload = live_response.json()
    assert live_payload["status"] == "UPLOADING"
    assert live_payload["transcript"]["text"] == ""
    assert live_payload["transcript"]["segments"] == []
    assert live_payload["transcript"]["meeting_edge_status"] == "ready"
    assert live_payload["transcript"]["meeting_edge_payload"] == meeting_edge_payload

    processed_response = await client.get(
        "/api/v1/recordings/recording-final",
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert processed_response.status_code == 200
    processed_payload = processed_response.json()
    assert processed_payload["status"] == "PROCESSED"
    assert processed_payload["transcript"]["text"] == "Interim transcript text."
    assert (
        processed_payload["transcript"]["segments"][0]["text"]
        == "Interim transcript text."
    )


# Issue #519: a capture whose tab closed without its pause arriving stays
# UPLOADING. Once it has been quiet past STALLED_CAPTURE_AFTER it is offered for
# resume, stop or discard; a capture still uploading never is.
QUIET_PAST_THRESHOLD = timedelta(minutes=3)
QUIET_BRIEFLY = timedelta(seconds=40)


async def _set_last_heard(
    session_maker: sessionmaker, public_id: str, *, quiet_for: timedelta, capture: bool
) -> datetime:
    from backend.utils.time import utc_now

    last_heard = utc_now() - quiet_for
    # Init and resume mark a live capture RECORDING; an import leaves it unset.
    client_status = "COALESCE(client_status, 'RECORDING')" if capture else "NULL"
    async with session_maker() as session:
        await session.execute(
            text(
                "UPDATE recordings SET last_activity_at = :last_heard, "
                f"client_status = {client_status} WHERE public_id = :public_id"
            ),
            {"last_heard": last_heard, "public_id": public_id},
        )
        await session.commit()
    return last_heard


async def insert_capture(
    session_maker: sessionmaker,
    *,
    recording_id: int,
    public_id: str,
    quiet_for: timedelta,
    status: str = "UPLOADING",
) -> datetime:
    """A live capture last heard from ``quiet_for`` ago."""
    await insert_recording(
        session_maker, recording_id=recording_id, public_id=public_id, status=status
    )
    return await _set_last_heard(
        session_maker, public_id, quiet_for=quiet_for, capture=True
    )


async def insert_import(
    session_maker: sessionmaker, *, recording_id: int, public_id: str
) -> None:
    """An import still receiving its bytes, which also sits in UPLOADING."""
    await insert_recording(
        session_maker,
        recording_id=recording_id,
        public_id=public_id,
        status="UPLOADING",
    )
    await _set_last_heard(
        session_maker, public_id, quiet_for=QUIET_PAST_THRESHOLD, capture=False
    )


def test_capture_looks_stalled_only_for_a_quiet_live_capture() -> None:
    from backend.models.recording import (
        STALLED_CAPTURE_AFTER,
        ClientStatus,
        Recording,
        RecordingStatus,
        capture_looks_stalled,
    )

    now = datetime(2026, 10, 22, 12, 0, 0)

    def capture(
        *,
        quiet_for: timedelta,
        status: RecordingStatus = RecordingStatus.UPLOADING,
        client_status: ClientStatus | None = ClientStatus.RECORDING,
    ) -> Recording:
        return Recording(
            name="Weekly sync",
            audio_path="/tmp/capture.wav",
            status=status,
            client_status=client_status,
            last_activity_at=now - quiet_for,
        )

    def stalled(recording: Recording) -> bool:
        return capture_looks_stalled(recording, now=now)

    assert not stalled(capture(quiet_for=QUIET_BRIEFLY))
    assert not stalled(capture(quiet_for=STALLED_CAPTURE_AFTER))
    assert stalled(capture(quiet_for=STALLED_CAPTURE_AFTER + timedelta(seconds=1)))
    # A pause that arrived is the ordinary paused lock, not a stall.
    assert not stalled(
        capture(
            quiet_for=QUIET_PAST_THRESHOLD,
            status=RecordingStatus.PAUSED,
            client_status=ClientStatus.PAUSED,
        )
    )
    # An import also sits in UPLOADING while its bytes arrive. It is no capture.
    assert not stalled(capture(quiet_for=QUIET_PAST_THRESHOLD, client_status=None))


async def _status_and_last_heard(
    session_maker: sessionmaker, public_id: str
) -> tuple[str, datetime]:
    async with session_maker() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status, last_activity_at FROM recordings "
                    "WHERE public_id = :public_id"
                ),
                {"public_id": public_id},
            )
        ).one()
    raw = row[1]
    return row[0], raw if isinstance(raw, datetime) else datetime.fromisoformat(raw)


@pytest.mark.anyio
async def test_unfinished_capture_listing_flags_only_a_quiet_capture(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    set_session_cookie(client)
    await insert_capture(
        test_session_maker,
        recording_id=3001,
        public_id="capture-tab-gone",
        quiet_for=QUIET_PAST_THRESHOLD,
    )
    await insert_capture(
        test_session_maker,
        recording_id=3002,
        public_id="capture-riding-out-an-outage",
        quiet_for=QUIET_BRIEFLY,
    )
    await insert_capture(
        test_session_maker,
        recording_id=3003,
        public_id="capture-paused",
        quiet_for=QUIET_PAST_THRESHOLD,
        status="PAUSED",
    )
    await insert_import(test_session_maker, recording_id=3004, public_id="slow-import")

    response = await client.get(
        "/api/v1/recordings",
        params=[("status", "PAUSED"), ("status", "UPLOADING"), ("user", "me")],
    )

    assert response.status_code == 200
    assert {row["id"]: row["capture_stalled"] for row in response.json()} == {
        "capture-tab-gone": True,
        "capture-riding-out-an-outage": False,
        "capture-paused": False,
        "slow-import": False,
    }


@pytest.mark.anyio
async def test_resume_takes_over_a_capture_whose_tab_is_gone(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    from backend.utils.time import utc_now

    set_session_cookie(client)
    public_id = "capture-tab-gone"
    await insert_capture(
        test_session_maker,
        recording_id=3101,
        public_id=public_id,
        quiet_for=QUIET_PAST_THRESHOLD,
    )

    response = await client.post(
        f"/api/v1/recordings/{public_id}/resume",
        headers={"Origin": TRUSTED_ORIGIN},
    )

    assert response.status_code == 200
    assert response.json() == {
        "recording_id": public_id,
        "status": "UPLOADING",
        "last_sequence": -1,
        "capture_epoch": 1,
    }
    status_value, last_heard = await _status_and_last_heard(
        test_session_maker, public_id
    )
    assert status_value == "UPLOADING"
    assert last_heard > utc_now() - timedelta(minutes=1)
    # Fresh activity: the capture is live again, so nothing else may take it.
    listing = await client.get(
        "/api/v1/recordings", params={"status": "UPLOADING", "user": "me"}
    )
    assert [row["capture_stalled"] for row in listing.json()] == [False]


@pytest.mark.anyio
async def test_resume_refuses_a_capture_that_is_still_uploading(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    """Another tab or device must never take over a capture that is uploading."""
    set_session_cookie(client)
    public_id = "capture-riding-out-an-outage"
    last_heard_before = await insert_capture(
        test_session_maker,
        recording_id=3201,
        public_id=public_id,
        quiet_for=QUIET_BRIEFLY,
    )

    response = await client.post(
        f"/api/v1/recordings/{public_id}/resume",
        headers={"Origin": TRUSTED_ORIGIN},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "This recording is still in progress and received audio in the last two minutes."
    )
    status_value, last_heard = await _status_and_last_heard(
        test_session_maker, public_id
    )
    assert (status_value, last_heard) == ("UPLOADING", last_heard_before)


@pytest.mark.anyio
async def test_resume_refuses_an_import_however_quiet(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    set_session_cookie(client)
    await insert_import(test_session_maker, recording_id=3301, public_id="slow-import")

    response = await client.post(
        "/api/v1/recordings/slow-import/resume",
        headers={"Origin": TRUSTED_ORIGIN},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Recording is not paused"


@pytest.mark.anyio
async def test_init_conflict_explains_a_capture_still_in_progress(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    from backend.api.v1.endpoints.recordings.constants import (
        ACTIVE_RECORDING_CONFLICT_MESSAGE,
        CAPTURE_IN_PROGRESS_CONFLICT_MESSAGE,
    )

    set_session_cookie(client)
    public_id = "capture-elsewhere"
    await insert_capture(
        test_session_maker,
        recording_id=3401,
        public_id=public_id,
        quiet_for=QUIET_BRIEFLY,
    )

    in_progress = await client.post(
        "/api/v1/recordings/init",
        params={"name": "Blocked meeting"},
        headers={"Origin": TRUSTED_ORIGIN},
    )

    assert in_progress.status_code == 409
    assert in_progress.json()["detail"] == {
        "code": "active_recording_exists",
        "message": CAPTURE_IN_PROGRESS_CONFLICT_MESSAGE,
        "recording_id": public_id,
        "status": "UPLOADING",
    }

    # Once stalled it is offered in the dialog, which the generic message suits.
    await _set_last_heard(
        test_session_maker, public_id, quiet_for=QUIET_PAST_THRESHOLD, capture=True
    )
    stalled = await client.post(
        "/api/v1/recordings/init",
        params={"name": "Blocked meeting"},
        headers={"Origin": TRUSTED_ORIGIN},
    )

    assert stalled.status_code == 409
    assert stalled.json()["detail"]["message"] == ACTIVE_RECORDING_CONFLICT_MESSAGE


# Issue #570: a resume that takes a capture over issues the next capture epoch,
# and the segments, pauses and finalize of the tab that held it are refused from
# then on, so that tab cannot overwrite what the new one records.
SUPERSEDED = {
    "code": "capture_superseded",
    "message": "This recording continued elsewhere: another tab or device resumed it.",
}


async def _post(client: AsyncClient, path: str, **kwargs):
    return await client.post(
        f"/api/v1/recordings/{path}", headers={"Origin": TRUSTED_ORIGIN}, **kwargs
    )


async def _upload(
    client: AsyncClient,
    recording_id: str,
    sequence: int,
    payload: bytes,
    *,
    epoch: int | None,
):
    params = {"sequence": sequence}
    if epoch is not None:
        params["epoch"] = epoch
    return await _post(
        client,
        f"{recording_id}/segment",
        params=params,
        files={"file": (f"{sequence}.wav", payload, "audio/wav")},
    )


async def _taken_over_capture(client: AsyncClient) -> str:
    """A capture tab A started and paused, then took over elsewhere at epoch 1."""
    set_session_cookie(client)
    init = await _post(client, "init", params={"name": "Weekly sync"})
    recording_id = init.json()["id"]
    assert (
        await _upload(client, recording_id, 0, make_wav_bytes(), epoch=0)
    ).status_code == 200
    assert (
        await _post(client, f"{recording_id}/pause", params={"epoch": 0})
    ).status_code == 200

    takeover = await _post(client, f"{recording_id}/resume")
    assert takeover.status_code == 200
    assert takeover.json()["capture_epoch"] == 1
    assert takeover.json()["last_sequence"] == 0
    return recording_id


async def _recording_row(session_maker: sessionmaker, public_id: str):
    async with session_maker() as session:
        return (
            await session.execute(
                text(
                    "SELECT id, status, client_status, capture_epoch FROM recordings "
                    "WHERE public_id = :public_id"
                ),
                {"public_id": public_id},
            )
        ).one()


@pytest.mark.anyio
async def test_a_segment_from_a_tab_whose_capture_was_taken_over_is_refused(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    tmp_path: Path,
) -> None:
    recording_id = await _taken_over_capture(client)
    new_tab_audio = make_wav_bytes(duration_s=0.5)
    old_tab_audio = make_wav_bytes(duration_s=0.25)

    new_tab = await _upload(client, recording_id, 1, new_tab_audio, epoch=1)
    old_tab = await _upload(client, recording_id, 1, old_tab_audio, epoch=0)

    assert new_tab.status_code == 200
    assert old_tab.status_code == 409
    assert old_tab.json()["detail"] == SUPERSEDED
    row = await _recording_row(test_session_maker, recording_id)
    upload_dir = _recording_temp_dir(tmp_path, row.id, create=False)
    # The new tab's segment stands, and the refused one left nothing behind.
    assert (upload_dir / "1.wav").read_bytes() == new_tab_audio
    assert sorted(path.name for path in upload_dir.iterdir()) == ["0.wav", "1.wav"]
    async with test_session_maker() as session:
        durations = (
            await session.execute(
                text(
                    "SELECT sequence_no, duration_ms FROM recording_audio_chunks "
                    "WHERE recording_id = :id ORDER BY sequence_no"
                ),
                {"id": row.id},
            )
        ).all()
    assert [tuple(duration) for duration in durations] == [(0, 250), (1, 500)]

    # A later segment from the old tab is refused the same way, before the new
    # tab reaches that sequence.
    ahead = await _upload(client, recording_id, 2, old_tab_audio, epoch=0)
    assert ahead.status_code == 409
    assert not (upload_dir / "2.wav").exists()


@pytest.mark.anyio
async def test_a_late_pause_from_a_superseded_tab_is_refused(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    recording_id = await _taken_over_capture(client)

    late_pause = await _post(client, f"{recording_id}/pause", params={"epoch": 0})

    assert late_pause.status_code == 409
    assert late_pause.json()["detail"] == SUPERSEDED
    row = await _recording_row(test_session_maker, recording_id)
    assert (row.status, row.client_status, row.capture_epoch) == (
        "UPLOADING",
        "RECORDING",
        1,
    )
    # The tab now holding it pauses as usual.
    own_pause = await _post(client, f"{recording_id}/pause", params={"epoch": 1})
    assert own_pause.status_code == 200
    assert own_pause.json()["capture_epoch"] == 1


@pytest.mark.anyio
async def test_a_stop_from_a_superseded_tab_does_not_finalize(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    recording_id = await _taken_over_capture(client)

    stale_stop = await _post(client, f"{recording_id}/finalize", params={"epoch": 0})

    assert stale_stop.status_code == 409
    assert stale_stop.json()["detail"] == SUPERSEDED
    row = await _recording_row(test_session_maker, recording_id)
    assert row.status == "UPLOADING"
    # Stop from a dialog acts for no tab, and finalizes whoever holds it.
    dialog_stop = await _post(client, f"{recording_id}/finalize")
    assert dialog_stop.status_code == 200
    assert dialog_stop.json()["status"] == "QUEUED"


@pytest.mark.anyio
async def test_a_superseded_tab_cannot_resume_its_old_capture(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    recording_id = await _taken_over_capture(client)
    # The new holder pauses, so only the epoch stands in the old tab's way.
    assert (
        await _post(client, f"{recording_id}/pause", params={"epoch": 1})
    ).status_code == 200

    stale_resume = await _post(client, f"{recording_id}/resume", params={"epoch": 0})

    assert stale_resume.status_code == 409
    assert stale_resume.json()["detail"] == SUPERSEDED
    row = await _recording_row(test_session_maker, recording_id)
    assert (row.status, row.capture_epoch) == ("PAUSED", 1)


@pytest.mark.anyio
async def test_the_holding_tab_resumes_its_capture_while_the_server_holds_it_uploading(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    """A pause that never arrived does not keep the holding tab from resuming."""
    set_session_cookie(client)
    public_id = "capture-pause-never-arrived"
    await insert_capture(
        test_session_maker,
        recording_id=3501,
        public_id=public_id,
        quiet_for=QUIET_BRIEFLY,
    )

    takeover = await _post(client, f"{public_id}/resume")
    holder = await _post(client, f"{public_id}/resume", params={"epoch": 0})

    # Nobody else may take a capture that is still uploading.
    assert takeover.status_code == 409
    assert holder.status_code == 200
    assert holder.json()["capture_epoch"] == 0
    row = await _recording_row(test_session_maker, public_id)
    assert (row.status, row.client_status, row.capture_epoch) == (
        "UPLOADING",
        "RECORDING",
        0,
    )


@pytest.mark.anyio
async def test_the_holding_tab_cannot_resume_a_closed_recording(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    set_session_cookie(client)
    await insert_recording(
        test_session_maker, recording_id=3601, public_id="queued", status="QUEUED"
    )
    await insert_import(test_session_maker, recording_id=3602, public_id="slow-import")

    closed = await _post(client, "queued/resume", params={"epoch": 0})
    an_import = await _post(client, "slow-import/resume", params={"epoch": 0})

    assert closed.status_code == 409
    assert closed.json()["detail"] == "Recording is no longer accepting capture uploads"
    assert an_import.status_code == 409
    assert an_import.json()["detail"] == "Recording is not paused"


@pytest.mark.anyio
async def test_a_tab_cannot_discard_its_capture_once_it_was_stopped_elsewhere(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stop from a dialog issues no epoch, so only the status fences the old tab."""
    from backend.api.v1.endpoints import recordings as recordings_module

    monkeypatch.setattr(
        recordings_module.celery_app.control, "revoke", lambda *args, **kwargs: None
    )
    set_session_cookie(client)
    init = await _post(client, "init", params={"name": "Weekly sync"})
    recording_id = init.json()["id"]
    assert (
        await _upload(client, recording_id, 0, make_wav_bytes(), epoch=0)
    ).status_code == 200
    assert (
        await _post(client, f"{recording_id}/pause", params={"epoch": 0})
    ).status_code == 200
    assert (await _post(client, f"{recording_id}/finalize")).status_code == 200

    stale_discard = await _post(client, f"{recording_id}/discard", params={"epoch": 0})
    stale_segment = await _upload(client, recording_id, 1, make_wav_bytes(), epoch=0)

    closed = "Recording is no longer accepting capture uploads"
    assert stale_discard.status_code == 409
    assert stale_discard.json()["detail"] == closed
    assert stale_segment.status_code == 409
    assert stale_segment.json()["detail"] == closed
    row = await _recording_row(test_session_maker, recording_id)
    assert (row.status, row.capture_epoch) == ("QUEUED", 0)
    # The list and the dialogs discard without an epoch, and still can.
    assert (await _post(client, f"{recording_id}/discard")).status_code == 200


@pytest.mark.anyio
async def test_a_tab_on_an_older_build_sends_no_epoch_and_is_not_checked(
    client: AsyncClient,
) -> None:
    """Without an epoch nothing is fenced, as before epochs existed."""
    recording_id = await _taken_over_capture(client)

    segment = await _upload(client, recording_id, 1, make_wav_bytes(), epoch=None)
    pause = await _post(client, f"{recording_id}/pause")

    assert segment.status_code == 200
    assert pause.status_code == 200
    assert pause.json()["capture_epoch"] == 1


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["no_objections", "objected"])
async def test_consent_notice_answer_is_durable_and_does_not_stop_capture(
    client: AsyncClient, test_session_maker: sessionmaker, outcome: str
) -> None:
    set_session_cookie(client)
    initial = await client.post(
        "/api/v1/recordings/init", headers={"Origin": TRUSTED_ORIGIN}
    )
    assert initial.status_code == 200
    assert initial.json()["consent_notice_outcome"] == "no_answer"
    recording_id = initial.json()["id"]
    response = await client.patch(
        f"/api/v1/recordings/{recording_id}",
        json={"consent_notice_outcome": outcome},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert response.status_code == 200
    assert response.json()["consent_notice_outcome"] == outcome
    async with test_session_maker() as session:
        row = (
            await session.execute(
                text(
                    "SELECT consent_notice_outcome, status, client_status FROM recordings WHERE public_id = :id"
                ),
                {"id": recording_id},
            )
        ).one()
    assert tuple(row) == (outcome, "UPLOADING", "RECORDING")
    read = await client.get(f"/api/v1/recordings/{recording_id}")
    assert read.status_code == 200
    assert read.json()["consent_notice_outcome"] == outcome


@pytest.mark.anyio
async def test_consent_notice_suppression_applies_only_to_the_initiating_user(
    client: AsyncClient, api_app: FastAPI, test_session_maker: sessionmaker
) -> None:
    user = build_test_user()
    user.settings = {"hide_recording_consent_notice": True}
    api_app.dependency_overrides[deps.get_current_recording_client_user] = lambda: user
    first = await client.post("/api/v1/recordings/init")
    assert first.status_code == 200
    assert first.json()["consent_notice_outcome"] == "not_shown"
    user.id = 2
    user.settings = {}
    second = await client.post("/api/v1/recordings/init")
    assert second.status_code == 200
    assert second.json()["consent_notice_outcome"] == "no_answer"
    async with test_session_maker() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT user_id, consent_notice_outcome FROM recordings ORDER BY user_id"
                )
            )
        ).all()
    assert [tuple(row) for row in rows] == [(1, "not_shown"), (2, "no_answer")]


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["assumed", "no_answer", "not_shown"])
async def test_consent_notice_rejects_invalid_answers_and_other_users(
    client: AsyncClient, api_app: FastAPI, outcome: str
) -> None:
    set_session_cookie(client)
    initial = await client.post(
        "/api/v1/recordings/init", headers={"Origin": TRUSTED_ORIGIN}
    )
    url = f"/api/v1/recordings/{initial.json()['id']}"
    invalid = await client.patch(
        url,
        json={"consent_notice_outcome": outcome},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert invalid.status_code == 422
    other_user = build_test_user()
    other_user.id = 2
    api_app.dependency_overrides[deps.get_current_user] = lambda: other_user
    denied = await client.patch(url, json={"consent_notice_outcome": "no_objections"})
    assert denied.status_code == 404


@pytest.mark.anyio
async def test_consent_notice_cannot_be_answered_once_capture_has_ended(
    client: AsyncClient, test_session_maker: sessionmaker
) -> None:
    set_session_cookie(client)
    initial = await client.post(
        "/api/v1/recordings/init", headers={"Origin": TRUSTED_ORIGIN}
    )
    recording_id = initial.json()["id"]
    async with test_session_maker() as session:
        await session.execute(
            text(
                "UPDATE recordings SET status = 'PROCESSED', client_status = 'IDLE' "
                "WHERE public_id = :id"
            ),
            {"id": recording_id},
        )
        await session.commit()
    response = await client.patch(
        f"/api/v1/recordings/{recording_id}",
        json={"consent_notice_outcome": "no_objections"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert response.status_code == 409
    read = await client.get(f"/api/v1/recordings/{recording_id}")
    assert read.json()["consent_notice_outcome"] == "no_answer"
