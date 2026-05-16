"""Tests for the reprocess endpoint and per-reprocess engine override."""

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
    recording_id: int = 201,
    public_id: str = "reprocess-rec-public-id",
    user_id: int = 1,
    status: str = "PROCESSED",
) -> None:
    """Insert a single recording row into the test DB."""
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    is_archived, is_deleted, user_id
                ) VALUES (
                    :id, :now, :now, :name, :public_id, :meeting_uid,
                    :audio_path, :status, 0, 100, 0, 0, :user_id
                )
                """
            ),
            {
                "id": recording_id,
                "now": "2026-05-16 00:00:00",
                "name": "Processed meeting",
                "public_id": public_id,
                "meeting_uid": f"meeting-uid-{recording_id}",
                "audio_path": "/tmp/recording.wav",
                "status": status,
                "user_id": user_id,
            },
        )
        await session.commit()


def _patch_delay(monkeypatch):
    """Patch process_recording_task.delay; return the captured-calls list."""
    from backend.api.v1.endpoints import recordings as recordings_module

    calls: list = []

    class _FakeTask:
        id = "fake-task-id"

    monkeypatch.setattr(
        recordings_module.process_recording_task,
        "delay",
        lambda *args, **kwargs: (calls.append((args, kwargs)), _FakeTask())[1],
    )
    return calls


# --- endpoint tests ---------------------------------------------------------


@pytest.mark.anyio
async def test_reprocess_processed_recording_queues_with_override(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    monkeypatch,
) -> None:
    """POST /reprocess on a PROCESSED recording queues it with an engine override."""
    await _insert_recording(test_session_maker, recording_id=201)
    calls = _patch_delay(monkeypatch)

    response = await client.post(
        "/api/v1/recordings/reprocess-rec-public-id/reprocess",
        json={"transcription_backend": "parakeet"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "QUEUED"
    assert len(calls) == 1
    assert calls[0][0] == (201, True, {"transcription_backend": "parakeet"})


@pytest.mark.anyio
async def test_reprocess_invalid_backend_rejected(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    monkeypatch,
) -> None:
    """An unknown transcription_backend is rejected with 400."""
    await _insert_recording(test_session_maker, recording_id=202, public_id="rec-202")
    calls = _patch_delay(monkeypatch)

    response = await client.post(
        "/api/v1/recordings/rec-202/reprocess",
        json={"transcription_backend": "foobar"},
    )

    assert response.status_code == 400
    assert calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["UPLOADING", "QUEUED", "PROCESSING"])
async def test_reprocess_in_progress_recording_rejected(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    monkeypatch,
    status: str,
) -> None:
    """Reprocessing a recording that is already in progress is rejected with 400."""
    await _insert_recording(
        test_session_maker,
        recording_id=203,
        public_id="rec-203",
        status=status,
    )
    calls = _patch_delay(monkeypatch)

    response = await client.post(
        "/api/v1/recordings/rec-203/reprocess",
        json={"transcription_backend": "whisper"},
    )

    assert response.status_code == 400
    assert calls == []


@pytest.mark.anyio
async def test_reprocess_other_users_recording_rejected(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    monkeypatch,
) -> None:
    """Reprocessing a recording owned by another user is rejected (404)."""
    await _insert_recording(
        test_session_maker,
        recording_id=204,
        public_id="rec-204",
        user_id=999,
    )
    calls = _patch_delay(monkeypatch)

    response = await client.post(
        "/api/v1/recordings/rec-204/reprocess",
        json={"transcription_backend": "whisper"},
    )

    assert response.status_code == 404
    assert calls == []


@pytest.mark.anyio
async def test_reprocess_optional_model_keys_only_when_provided(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    monkeypatch,
) -> None:
    """whisper_model_size / parakeet_model land in the override only when provided."""
    await _insert_recording(test_session_maker, recording_id=205, public_id="rec-205")
    calls = _patch_delay(monkeypatch)

    response = await client.post(
        "/api/v1/recordings/rec-205/reprocess",
        json={
            "transcription_backend": "whisper",
            "whisper_model_size": "large-v3",
        },
    )

    assert response.status_code == 200
    assert len(calls) == 1
    override = calls[0][0][2]
    assert override == {
        "transcription_backend": "whisper",
        "whisper_model_size": "large-v3",
    }
    assert "parakeet_model" not in override


@pytest.mark.anyio
async def test_retry_still_dispatches_without_override(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    monkeypatch,
) -> None:
    """Regression: /retry still dispatches process_recording_task with (id, True, None)."""
    await _insert_recording(test_session_maker, recording_id=206, public_id="rec-206")
    calls = _patch_delay(monkeypatch)

    response = await client.post("/api/v1/recordings/rec-206/retry")

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0][0] == (206, True, None)


# --- task unit test ---------------------------------------------------------


def test_process_recording_task_applies_engine_override(monkeypatch):
    """process_recording_task merges engine_override into merged_config before
    the transcription stage, so the engine keys reach transcribe_audio."""
    from backend.models.recording import RecordingStatus
    from backend.worker import tasks as tasks_module

    captured: dict = {}

    # Stop the pipeline right after the transcription stage by raising from a
    # later mocked call; we only need to observe what transcribe_audio saw.
    class _StopPipeline(Exception):
        pass

    def fake_transcribe_audio(path, config=None):
        captured["config"] = dict(config or {})
        raise _StopPipeline()

    base_config = {
        "transcription_backend": "whisper",
        "whisper_model_size": "base",
        "enable_vad": False,
        "enable_diarization": False,
    }

    class _FakeLlmConfig:
        merged_config = dict(base_config)

    monkeypatch.setattr(
        tasks_module, "resolve_llm_config", lambda *a, **k: _FakeLlmConfig()
    )

    class _FakeRecording:
        id = 301
        status = RecordingStatus.PROCESSED
        user_id = None
        audio_path = "/tmp/recording.wav"
        proxy_path = None
        duration_seconds = 60.0
        processing_started_at = None
        processing_completed_at = None
        processing_progress = 0
        processing_step = ""

    class _FakeSession:
        def get(self, model, recording_id):
            return _FakeRecording()

        def add(self, obj):
            pass

        def commit(self):
            pass

        def refresh(self, obj):
            pass

        def exec(self, *a, **k):
            raise _StopPipeline()

        def close(self):
            pass

    monkeypatch.setattr(tasks_module.config_manager, "reload", lambda: None)
    monkeypatch.setattr(
        tasks_module.config_manager,
        "get",
        lambda key, default=None: default,
    )

    fake_session = _FakeSession()

    # Patch the heavy processing imports the task pulls in lazily.
    import sys
    import types

    def _install(module_name: str, **attrs):
        mod = types.ModuleType(module_name)
        for name, value in attrs.items():
            setattr(mod, name, value)
        monkeypatch.setitem(sys.modules, module_name, mod)

    def _ok_preprocess(path):
        return "/tmp/recording_vad.wav"

    _install(
        "backend.processing.vad",
        mute_non_speech_segments=lambda *a, **k: (True, 10.0),
    )
    _install(
        "backend.processing.audio_preprocessing",
        convert_wav_to_mp3=lambda *a, **k: None,
        preprocess_audio_for_vad=_ok_preprocess,
        validate_audio_file=lambda *a, **k: None,
        cleanup_temp_file=lambda *a, **k: None,
        repair_audio_file=lambda *a, **k: None,
    )
    _install("backend.processing.transcribe", transcribe_audio=fake_transcribe_audio)
    _install("backend.processing.diarize", diarize_audio=lambda *a, **k: None)
    _install("backend.processing.embedding_core", extract_embeddings=lambda *a, **k: None)
    _install(
        "backend.processing.embedding",
        cosine_similarity=lambda *a, **k: 0.0,
        merge_embeddings=lambda *a, **k: None,
        find_matching_global_speaker=lambda *a, **k: None,
        AUTO_UPDATE_THRESHOLD=0.8,
    )
    _install(
        "backend.utils.transcript_utils",
        combine_transcription_diarization=lambda *a, **k: [],
        consolidate_diarized_transcript=lambda *a, **k: [],
    )
    _install(
        "backend.utils.audio",
        get_audio_duration=lambda *a, **k: 60.0,
        convert_to_mp3=lambda *a, **k: None,
        convert_to_proxy_mp3=lambda *a, **k: None,
    )
    _install("backend.processing.llm_services", get_llm_backend=lambda *a, **k: None)

    monkeypatch.setattr("os.path.exists", lambda path: True)

    engine_override = {"transcription_backend": "parakeet", "parakeet_model": "v3"}

    # process_recording_task is a bound Celery task: task.run carries `self`
    # implicitly. Inject the fake DB session and stub the progress callback.
    task = tasks_module.process_recording_task
    monkeypatch.setattr(task, "_session", fake_session, raising=False)
    monkeypatch.setattr(task, "update_state", lambda *a, **k: None, raising=False)

    with pytest.raises(_StopPipeline):
        task.run(301, False, engine_override)

    assert captured["config"]["transcription_backend"] == "parakeet"
    assert captured["config"]["parakeet_model"] == "v3"
