"""Tests for the reprocess endpoint and per-reprocess engine override."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from pathlib import Path

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
    processing_run_id INTEGER,
    last_speaker_correction_event_id INTEGER,
    last_diarization_window_result_id INTEGER,
    snippet_start FLOAT,
    snippet_end FLOAT,
    voice_snippet_path VARCHAR(1024)
)
"""

RECORDING_AUDIO_CHUNKS_SCHEMA = """
CREATE TABLE recording_audio_chunks (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    public_id VARCHAR(36),
    recording_id INTEGER NOT NULL,
    sequence_no INTEGER NOT NULL,
    source_kind VARCHAR(64) NOT NULL,
    absolute_start_ms INTEGER NOT NULL,
    absolute_end_ms INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    sample_rate_hz INTEGER NOT NULL,
    channel_count INTEGER NOT NULL,
    byte_size INTEGER NOT NULL,
    sha256 VARCHAR(128) NOT NULL,
    storage_path TEXT NOT NULL,
    upload_status VARCHAR(32),
    idempotency_key VARCHAR(255),
    received_at DATETIME,
    cleanup_eligible_at DATETIME
)
"""

RECORDING_AUDIO_WINDOW_MANIFESTS_SCHEMA = """
CREATE TABLE recording_audio_window_manifests (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    public_id VARCHAR(36),
    recording_id INTEGER NOT NULL,
    window_index INTEGER NOT NULL,
    source_kind VARCHAR(64) NOT NULL,
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
        await connection.execute(text(RECORDING_AUDIO_CHUNKS_SCHEMA))
        await connection.execute(text(RECORDING_AUDIO_WINDOW_MANIFESTS_SCHEMA))

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
    pipeline_generation: str = "unified",
) -> None:
    """Insert a single recording row into the test DB."""
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    pipeline_generation, is_archived, is_deleted, user_id
                ) VALUES (
                    :id, :now, :now, :name, :public_id, :meeting_uid,
                    :audio_path, :status, 0, 100, :pipeline_generation, 0, 0, :user_id
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
                "pipeline_generation": pipeline_generation,
                "user_id": user_id,
            },
        )
        await session.commit()


def _patch_delay(monkeypatch):
    """Patch celery_app.send_task; return the captured-calls list."""
    from backend.api.v1.endpoints import recordings as recordings_module

    calls: list = []

    class _FakeTask:
        id = "fake-task-id"

    def fake_send_task(name, args=None, kwargs=None, **other_kwargs):
        if name == "backend.worker.tasks.process_recording_task":
            calls.append((tuple(args or []), kwargs or {}))
        return _FakeTask()

    monkeypatch.setattr(recordings_module.celery_app, "send_task", fake_send_task)
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


@pytest.mark.anyio
async def test_reprocess_promotes_legacy_recording_to_unified(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    monkeypatch,
) -> None:
    await _insert_recording(
        test_session_maker,
        recording_id=207,
        public_id="rec-207",
        pipeline_generation="legacy_backfilled",
    )
    calls = _patch_delay(monkeypatch)

    response = await client.post(
        "/api/v1/recordings/rec-207/reprocess",
        json={"transcription_backend": "whisper"},
    )

    assert response.status_code == 200
    assert len(calls) == 1

    async with test_session_maker() as session:
        generation = (
            await session.execute(
                text("SELECT pipeline_generation FROM recordings WHERE id = 207")
            )
        ).scalar_one()

    assert generation == "unified"


# --- task unit test ---------------------------------------------------------


def test_process_recording_task_applies_engine_override(monkeypatch):
    """process_recording_task merges engine_override into merged_config before
    the transcription stage, so the engine keys reach transcribe_audio."""
    from backend.models.recording import RecordingStatus
    from backend.worker import tasks as tasks_module

    captured: dict = {}

    # Stop the pipeline right after the transcription stage by raising from a
    # later mocked call; we only need to observe what transcribe_audio saw.
    class _StopPipeline(BaseException):
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
        lambda key, default=None: True if key == "keep_models_loaded" else default,
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
        extract_audio_clip=lambda *a, **k: None,
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


@pytest.mark.skip(reason="Simplified pipeline: catch-up diarization and live reuse removed from finalization")
def test_process_recording_task_runs_catch_up_diarization_before_promotion(monkeypatch):
    """Pending live manifests trigger catch-up diarization before final promotion continues."""
    from types import SimpleNamespace

    from backend.models.recording import RecordingStatus
    from backend.worker import tasks as tasks_module

    captured: dict = {}

    class _StopPipeline(BaseException):
        pass

    base_config = {
        "transcription_backend": "whisper",
        "whisper_model_size": "base",
        "enable_vad": False,
        "enable_diarization": True,
    }

    class _FakeLlmConfig:
        merged_config = dict(base_config)

    monkeypatch.setattr(
        tasks_module, "resolve_llm_config", lambda *a, **k: _FakeLlmConfig()
    )

    class _FakeTranscript:
        segments = [
            {
                "start": 0.0,
                "end": 1.0,
                "speaker": "LIVE_01",
                "text": "hello",
                "segment_source": "live",
            }
        ]

    class _ExecResult:
        def first(self):
            return _FakeTranscript()

    class _FakeRecording:
        id = 401
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
            return _ExecResult()

        def close(self):
            pass

    monkeypatch.setattr(tasks_module.config_manager, "reload", lambda: None)
    monkeypatch.setattr(
        tasks_module.config_manager,
        "get",
        lambda key, default=None: True if key == "keep_models_loaded" else default,
    )

    fake_session = _FakeSession()

    import sys
    import types

    def _install(module_name: str, **attrs):
        mod = types.ModuleType(module_name)
        for name, value in attrs.items():
            setattr(mod, name, value)
        monkeypatch.setitem(sys.modules, module_name, mod)

    _install(
        "backend.processing.vad",
        mute_non_speech_segments=lambda *a, **k: (True, 10.0),
    )
    _install(
        "backend.processing.audio_preprocessing",
        convert_wav_to_mp3=lambda *a, **k: None,
        preprocess_audio_for_vad=lambda path: "/tmp/recording_vad.wav",
        validate_audio_file=lambda *a, **k: None,
        cleanup_temp_file=lambda *a, **k: None,
        repair_audio_file=lambda *a, **k: None,
    )
    _install(
        "backend.processing.transcribe",
        transcribe_audio=lambda *a, **k: {"text": "hello", "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}]},
        release_model_cache=lambda: None,
    )
    _install(
        "backend.processing.diarize",
        diarize_audio=lambda *a, **k: None,
        release_pipeline_cache=lambda: None,
    )
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
        extract_audio_clip=lambda *a, **k: None,
    )
    _install(
        "backend.utils.live_transcript",
        apply_live_authority_to_segments=lambda live, combined: combined,
        build_transcription_result_from_segments=lambda segments: ({"text": "hello", "segments": []}, []),
        merge_reusable_segments=lambda primary, additional: list(primary) + list(additional),
        map_final_speakers_to_live_labels=lambda *a, **k: {},
    )
    _install("backend.processing.llm_services", get_llm_backend=lambda *a, **k: None)
    _install("backend.processing.text_embedding", release_embedding_model=lambda: None)

    monkeypatch.setattr("os.path.exists", lambda path: True)
    monkeypatch.setattr(
        tasks_module,
        "_load_recording_audio_window_manifests",
        lambda *a, **k: [SimpleNamespace(id=11)],
    )
    monkeypatch.setattr(
        tasks_module,
        "_load_recording_audio_chunks",
        lambda *a, **k: [],
    )
    monkeypatch.setattr(
        tasks_module,
        "collect_pending_chunk_spans",
        lambda *a, **k: [SimpleNamespace(window_id=11)],
    )
    monkeypatch.setattr(
        tasks_module,
        "build_reusable_live_segments",
        lambda *a, **k: [
            {
                "id": "canon-live-1",
                "start": 0.0,
                "end": 1.0,
                "speaker": "LIVE_01",
                "text": "hello",
                "segment_source": "live",
            }
        ],
    )
    monkeypatch.setattr(
        tasks_module,
        "_build_catch_up_segments",
        lambda **kwargs: ([], {11}, SimpleNamespace(id=91, status="running")),
    )

    def fake_run_catch_up_diarization_windows(**kwargs):
        captured["processing_run_id"] = kwargs["processing_run_id"]
        captured["recording_id"] = kwargs["recording"].id
        raise _StopPipeline()

    monkeypatch.setattr(
        tasks_module,
        "_run_catch_up_diarization_windows",
        fake_run_catch_up_diarization_windows,
    )

    task = tasks_module.process_recording_task
    monkeypatch.setattr(task, "_session", fake_session, raising=False)
    monkeypatch.setattr(task, "update_state", lambda *a, **k: None, raising=False)

    with pytest.raises(_StopPipeline):
        task.run(401, False, None)

    assert captured == {"processing_run_id": 91, "recording_id": 401}


def test_process_recording_task_rolls_back_before_persisting_error_state(monkeypatch):
    from backend.models.recording import RecordingStatus
    from backend.worker import tasks as tasks_module

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

    class _FakeTranscript:
        def __init__(self):
            self.recording_id = 601
            self.text = ""
            self.segments = []
            self.transcript_status = "pending"
            self.error_message = None
            self.notes_status = "pending"

    class _ExecResult:
        def first(self):
            return None

        def all(self):
            return []

    class _FakeRecording:
        id = 601
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
        def __init__(self):
            self.recording = _FakeRecording()
            self.rollback_called = False
            self.needs_rollback = False
            self.transcript = _FakeTranscript()

        def get(self, model, recording_id):
            return self.recording

        def add(self, obj):
            pass

        def commit(self):
            if self.needs_rollback and not self.rollback_called:
                raise RuntimeError("session still needs rollback")

        def rollback(self):
            self.rollback_called = True
            self.needs_rollback = False

        def refresh(self, obj):
            pass

        def flush(self):
            pass

        def exec(self, *a, **k):
            return _ExecResult()

        def close(self):
            pass

    monkeypatch.setattr(tasks_module.config_manager, "reload", lambda: None)
    monkeypatch.setattr(
        tasks_module.config_manager,
        "get",
        lambda key, default=None: True
        if key in {"keep_models_loaded", "enable_canonical_transcript_writes"}
        else default,
    )

    fake_session = _FakeSession()

    import sys
    import types

    def _install(module_name: str, **attrs):
        mod = types.ModuleType(module_name)
        for name, value in attrs.items():
            setattr(mod, name, value)
        monkeypatch.setitem(sys.modules, module_name, mod)

    _install(
        "backend.processing.vad",
        mute_non_speech_segments=lambda *a, **k: (True, 10.0),
    )
    _install(
        "backend.processing.audio_preprocessing",
        convert_wav_to_mp3=lambda *a, **k: None,
        preprocess_audio_for_vad=lambda path: "/tmp/recording_vad.wav",
        validate_audio_file=lambda *a, **k: None,
        cleanup_temp_file=lambda *a, **k: None,
        repair_audio_file=lambda *a, **k: None,
    )
    _install(
        "backend.processing.transcribe",
        transcribe_audio=lambda *a, **k: {
            "text": "hello world",
            "segments": [{"start": 0.0, "end": 1.0, "text": "hello world"}],
        },
        release_model_cache=lambda: None,
    )
    _install(
        "backend.processing.diarize",
        diarize_audio=lambda *a, **k: None,
        release_pipeline_cache=lambda: None,
    )
    _install("backend.processing.embedding_core", extract_embeddings=lambda *a, **k: {})
    _install(
        "backend.processing.embedding",
        cosine_similarity=lambda *a, **k: 0.0,
        merge_embeddings=lambda *a, **k: None,
        find_matching_global_speaker=lambda *a, **k: (None, 0.0),
        AUTO_UPDATE_THRESHOLD=0.8,
    )
    _install(
        "backend.utils.transcript_utils",
        combine_transcription_diarization=lambda *a, **k: [],
        consolidate_diarized_transcript=lambda *a, **k: [
            {
                "start": 0.0,
                "end": 1.0,
                "speaker": "UNKNOWN",
                "text": "hello world",
                "segment_source": "finalize",
            }
        ],
    )
    _install(
        "backend.utils.audio",
        get_audio_duration=lambda *a, **k: 60.0,
        convert_to_mp3=lambda *a, **k: None,
        convert_to_proxy_mp3=lambda *a, **k: None,
        extract_audio_clip=lambda *a, **k: None,
    )
    _install(
        "backend.utils.live_transcript",
        apply_live_authority_to_segments=lambda live, combined: combined,
        build_transcription_result_from_segments=lambda segments: ({"text": "", "segments": []}, []),
        merge_reusable_segments=lambda primary, additional: list(primary) + list(additional),
        map_final_speakers_to_live_labels=lambda *a, **k: {},
    )
    _install("backend.processing.llm_services", get_llm_backend=lambda *a, **k: None)
    _install("backend.processing.text_embedding", release_embedding_model=lambda: None)

    monkeypatch.setattr("os.path.exists", lambda path: True)
    monkeypatch.setattr(tasks_module, "build_reusable_live_segments", lambda *a, **k: [])
    monkeypatch.setattr(tasks_module, "_load_recording_audio_window_manifests", lambda *a, **k: [])
    monkeypatch.setattr(tasks_module, "_recording_has_completed_diarization_windows", lambda *a, **k: False)
    monkeypatch.setattr(tasks_module, "_run_automatic_meeting_intelligence_stage", lambda *a, **k: None)
    monkeypatch.setattr(tasks_module, "mark_recording_audio_chunks_ready_for_cleanup", lambda *a, **k: None)
    monkeypatch.setattr(tasks_module, "auto_link_recording", lambda *a, **k: None)
    monkeypatch.setattr(tasks_module, "update_recording_status", lambda *a, **k: None)
    monkeypatch.setattr(tasks_module, "build_recording_speaker_map", lambda *a, **k: {})
    monkeypatch.setattr(tasks_module, "get_speakers_eligible_for_llm_renaming", lambda *a, **k: [])
    monkeypatch.setattr(tasks_module, "_build_automatic_meeting_intelligence_transcript", lambda *a, **k: "")

    def _explode_finalize(session, *args, **kwargs):
        session.needs_rollback = True
        raise RuntimeError("finalize exploded")

    monkeypatch.setattr(tasks_module, "finalize_utterances_from_segments", _explode_finalize)

    task = tasks_module.process_recording_task
    monkeypatch.setattr(task, "_session", fake_session, raising=False)
    monkeypatch.setattr(task, "update_state", lambda *a, **k: None, raising=False)

    result = task.run(601, False, None)

    assert result is None
    assert fake_session.rollback_called is True
    assert fake_session.recording.status == RecordingStatus.ERROR
    assert fake_session.recording.processing_step == "System Error: finalize exploded"


@pytest.mark.skip(reason="Simplified pipeline: catch-up diarization and live reuse removed from finalization")
def test_process_recording_task_prefers_canonical_live_segments_for_reuse(monkeypatch):
    from backend.models.recording import RecordingStatus
    from backend.worker import tasks as tasks_module

    captured: dict = {}

    class _StopPipeline(BaseException):
        pass

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

    class _FakeTranscript:
        segments = []

    class _ExecResult:
        def first(self):
            return _FakeTranscript()

    class _FakeRecording:
        id = 501
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
            return _ExecResult()

        def close(self):
            pass

    monkeypatch.setattr(tasks_module.config_manager, "reload", lambda: None)
    monkeypatch.setattr(
        tasks_module.config_manager,
        "get",
        lambda key, default=None: True
        if key in {"keep_models_loaded", "enable_canonical_transcript_writes"}
        else default,
    )

    fake_session = _FakeSession()

    import sys
    import types

    def _install(module_name: str, **attrs):
        mod = types.ModuleType(module_name)
        for name, value in attrs.items():
            setattr(mod, name, value)
        monkeypatch.setitem(sys.modules, module_name, mod)

    _install(
        "backend.processing.vad",
        mute_non_speech_segments=lambda *a, **k: (True, 10.0),
    )
    _install(
        "backend.processing.audio_preprocessing",
        convert_wav_to_mp3=lambda *a, **k: None,
        preprocess_audio_for_vad=lambda path: "/tmp/recording_vad.wav",
        validate_audio_file=lambda *a, **k: None,
        cleanup_temp_file=lambda *a, **k: None,
        repair_audio_file=lambda *a, **k: None,
    )

    def _unexpected_transcribe(*args, **kwargs):
        raise AssertionError("transcribe_audio should not run when canonical live reuse is available")

    _install(
        "backend.processing.transcribe",
        transcribe_audio=_unexpected_transcribe,
        release_model_cache=lambda: None,
    )
    _install(
        "backend.processing.diarize",
        diarize_audio=lambda *a, **k: None,
        release_pipeline_cache=lambda: None,
    )
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

    def _fake_build_transcription_result_from_segments(segments):
        captured["segments"] = list(segments)
        raise _StopPipeline()

    _install(
        "backend.utils.live_transcript",
        apply_live_authority_to_segments=lambda live, combined: combined,
        build_transcription_result_from_segments=_fake_build_transcription_result_from_segments,
        merge_reusable_segments=lambda primary, additional: list(primary) + list(additional),
        map_final_speakers_to_live_labels=lambda *a, **k: {},
    )
    _install("backend.processing.llm_services", get_llm_backend=lambda *a, **k: None)
    _install("backend.processing.text_embedding", release_embedding_model=lambda: None)

    monkeypatch.setattr("os.path.exists", lambda path: True)
    monkeypatch.setattr(
        tasks_module,
        "build_reusable_live_segments",
        lambda *a, **k: [
            {
                "id": "canon-live-1",
                "start": 0.0,
                "end": 1.0,
                "speaker": "LIVE_01",
                "text": "hello",
                "segment_source": "live",
            }
        ],
    )
    monkeypatch.setattr(tasks_module, "_load_recording_audio_window_manifests", lambda *a, **k: [])
    monkeypatch.setattr(tasks_module, "_load_recording_audio_chunks", lambda *a, **k: [])
    monkeypatch.setattr(tasks_module, "collect_pending_chunk_spans", lambda *a, **k: [])

    task = tasks_module.process_recording_task
    monkeypatch.setattr(task, "_session", fake_session, raising=False)
    monkeypatch.setattr(task, "update_state", lambda *a, **k: None, raising=False)

    with pytest.raises(_StopPipeline):
        task.run(501, False, None)

    assert captured["segments"][0]["id"] == "canon-live-1"


@pytest.mark.skip(reason="Simplified pipeline: catch-up diarization and live reuse removed from finalization")
def test_process_recording_task_uses_catch_up_segments_without_live_reuse(monkeypatch):
    from types import SimpleNamespace

    from backend.models.recording import RecordingStatus
    from backend.worker import tasks as tasks_module

    captured: dict = {}

    class _StopPipeline(BaseException):
        pass

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

    class _FakeTranscript:
        segments = []

    class _ExecResult:
        def first(self):
            return _FakeTranscript()

    class _FakeRecording:
        id = 502
        status = RecordingStatus.PROCESSED
        user_id = None
        audio_path = "/tmp/imported-recording.wav"
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
            return _ExecResult()

        def close(self):
            pass

    monkeypatch.setattr(tasks_module.config_manager, "reload", lambda: None)
    monkeypatch.setattr(
        tasks_module.config_manager,
        "get",
        lambda key, default=None: True
        if key in {"keep_models_loaded", "enable_canonical_transcript_writes"}
        else default,
    )

    fake_session = _FakeSession()

    import sys
    import types

    def _install(module_name: str, **attrs):
        mod = types.ModuleType(module_name)
        for name, value in attrs.items():
            setattr(mod, name, value)
        monkeypatch.setitem(sys.modules, module_name, mod)

    _install(
        "backend.processing.vad",
        mute_non_speech_segments=lambda *a, **k: (True, 10.0),
    )
    _install(
        "backend.processing.audio_preprocessing",
        convert_wav_to_mp3=lambda *a, **k: None,
        preprocess_audio_for_vad=lambda path: "/tmp/imported-recording_vad.wav",
        validate_audio_file=lambda *a, **k: None,
        cleanup_temp_file=lambda *a, **k: None,
        repair_audio_file=lambda *a, **k: None,
    )

    def _unexpected_transcribe(*args, **kwargs):
        raise AssertionError("transcribe_audio should not run when catch-up spans already cover the recording")

    _install(
        "backend.processing.transcribe",
        transcribe_audio=_unexpected_transcribe,
        release_model_cache=lambda: None,
    )
    _install(
        "backend.processing.diarize",
        diarize_audio=lambda *a, **k: None,
        release_pipeline_cache=lambda: None,
    )
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
        extract_audio_clip=lambda *a, **k: None,
    )

    def _fake_build_transcription_result_from_segments(segments):
        captured["segments"] = list(segments)
        raise _StopPipeline()

    _install(
        "backend.utils.live_transcript",
        apply_live_authority_to_segments=lambda live, combined: combined,
        build_transcription_result_from_segments=_fake_build_transcription_result_from_segments,
        merge_reusable_segments=lambda primary, additional: list(primary) + list(additional),
        map_final_speakers_to_live_labels=lambda *a, **k: {},
    )
    _install("backend.processing.llm_services", get_llm_backend=lambda *a, **k: None)
    _install("backend.processing.text_embedding", release_embedding_model=lambda: None)

    monkeypatch.setattr("os.path.exists", lambda path: True)
    monkeypatch.setattr(tasks_module, "build_reusable_live_segments", lambda *a, **k: [])
    monkeypatch.setattr(
        tasks_module,
        "_load_recording_audio_window_manifests",
        lambda *a, **k: [SimpleNamespace(id=31, status="pending")],
    )
    monkeypatch.setattr(tasks_module, "_load_recording_audio_chunks", lambda *a, **k: [])
    monkeypatch.setattr(tasks_module, "collect_pending_chunk_spans", lambda *a, **k: [])
    monkeypatch.setattr(
        tasks_module,
        "_build_catch_up_segments",
        lambda **kwargs: (
            [
                {
                    "start": 0.0,
                    "end": 1.2,
                    "speaker": "UNKNOWN",
                    "text": "import catch up",
                    "segment_source": "catch_up",
                }
            ],
            {31},
            SimpleNamespace(id=93, status="running"),
        ),
    )

    task = tasks_module.process_recording_task
    monkeypatch.setattr(task, "_session", fake_session, raising=False)
    monkeypatch.setattr(task, "update_state", lambda *a, **k: None, raising=False)

    with pytest.raises(_StopPipeline):
        task.run(502, False, None)

    assert captured["segments"] == [
        {
            "start": 0.0,
            "end": 1.2,
            "speaker": "UNKNOWN",
            "text": "import catch up",
            "segment_source": "catch_up",
        }
    ]


@pytest.mark.skip(reason="Simplified pipeline: catch-up diarization and live reuse removed from finalization")
def test_process_recording_task_refreshes_transcript_projection_after_window_replay(monkeypatch):
    from types import SimpleNamespace

    from backend.models.recording import RecordingStatus
    from backend.worker import tasks as tasks_module

    captured: dict = {}

    base_config = {
        "transcription_backend": "whisper",
        "whisper_model_size": "base",
        "enable_vad": False,
        "enable_diarization": True,
    }

    class _FakeLlmConfig:
        merged_config = dict(base_config)

    monkeypatch.setattr(
        tasks_module, "resolve_llm_config", lambda *a, **k: _FakeLlmConfig()
    )

    class _FakeTranscript:
        def __init__(self):
            self.recording_id = 701
            self.text = ""
            self.segments = []
            self.transcript_status = "pending"
            self.error_message = None
            self.notes_status = "pending"

    class _ExecResult:
        def __init__(self, session):
            self._session = session

        def first(self):
            if not self._session.transcript_returned:
                self._session.transcript_returned = True
                return self._session.transcript
            return None

        def all(self):
            return []

    class _FakeRecording:
        id = 701
        status = RecordingStatus.PROCESSED
        user_id = None
        audio_path = "/tmp/recording.wav"
        proxy_path = None
        duration_seconds = 60.0
        processing_started_at = None
        processing_completed_at = None
        processing_progress = 0
        processing_step = ""
        client_status = None

    class _FakeSession:
        def __init__(self):
            self.recording = _FakeRecording()
            self.transcript = _FakeTranscript()
            self.transcript_returned = False

        def get(self, model, recording_id):
            return self.recording

        def add(self, obj):
            pass

        def commit(self):
            pass

        def refresh(self, obj):
            pass

        def flush(self):
            pass

        def exec(self, *a, **k):
            return _ExecResult(self)

        def close(self):
            pass

    monkeypatch.setattr(tasks_module.config_manager, "reload", lambda: None)
    monkeypatch.setattr(
        tasks_module.config_manager,
        "get",
        lambda key, default=None: True
        if key in {"keep_models_loaded", "enable_canonical_transcript_writes"}
        else default,
    )

    fake_session = _FakeSession()

    import sys
    import types

    def _install(module_name: str, **attrs):
        mod = types.ModuleType(module_name)
        for name, value in attrs.items():
            setattr(mod, name, value)
        monkeypatch.setitem(sys.modules, module_name, mod)

    _install(
        "backend.processing.vad",
        mute_non_speech_segments=lambda *a, **k: (True, 10.0),
    )
    _install(
        "backend.processing.audio_preprocessing",
        convert_wav_to_mp3=lambda *a, **k: None,
        preprocess_audio_for_vad=lambda path: "/tmp/recording_vad.wav",
        validate_audio_file=lambda *a, **k: None,
        cleanup_temp_file=lambda *a, **k: None,
        repair_audio_file=lambda *a, **k: None,
    )

    def _unexpected_transcribe(*args, **kwargs):
        raise AssertionError("transcribe_audio should not run when live reuse is available")

    _install(
        "backend.processing.transcribe",
        transcribe_audio=_unexpected_transcribe,
        release_model_cache=lambda: None,
    )
    _install(
        "backend.processing.diarize",
        diarize_audio=lambda *a, **k: None,
        release_pipeline_cache=lambda: None,
    )
    _install("backend.processing.embedding_core", extract_embeddings=lambda *a, **k: {})
    _install(
        "backend.processing.embedding",
        cosine_similarity=lambda *a, **k: 0.0,
        merge_embeddings=lambda *a, **k: None,
        find_matching_global_speaker=lambda *a, **k: (None, 0.0),
        AUTO_UPDATE_THRESHOLD=0.8,
    )
    _install(
        "backend.utils.transcript_utils",
        combine_transcription_diarization=lambda *a, **k: [],
        consolidate_diarized_transcript=lambda segments, *a, **k: [dict(segment) for segment in segments],
    )
    _install(
        "backend.utils.audio",
        get_audio_duration=lambda *a, **k: 60.0,
        convert_to_mp3=lambda *a, **k: None,
        convert_to_proxy_mp3=lambda *a, **k: None,
        extract_audio_clip=lambda *a, **k: None,
    )
    _install(
        "backend.utils.live_transcript",
        apply_live_authority_to_segments=lambda *a, **k: [
            {
                "start": 0.0,
                "end": 1.0,
                "speaker": "LIVE_01",
                "text": "hello there",
                "segment_source": "live",
            }
        ],
        build_transcription_result_from_segments=lambda segments: (
            {
                "text": "hello there",
                "segments": [{"start": 0.0, "end": 1.0, "text": "hello there"}],
            },
            [{"start": 0.0, "end": 1.0, "text": "hello there"}],
        ),
        merge_reusable_segments=lambda primary, additional: list(primary) + list(additional),
        map_final_speakers_to_live_labels=lambda *a, **k: {},
    )
    _install("backend.processing.llm_services", get_llm_backend=lambda *a, **k: None)
    _install("backend.processing.text_embedding", release_embedding_model=lambda: None)

    monkeypatch.setattr("os.path.exists", lambda path: True)
    monkeypatch.setattr(
        tasks_module,
        "build_reusable_live_segments",
        lambda *a, **k: [
            {
                "id": "canon-live-1",
                "start": 0.0,
                "end": 1.0,
                "speaker": "LIVE_01",
                "text": "hello there",
                "segment_source": "live",
                "speaker_state": "stable",
                "speaker_confidence": 0.95,
            }
        ],
    )
    monkeypatch.setattr(tasks_module, "_load_recording_audio_window_manifests", lambda *a, **k: [])
    monkeypatch.setattr(tasks_module, "_load_recording_audio_chunks", lambda *a, **k: [])
    monkeypatch.setattr(tasks_module, "collect_pending_chunk_spans", lambda *a, **k: [])
    monkeypatch.setattr(
        tasks_module,
        "_summarize_completed_diarization_window_speaker_evidence",
        lambda *a, **k: {
            "completed_window_count": 1,
            "multi_speaker_window_count": 1,
            "max_speaker_count": 2,
        },
    )
    monkeypatch.setattr(
        tasks_module,
        "_build_final_diarization_plan",
        lambda **kwargs: {
            "should_run": False,
            "reason": "confident_live_reuse",
            "low_confidence_spans": [],
            "completed_window_replay_available": True,
        },
    )
    monkeypatch.setattr(tasks_module, "finalize_utterances_from_segments", lambda *a, **k: None)
    monkeypatch.setattr(
        tasks_module,
        "reconcile_completed_diarization_windows",
        lambda *a, **k: {
            "matched_turn_count": 2,
            "updated_utterance_count": 1,
            "preserved_manual_lock_count": 0,
        },
    )
    monkeypatch.setattr(
        tasks_module,
        "refine_recording_utterances_via_segmentation",
        lambda *a, **k: {"refined_utterance_count": 0},
    )

    projection_snapshots = [
        [
            {
                "start": 0.0,
                "end": 1.0,
                "speaker": "LIVE_01",
                "text": "hello there",
                "segment_source": "live",
            }
        ],
        [
            {
                "start": 0.0,
                "end": 0.5,
                "speaker": "LIVE_01",
                "text": "hello",
                "segment_source": "finalize_window_replay",
            },
            {
                "start": 0.5,
                "end": 1.0,
                "speaker": "LIVE_02",
                "text": "there",
                "segment_source": "finalize_window_replay",
            },
        ],
    ]
    refresh_calls: list[list[dict]] = []

    def _fake_refresh_projection(session, recording_id):
        snapshot = projection_snapshots[min(len(refresh_calls), len(projection_snapshots) - 1)]
        normalized = [dict(segment) for segment in snapshot]
        session.transcript.segments = normalized
        session.transcript.text = " ".join(segment["text"] for segment in normalized)
        refresh_calls.append(normalized)
        return normalized

    monkeypatch.setattr(
        tasks_module,
        "refresh_transcript_projection_from_canonical",
        _fake_refresh_projection,
    )
    monkeypatch.setattr(tasks_module, "get_speakers_eligible_for_llm_renaming", lambda *a, **k: [])
    monkeypatch.setattr(tasks_module, "build_recording_speaker_map", lambda *a, **k: {})

    def _capture_transcript_for_ai(segments, *args, **kwargs):
        captured["segments_for_ai"] = [dict(segment) for segment in segments]
        return ""

    monkeypatch.setattr(
        tasks_module,
        "_build_automatic_meeting_intelligence_transcript",
        _capture_transcript_for_ai,
    )
    monkeypatch.setattr(tasks_module, "_run_automatic_meeting_intelligence_stage", lambda *a, **k: None)
    monkeypatch.setattr(tasks_module, "mark_recording_audio_chunks_ready_for_cleanup", lambda *a, **k: None)
    monkeypatch.setattr(tasks_module, "auto_link_recording", lambda *a, **k: None)
    monkeypatch.setattr(tasks_module, "update_recording_status", lambda *a, **k: None)
    monkeypatch.setattr(tasks_module, "index_transcript_task", SimpleNamespace(delay=lambda *a, **k: None))

    task = tasks_module.process_recording_task
    monkeypatch.setattr(task, "_session", fake_session, raising=False)
    monkeypatch.setattr(task, "update_state", lambda *a, **k: None, raising=False)

    result = task.run(701, False, None)

    assert result == {"status": "success", "recording_id": 701}
    assert len(refresh_calls) == 2
    assert fake_session.transcript.segments == projection_snapshots[1]
    assert fake_session.transcript.text == "hello there"
    assert captured["segments_for_ai"] == projection_snapshots[1]


def test_summarize_completed_diarization_window_speaker_evidence_rows_counts_distinct_speakers():
    from types import SimpleNamespace

    from backend.worker import tasks as tasks_module

    summary = tasks_module._summarize_completed_diarization_window_speaker_evidence_rows(
        [
            SimpleNamespace(raw_payload={"speaker_labels": ["SPEAKER_00", "SPEAKER_01"]}),
            SimpleNamespace(
                raw_payload={
                    "speaker_metadata": {
                        "SPEAKER_00": {},
                        "SPEAKER_01": {},
                        "SPEAKER_02": {},
                    }
                }
            ),
            SimpleNamespace(
                raw_payload={
                    "turns": [
                        {"local_speaker_key": "SPEAKER_01"},
                        {"local_speaker_key": "SPEAKER_01"},
                    ]
                }
            ),
        ]
    )

    assert summary == {
        "completed_window_count": 3,
        "multi_speaker_window_count": 2,
        "max_speaker_count": 3,
    }


def test_build_final_diarization_plan_skips_confident_live_reuse():
    from backend.worker import tasks as tasks_module

    plan = tasks_module._build_final_diarization_plan(
        live_segments_for_reuse=[
            {
                "start": 0.0,
                "end": 1.0,
                "speaker": "LIVE_01",
                "segment_source": "live",
                "speaker_state": "stable",
                "speaker_confidence": 0.91,
                "overlapping_speakers": [],
            }
        ],
        reused_live_transcript_segments=[
            {"start": 0.0, "end": 1.0, "text": "hello"}
        ],
        engine_override=None,
    )

    assert plan == {
        "should_run": False,
        "reason": "confident_live_reuse",
        "low_confidence_spans": [],
        "completed_window_replay_available": False,
    }


def test_build_final_diarization_plan_runs_for_low_confidence_live_spans():
    from backend.worker import tasks as tasks_module

    plan = tasks_module._build_final_diarization_plan(
        live_segments_for_reuse=[
            {
                "start": 0.0,
                "end": 1.0,
                "speaker": "LIVE_01",
                "segment_source": "live",
                "speaker_state": "stable",
                "speaker_confidence": 0.94,
                "overlapping_speakers": [],
            },
            {
                "start": 2.0,
                "end": 2.5,
                "speaker": "LIVE_02",
                "segment_source": "live",
                "speaker_state": "provisional",
                "speaker_confidence": 0.48,
                "overlapping_speakers": [],
            },
            {
                "start": 2.6,
                "end": 3.0,
                "speaker": "LIVE_02",
                "segment_source": "live",
                "speaker_state": "provisional",
                "speaker_confidence": 0.41,
                "overlapping_speakers": [],
            },
        ],
        reused_live_transcript_segments=[
            {"start": 0.0, "end": 3.0, "text": "hello provisional span"}
        ],
        engine_override=None,
    )

    assert plan["should_run"] is True
    assert plan["reason"] == "low_confidence_spans"
    assert plan["low_confidence_spans"] == [
        {"start_ms": 1000, "end_ms": 4000, "segment_count": 2}
    ]


def test_build_final_diarization_plan_runs_for_low_confidence_even_with_window_replay():
    from backend.worker import tasks as tasks_module

    plan = tasks_module._build_final_diarization_plan(
        live_segments_for_reuse=[
            {
                "start": 0.0,
                "end": 1.0,
                "speaker": "LIVE_01",
                "segment_source": "live",
                "speaker_state": "provisional",
                "speaker_confidence": 0.42,
                "overlapping_speakers": [],
            }
        ],
        reused_live_transcript_segments=[
            {"start": 0.0, "end": 1.0, "text": "hello replay"}
        ],
        engine_override=None,
        completed_window_replay_available=True,
    )

    assert plan == {
        "should_run": True,
        "reason": "low_confidence_spans",
        "low_confidence_spans": [{"start_ms": 0, "end_ms": 2000, "segment_count": 1}],
        "completed_window_replay_available": True,
    }


def test_build_final_diarization_plan_runs_for_completed_window_speaker_mismatch():
    from backend.worker import tasks as tasks_module

    plan = tasks_module._build_final_diarization_plan(
        live_segments_for_reuse=[
            {
                "start": 0.0,
                "end": 5.0,
                "speaker": "LIVE_01",
                "segment_source": "live",
                "speaker_state": "stable",
                "speaker_confidence": 0.93,
                "overlapping_speakers": [],
            },
            {
                "start": 5.0,
                "end": 10.0,
                "speaker": "LIVE_01",
                "segment_source": "live",
                "speaker_state": "stable",
                "speaker_confidence": 0.94,
                "overlapping_speakers": [],
            },
        ],
        reused_live_transcript_segments=[
            {"start": 0.0, "end": 5.0, "text": "speaker a content"},
            {"start": 5.0, "end": 10.0, "text": "speaker b content"},
        ],
        engine_override=None,
        completed_window_replay_available=True,
        completed_window_speaker_evidence={
            "completed_window_count": 3,
            "multi_speaker_window_count": 2,
            "max_speaker_count": 2,
        },
    )

    assert plan == {
        "should_run": True,
        "reason": "completed_window_speaker_mismatch",
        "low_confidence_spans": [],
        "completed_window_replay_available": True,
    }


def test_collect_ordered_final_speaker_labels_keeps_unknown_unresolved():
    from backend.worker import tasks as tasks_module

    labels = tasks_module._collect_ordered_final_speaker_labels(
        [
            {"speaker": "UNKNOWN", "overlapping_speakers": ["LIVE_01"]},
            {"speaker": "LIVE_02", "overlapping_speakers": ["UNKNOWN", "LIVE_01"]},
            {"speaker": "", "overlapping_speakers": []},
        ]
    )

    assert labels == ["LIVE_01", "LIVE_02"]


def test_build_catch_up_segments_reuses_completed_ledger_span(monkeypatch):
    from types import SimpleNamespace

    from backend.worker import tasks as tasks_module

    fake_run = SimpleNamespace(id=91, status="running", completed_at=None, error_summary=None)

    class _FakeSession:
        def __init__(self):
            self.added = []

        def add(self, obj):
            self.added.append(obj)

    session = _FakeSession()
    recording = SimpleNamespace(id=601)

    monkeypatch.setattr(tasks_module, "ensure_processing_run", lambda *a, **k: fake_run)
    monkeypatch.setattr(
        tasks_module,
        "_load_recording_audio_window_manifests",
        lambda *a, **k: [
            SimpleNamespace(
                id=11,
                status="pending",
                window_start_ms=1000,
                window_end_ms=3000,
                chunk_start_sequence=1,
                chunk_end_sequence=3,
            )
        ],
    )
    monkeypatch.setattr(
        tasks_module,
        "_load_recording_audio_chunks",
        lambda *a, **k: [
            SimpleNamespace(sequence_no=1, absolute_start_ms=1000, absolute_end_ms=2000),
            SimpleNamespace(sequence_no=2, absolute_start_ms=2000, absolute_end_ms=2500),
            SimpleNamespace(sequence_no=3, absolute_start_ms=2500, absolute_end_ms=3000),
        ],
    )
    monkeypatch.setattr(
        tasks_module.config_manager,
        "get",
        lambda key, default=None: True if key == "enable_asr_window_result_ledger" else default,
    )
    monkeypatch.setattr(
        tasks_module,
        "get_recording_asr_window_result",
        lambda *a, **k: SimpleNamespace(
            status="completed",
            span_start_ms=1000,
            result_payload={
                "segments": [
                    {"start": 0.0, "end": 0.75, "speaker": "LIVE_01", "text": "hello"}
                ]
            },
        ),
    )

    def _unexpected_transcribe(*args, **kwargs):
        raise AssertionError("transcribe_audio should not run for a completed catch-up ledger span")

    segments, window_ids, catch_up_run = tasks_module._build_catch_up_segments(
        session=session,
        recording=recording,
        processed_audio_path="/tmp/recording.wav",
        merged_config={"transcription_backend": "whisper", "whisper_model_size": "base"},
        transcribe_audio=_unexpected_transcribe,
        extract_audio_clip=lambda *a, **k: None,
        temp_files=[],
        log=tasks_module.logger,
    )

    assert segments == [
        {
            "start": 1.0,
            "end": 1.75,
            "speaker": "LIVE_01",
            "text": "hello",
            "segment_source": "catch_up",
        }
    ]
    assert window_ids == {11}
    assert catch_up_run is fake_run


def test_build_catch_up_segments_reruns_only_uncovered_spans(monkeypatch):
    from types import SimpleNamespace

    from backend.worker import tasks as tasks_module

    fake_run = SimpleNamespace(id=92, status="running", completed_at=None, error_summary=None)
    transcribe_calls: list[str] = []

    class _FakeSession:
        def __init__(self):
            self.added = []

        def add(self, obj):
            self.added.append(obj)

    session = _FakeSession()
    recording = SimpleNamespace(id=602)

    monkeypatch.setattr(tasks_module, "ensure_processing_run", lambda *a, **k: fake_run)
    monkeypatch.setattr(
        tasks_module,
        "_load_recording_audio_window_manifests",
        lambda *a, **k: [
            SimpleNamespace(
                id=21,
                status="pending",
                window_start_ms=0,
                window_end_ms=1000,
                chunk_start_sequence=1,
                chunk_end_sequence=1,
            ),
            SimpleNamespace(
                id=22,
                status="pending",
                window_start_ms=2000,
                window_end_ms=3000,
                chunk_start_sequence=3,
                chunk_end_sequence=3,
            ),
        ],
    )
    monkeypatch.setattr(tasks_module, "_load_recording_audio_chunks", lambda *a, **k: [])
    monkeypatch.setattr(
        tasks_module,
        "collect_pending_chunk_spans",
        lambda *a, **k: [
            SimpleNamespace(start_sequence=1, end_sequence=1, start_ms=0, end_ms=1000),
            SimpleNamespace(start_sequence=3, end_sequence=3, start_ms=2000, end_ms=3000),
        ],
    )
    monkeypatch.setattr(
        tasks_module.config_manager,
        "get",
        lambda key, default=None: True if key == "enable_asr_window_result_ledger" else default,
    )

    def _fake_lookup(*args, **kwargs):
        if kwargs["chunk_start_sequence"] == 1:
            return SimpleNamespace(
                status="completed",
                span_start_ms=0,
                result_payload={
                    "segments": [
                        {"start": 0.0, "end": 0.6, "speaker": "LIVE_01", "text": "done"}
                    ]
                },
            )
        if kwargs["chunk_start_sequence"] == 3:
            return SimpleNamespace(
                status="failed",
                span_start_ms=2000,
                result_payload={"error": "boom"},
            )
        return None

    monkeypatch.setattr(tasks_module, "get_recording_asr_window_result", _fake_lookup)
    monkeypatch.setattr(tasks_module, "start_recording_asr_window_result", lambda *a, **k: None)
    monkeypatch.setattr(tasks_module, "complete_recording_asr_window_result", lambda *a, **k: None)
    monkeypatch.setattr(tasks_module, "fail_recording_asr_window_result", lambda *a, **k: None)

    def _fake_extract_audio_clip(*args, **kwargs):
        pass

    def _fake_transcribe(path, config=None):
        transcribe_calls.append(path)
        return {
            "text": "retry",
            "segments": [
                {"start": 0.1, "end": 0.7, "speaker": "LIVE_02", "text": "retry"}
            ],
        }

    segments, window_ids, catch_up_run = tasks_module._build_catch_up_segments(
        session=session,
        recording=recording,
        processed_audio_path="/tmp/recording.wav",
        merged_config={"transcription_backend": "whisper", "whisper_model_size": "base"},
        transcribe_audio=_fake_transcribe,
        extract_audio_clip=_fake_extract_audio_clip,
        temp_files=[],
        log=tasks_module.logger,
    )

    assert len(transcribe_calls) == 1
    assert transcribe_calls[0].endswith("catch_up_602_3_3.wav")
    assert segments == [
        {
            "start": 0.0,
            "end": 0.6,
            "speaker": "LIVE_01",
            "text": "done",
            "segment_source": "catch_up",
        },
        {
            "start": 2.1,
            "end": 2.7,
            "speaker": "LIVE_02",
            "text": "retry",
            "segment_source": "catch_up",
        },
    ]
    assert window_ids == {21, 22}
    assert catch_up_run is fake_run


@pytest.mark.anyio
async def test_import_audio_bootstraps_durable_import_chunk_and_window(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    monkeypatch,
    tmp_path: Path,
) -> None:
    from backend.api.v1.endpoints import recordings as recordings_module
    from backend.utils import recording_audio_sync as sync_module

    calls = _patch_delay(monkeypatch)
    monkeypatch.setattr(recordings_module, "get_audio_duration", lambda *a, **k: 12.0)
    monkeypatch.setattr(sync_module, "get_audio_duration", lambda *a, **k: 12.0)
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path))

    response = await client.post(
        "/api/v1/recordings/import",
        files={"file": ("import.wav", b"RIFF....fakewavdata", "audio/wav")},
    )

    assert response.status_code == 200
    assert len(calls) == 1

    async with test_session_maker() as session:
        chunk_rows = (
            await session.execute(text("SELECT sequence_no, source_kind, absolute_start_ms, absolute_end_ms, storage_path FROM recording_audio_chunks"))
        ).all()
        manifest_rows = (
            await session.execute(text("SELECT source_kind, window_start_ms, window_end_ms, chunk_start_sequence, chunk_end_sequence FROM recording_audio_window_manifests"))
        ).all()

    assert len(chunk_rows) == 1
    assert chunk_rows[0][0] == 0
    assert chunk_rows[0][1] == "import"
    assert chunk_rows[0][2] == 0
    assert chunk_rows[0][3] == 12000
    assert Path(chunk_rows[0][4]).exists()
    assert manifest_rows == [("import", 0, 12000, 0, 0)]


@pytest.mark.anyio
async def test_import_low_bitrate_audio_succeeds(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    monkeypatch,
    tmp_path: Path,
) -> None:
    from backend.api.v1.endpoints import recordings as recordings_module
    from backend.utils import recording_audio_sync as sync_module

    calls = _patch_delay(monkeypatch)
    monkeypatch.setattr(recordings_module, "get_audio_duration", lambda *a, **k: 15.0)
    monkeypatch.setattr(sync_module, "get_audio_duration", lambda *a, **k: 15.0)
    monkeypatch.setenv("RECORDINGS_DIR", str(tmp_path))

    # Send a request to import a low-bitrate .mp3 file
    response = await client.post(
        "/api/v1/recordings/import",
        files={"file": ("low_quality.mp3", b"fake-mp3-data", "audio/mp3")},
    )

    assert response.status_code == 200
    assert len(calls) == 1

    async with test_session_maker() as session:
        chunk_rows = (
            await session.execute(text("SELECT sequence_no, source_kind, absolute_start_ms, absolute_end_ms, storage_path FROM recording_audio_chunks"))
        ).all()
        manifest_rows = (
            await session.execute(text("SELECT source_kind, window_start_ms, window_end_ms, chunk_start_sequence, chunk_end_sequence FROM recording_audio_window_manifests"))
        ).all()

    assert len(chunk_rows) == 1
    assert chunk_rows[0][1] == "import"
    assert chunk_rows[0][3] == 15000
    assert manifest_rows == [("import", 0, 15000, 0, 0)]

