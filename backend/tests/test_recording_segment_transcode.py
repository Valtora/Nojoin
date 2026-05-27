from __future__ import annotations

import io
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

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
    last_error TEXT
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


def make_wav_bytes(*, duration_s: float = 0.25, sample_rate: int = 16000, channels: int = 1) -> bytes:
    frame_count = int(duration_s * sample_rate)
    pcm_frames = b"\x00\x00" * frame_count * channels
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_frames)
    return buffer.getvalue()


def test_ffmpeg_transcode_preserves_browser_source_channels(monkeypatch, tmp_path):
    from backend.processing.browser_live_audio import (
        BROWSER_LIVE_CHANNEL_COUNT,
        BROWSER_LIVE_SAMPLE_RATE_HZ,
    )
    from backend.processing import segment_transcode as segment_transcode_module

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(segment_transcode_module.subprocess, "run", fake_run)

    segment_transcode_module._run_ffmpeg_transcode(
        tmp_path / "0.webm",
        tmp_path / "0.wav",
    )

    command = captured["command"]
    assert command[command.index("-ar") + 1] == str(BROWSER_LIVE_SAMPLE_RATE_HZ)
    assert command[command.index("-ac") + 1] == str(BROWSER_LIVE_CHANNEL_COUNT)


def _recording_temp_dir(root: Path, recording_id: int, *, create: bool) -> Path:
    path = root / f"upload-{recording_id}"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def live_dispatches() -> list[tuple[int, int]]:
    return []


@pytest.fixture
def transcode_dispatches() -> list[tuple[int, int]]:
    return []


@pytest.fixture
def pipeline_metrics() -> list[dict[str, object]]:
    return []


@pytest.fixture
def sqlite_urls(tmp_path):
    db_path = tmp_path / "segment-transcode.sqlite3"
    return {
        "async": f"sqlite+aiosqlite:///{db_path}",
        "sync": f"sqlite:///{db_path}",
    }


@pytest.fixture
async def test_session_maker(sqlite_urls) -> sessionmaker:
    engine = create_async_engine(sqlite_urls["async"], future=True)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.execute(text(RECORDINGS_SCHEMA))
        await connection.execute(text(TRANSCRIPTS_SCHEMA))
        await connection.execute(text(RECORDING_AUDIO_CHUNKS_SCHEMA))
        await connection.execute(text(RECORDING_AUDIO_WINDOW_MANIFESTS_SCHEMA))

    try:
        yield session_maker
    finally:
        await engine.dispose()


@pytest.fixture
def sync_engine(sqlite_urls):
    engine = create_engine(sqlite_urls["sync"], future=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
async def api_app(
    monkeypatch,
    tmp_path,
    test_session_maker: sessionmaker,
    sync_engine,
    live_dispatches,
    transcode_dispatches,
    pipeline_metrics,
) -> FastAPI:
    monkeypatch.setenv("WEB_APP_URL", TRUSTED_ORIGIN)

    async def override_get_db():
        async with test_session_maker() as session:
            yield session

    fake_user = build_test_user()

    async def fake_get_authenticated_token_details(db, actual_token, *, allowed_token_types, required_scopes_by_type=None):
        if actual_token == "session-token":
            return fake_user, {"sub": fake_user.username, "token_type": "session", "scopes": ["session:web"]}
        raise AssertionError(f"Unexpected token: {actual_token}")

    async def fake_get_authenticated_user_from_token(db, actual_token, *, allowed_token_types, required_scopes_by_type=None):
        assert actual_token == "session-token"
        return fake_user

    from backend.api.v1.endpoints import recordings as recordings_module
    from backend.processing import segment_transcode as segment_transcode_module
    from backend.utils import recording_audio_sync, recording_storage

    monkeypatch.setattr(deps, "get_authenticated_token_details", fake_get_authenticated_token_details)
    monkeypatch.setattr(deps, "get_authenticated_user_from_token", fake_get_authenticated_user_from_token)
    monkeypatch.setattr(recordings_module, "recordings_root_dir", lambda: tmp_path)
    monkeypatch.setattr(
        recordings_module,
        "recording_upload_temp_dir",
        lambda recording_id, create=False: _recording_temp_dir(tmp_path, recording_id, create=create),
    )
    monkeypatch.setattr(
        recording_storage,
        "recordings_root_dir",
        lambda create=True: tmp_path,
    )
    monkeypatch.setattr(
        recording_storage,
        "recording_upload_temp_dir",
        lambda recording_id, create=False: _recording_temp_dir(tmp_path, recording_id, create=create),
    )
    monkeypatch.setattr(
        recording_audio_sync,
        "recording_upload_temp_dir",
        lambda recording_id, create=False: _recording_temp_dir(tmp_path, recording_id, create=create),
    )
    monkeypatch.setattr(
        segment_transcode_module,
        "recording_upload_temp_dir",
        lambda recording_id, create=False: _recording_temp_dir(tmp_path, recording_id, create=create),
    )
    monkeypatch.setattr(
        recordings_module.transcribe_segment_live_task,
        "delay",
        lambda recording_id, sequence: live_dispatches.append((recording_id, sequence)),
    )
    monkeypatch.setattr(
        segment_transcode_module.transcribe_segment_live_task,
        "delay",
        lambda recording_id, sequence: live_dispatches.append((recording_id, sequence)),
    )
    monkeypatch.setattr(
        recordings_module.transcode_segment_task,
        "delay",
        lambda recording_id, sequence: transcode_dispatches.append((recording_id, sequence)),
    )
    monkeypatch.setattr(recordings_module.process_recording_task, "delay", lambda *args, **kwargs: SimpleNamespace(id="task-1"))
    monkeypatch.setattr(recordings_module.generate_proxy_task, "delay", lambda *args, **kwargs: None)
    monkeypatch.setattr(segment_transcode_module, "get_sync_session", lambda: Session(sync_engine))
    monkeypatch.setattr(
        segment_transcode_module,
        "record_pipeline_metric",
        lambda *, stage, recording_id, payload, log: pipeline_metrics.append(
            {
                "stage": stage,
                "recording_id": recording_id,
                "payload": payload,
            }
        ),
    )

    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    return app


@pytest.fixture
async def client(api_app: FastAPI) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=api_app),
        base_url=BASE_URL,
    ) as async_client:
        yield async_client


async def _lookup_internal_recording_id(
    session_maker: sessionmaker,
    *,
    public_id: str,
) -> int:
    async with session_maker() as session:
        row = (
            await session.execute(
                text("SELECT id FROM recordings WHERE public_id = :public_id"),
                {"public_id": public_id},
            )
        ).one()
    return int(row[0])


async def _chunk_rows_for_recording(
    session_maker: sessionmaker,
    *,
    recording_id: int,
) -> list[tuple[int, str]]:
    async with session_maker() as session:
        rows = await session.execute(
            text(
                "SELECT sequence_no, storage_path FROM recording_audio_chunks WHERE recording_id = :recording_id ORDER BY sequence_no"
            ),
            {"recording_id": recording_id},
        )
        return [(int(sequence_no), str(storage_path)) for sequence_no, storage_path in rows.all()]


async def _chunk_metadata_rows_for_recording(
    session_maker: sessionmaker,
    *,
    recording_id: int,
) -> list[tuple[int, int, int, str]]:
    async with session_maker() as session:
        rows = await session.execute(
            text(
                "SELECT sequence_no, sample_rate_hz, channel_count, storage_path FROM recording_audio_chunks WHERE recording_id = :recording_id ORDER BY sequence_no"
            ),
            {"recording_id": recording_id},
        )
        return [
            (int(sequence_no), int(sample_rate_hz), int(channel_count), str(storage_path))
            for sequence_no, sample_rate_hz, channel_count, storage_path in rows.all()
        ]


@pytest.mark.anyio
async def test_webm_upload_defers_live_sync_until_transcode_task(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    live_dispatches,
    transcode_dispatches,
    monkeypatch,
    tmp_path,
) -> None:
    from backend.processing.browser_live_audio import (
        BROWSER_LIVE_CHANNEL_COUNT,
        BROWSER_LIVE_SAMPLE_RATE_HZ,
    )

    set_session_cookie(client)

    init_response = await client.post(
        "/api/v1/recordings/init",
        params={"name": "Browser meeting"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert init_response.status_code == 200
    recording_public_id = init_response.json()["id"]
    recording_id = await _lookup_internal_recording_id(
        test_session_maker,
        public_id=recording_public_id,
    )

    upload_response = await client.post(
        f"/api/v1/recordings/{recording_public_id}/segment",
        params={"sequence": 0},
        headers={"Origin": TRUSTED_ORIGIN},
        files={"file": ("0.webm", b"webm-opus-segment", "audio/webm")},
    )

    assert upload_response.status_code == 200
    assert live_dispatches == []
    assert transcode_dispatches == [(recording_id, 0)]
    assert await _chunk_rows_for_recording(test_session_maker, recording_id=recording_id) == []

    upload_dir = _recording_temp_dir(tmp_path, recording_id, create=False)

    from backend.processing import segment_transcode as segment_transcode_module

    monkeypatch.setattr(
        segment_transcode_module,
        "_run_ffmpeg_transcode",
        lambda input_path, output_path: output_path.write_bytes(
            make_wav_bytes(channels=BROWSER_LIVE_CHANNEL_COUNT)
        ),
    )

    result = segment_transcode_module.transcode_segment_task.run(recording_id, 0)

    wav_path = upload_dir / "0.wav"
    webm_path = upload_dir / "0.webm"

    assert result == {"status": "received", "segment": 0}
    assert wav_path.exists()
    assert webm_path.exists()
    with wave.open(str(wav_path), "rb") as wav_file:
        assert wav_file.getframerate() == BROWSER_LIVE_SAMPLE_RATE_HZ
        assert wav_file.getnchannels() == BROWSER_LIVE_CHANNEL_COUNT
    assert live_dispatches == [(recording_id, 0)]

    chunk_rows = await _chunk_rows_for_recording(test_session_maker, recording_id=recording_id)
    assert chunk_rows == [(0, str(wav_path))]
    assert await _chunk_metadata_rows_for_recording(test_session_maker, recording_id=recording_id) == [
        (0, BROWSER_LIVE_SAMPLE_RATE_HZ, BROWSER_LIVE_CHANNEL_COUNT, str(wav_path))
    ]


@pytest.mark.anyio
async def test_finalize_upload_transcodes_pending_browser_segments(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    transcode_dispatches,
    monkeypatch,
    tmp_path,
) -> None:
    from backend.processing.browser_live_audio import BROWSER_LIVE_CHANNEL_COUNT
    from backend.api.v1.endpoints import recordings as recordings_module

    set_session_cookie(client)

    init_response = await client.post(
        "/api/v1/recordings/init",
        params={"name": "Finalize browser meeting"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert init_response.status_code == 200
    recording_public_id = init_response.json()["id"]
    recording_id = await _lookup_internal_recording_id(
        test_session_maker,
        public_id=recording_public_id,
    )

    upload_response = await client.post(
        f"/api/v1/recordings/{recording_public_id}/segment",
        params={"sequence": 0},
        headers={"Origin": TRUSTED_ORIGIN},
        files={"file": ("0.webm", b"webm-opus-segment", "audio/webm")},
    )
    assert upload_response.status_code == 200
    assert transcode_dispatches == [(recording_id, 0)]
    assert await _chunk_rows_for_recording(test_session_maker, recording_id=recording_id) == []

    from backend.processing import segment_transcode as segment_transcode_module

    monkeypatch.setattr(
        segment_transcode_module,
        "_run_ffmpeg_transcode",
        lambda input_path, output_path: output_path.write_bytes(
            make_wav_bytes(channels=BROWSER_LIVE_CHANNEL_COUNT)
        ),
    )
    monkeypatch.setattr(
        recordings_module,
        "concatenate_media_files",
        lambda paths, destination: Path(destination).write_bytes(b"joined-browser-master"),
    )
    monkeypatch.setattr(recordings_module, "get_audio_duration", lambda path: 1.25)
    monkeypatch.setattr(recordings_module, "_enforce_lossy_audio_bitrate_floor", lambda path: None)

    finalize_response = await client.post(
        f"/api/v1/recordings/{recording_public_id}/finalize",
        headers={"Origin": TRUSTED_ORIGIN},
    )

    upload_dir = _recording_temp_dir(tmp_path, recording_id, create=False)
    wav_path = upload_dir / "0.wav"

    assert finalize_response.status_code == 200
    assert finalize_response.json()["status"] == "QUEUED"
    assert wav_path.exists()
    assert (upload_dir / "0.webm").exists()
    assert await _chunk_rows_for_recording(test_session_maker, recording_id=recording_id) == [
        (0, str(wav_path))
    ]

    async with test_session_maker() as session:
        recording_row = (
            await session.execute(
                text("SELECT audio_path, proxy_path FROM recordings WHERE id = :recording_id"),
                {"recording_id": recording_id},
            )
        ).one()

    assert recording_row[0].endswith(".webm")
    assert Path(recording_row[0]).exists()
    assert recording_row[1].endswith(".mp3")


@pytest.mark.anyio
async def test_failed_webm_transcode_marks_sequence_incomplete_for_finalize(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    live_dispatches,
    pipeline_metrics,
    monkeypatch,
    tmp_path,
) -> None:
    set_session_cookie(client)

    init_response = await client.post(
        "/api/v1/recordings/init",
        params={"name": "Broken browser meeting"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert init_response.status_code == 200
    recording_public_id = init_response.json()["id"]
    recording_id = await _lookup_internal_recording_id(
        test_session_maker,
        public_id=recording_public_id,
    )

    upload_response = await client.post(
        f"/api/v1/recordings/{recording_public_id}/segment",
        params={"sequence": 0},
        headers={"Origin": TRUSTED_ORIGIN},
        files={"file": ("0.webm", b"corrupted-webm", "audio/webm")},
    )
    assert upload_response.status_code == 200

    from backend.processing import segment_transcode as segment_transcode_module

    monkeypatch.setattr(
        segment_transcode_module,
        "_run_ffmpeg_transcode",
        lambda input_path, output_path: (_ for _ in ()).throw(RuntimeError("broken container")),
    )

    result = segment_transcode_module.transcode_segment_task.run(recording_id, 0)
    upload_dir = _recording_temp_dir(tmp_path, recording_id, create=False)
    failure_marker = upload_dir / "0.transcode_failed"

    assert result == {"status": "failed", "segment": 0}
    assert failure_marker.exists()
    assert not (upload_dir / "0.wav").exists()
    assert live_dispatches == []
    assert await _chunk_rows_for_recording(test_session_maker, recording_id=recording_id) == []
    assert pipeline_metrics == [
        {
            "stage": "segment_transcode_failed",
            "recording_id": recording_id,
            "payload": {
                "sequence": 0,
                "input_path": str(upload_dir / "0.webm"),
                "output_path": str(upload_dir / "0.wav"),
                "error": "broken container",
            },
        }
    ]

    finalize_response = await client.post(
        f"/api/v1/recordings/{recording_public_id}/finalize",
        headers={"Origin": TRUSTED_ORIGIN},
    )

    assert finalize_response.status_code == 409
    assert finalize_response.json()["detail"] == (
        "Recording upload is still in progress; finalize after all segment uploads complete."
    )


@pytest.mark.anyio
async def test_wav_upload_keeps_existing_fast_path(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    live_dispatches,
    transcode_dispatches,
    tmp_path,
) -> None:
    set_session_cookie(client)

    init_response = await client.post(
        "/api/v1/recordings/init",
        params={"name": "WAV meeting"},
        headers={"Origin": TRUSTED_ORIGIN},
    )
    assert init_response.status_code == 200
    recording_public_id = init_response.json()["id"]
    recording_id = await _lookup_internal_recording_id(
        test_session_maker,
        public_id=recording_public_id,
    )

    upload_response = await client.post(
        f"/api/v1/recordings/{recording_public_id}/segment",
        params={"sequence": 0},
        headers={"Origin": TRUSTED_ORIGIN},
        files={"file": ("0.wav", make_wav_bytes(), "audio/wav")},
    )

    assert upload_response.status_code == 200
    assert transcode_dispatches == []
    assert live_dispatches == [(recording_id, 0)]

    upload_dir = _recording_temp_dir(tmp_path, recording_id, create=False)
    assert (upload_dir / "0.wav").exists()

    chunk_rows = await _chunk_rows_for_recording(test_session_maker, recording_id=recording_id)
    assert chunk_rows == [(0, str(upload_dir / "0.wav"))]


def test_transcode_task_registered_with_celery() -> None:
    from backend.celery_app import celery_app

    celery_app.loader.import_default_modules()

    assert (
        "backend.processing.segment_transcode.transcode_segment_task"
        in celery_app.tasks
    )