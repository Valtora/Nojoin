from __future__ import annotations

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.models.user import User
from backend.worker.tasks import get_text_embedding_task

TEST_TIMESTAMP = datetime(2026, 5, 25, 12, 0, 0)

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        created_at DATETIME,
        updated_at DATETIME,
        username VARCHAR,
        hashed_password VARCHAR,
        is_active BOOLEAN,
        is_superuser BOOLEAN,
        force_password_change BOOLEAN,
        role VARCHAR,
        token_version INTEGER,
        settings JSON,
        has_seen_demo_recording BOOLEAN,
        invitation_id INTEGER
    )
    """,
    """
    CREATE TABLE recordings (
        id INTEGER PRIMARY KEY,
        created_at DATETIME,
        updated_at DATETIME,
        name VARCHAR,
        public_id VARCHAR UNIQUE,
        meeting_uid VARCHAR,
        audio_path VARCHAR,
        proxy_path VARCHAR,
        celery_task_id VARCHAR,
        duration_seconds FLOAT,
        file_size_bytes INTEGER,
        status VARCHAR,
        client_status VARCHAR,
        upload_progress INTEGER,
        processing_progress INTEGER,
        processing_step VARCHAR,
        processing_started_at DATETIME,
        processing_completed_at DATETIME,
        pipeline_generation VARCHAR,
        is_archived BOOLEAN,
        is_deleted BOOLEAN,
        last_activity_at DATETIME,
        user_id INTEGER,
        calendar_event_id INTEGER
    )
    """,
    """
    CREATE TABLE transcripts (
        id INTEGER PRIMARY KEY,
        created_at DATETIME,
        updated_at DATETIME,
        recording_id INTEGER UNIQUE,
        text TEXT,
        segments JSON,
        notes TEXT,
        user_notes TEXT,
        meeting_edge_focus TEXT,
        meeting_edge_payload JSON,
        meeting_edge_status VARCHAR DEFAULT 'idle',
        meeting_edge_error_message TEXT,
        meeting_edge_source_signature TEXT,
        speaker_name_suggestions JSON,
        notes_status VARCHAR,
        transcript_status VARCHAR,
        error_message TEXT
    )
    """,
    """
    CREATE TABLE recording_speakers (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        public_id VARCHAR(36),
        recording_id INTEGER NOT NULL,
        global_speaker_id INTEGER,
        diarization_label VARCHAR(255) NOT NULL,
        name VARCHAR(255),
        local_name VARCHAR(255),
        speaker_status VARCHAR(32) NOT NULL DEFAULT 'active',
        speaker_kind VARCHAR(32) NOT NULL DEFAULT 'automated',
        processing_run_id INTEGER,
        last_speaker_correction_event_id INTEGER,
        last_diarization_window_result_id INTEGER,
        first_seen_ms INTEGER,
        last_seen_ms INTEGER,
        identity_confidence FLOAT,
        identity_locked BOOLEAN NOT NULL DEFAULT 0,
        voice_snippet_path VARCHAR(1024),
        snippet_start FLOAT,
        snippet_end FLOAT,
        embedding BLOB,
        has_voiceprint BOOLEAN NOT NULL DEFAULT 0,
        color VARCHAR(64),
        merged_into_id INTEGER,
        FOREIGN KEY(recording_id) REFERENCES recordings(id),
        FOREIGN KEY(global_speaker_id) REFERENCES global_speakers(id)
    )
    """,
    """
    CREATE TABLE global_speakers (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        name VARCHAR(255) NOT NULL,
        embedding BLOB,
        user_id INTEGER,
        color VARCHAR(64),
        title VARCHAR(255),
        company VARCHAR(255),
        email VARCHAR(255),
        phone_number VARCHAR(64),
        notes TEXT,
        is_voiceprint_locked BOOLEAN NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE chat_messages (
        id INTEGER PRIMARY KEY,
        created_at DATETIME,
        updated_at DATETIME,
        recording_id INTEGER,
        user_id INTEGER,
        role VARCHAR,
        content TEXT
    )
    """,
    """
    CREATE TABLE context_chunks (
        id INTEGER PRIMARY KEY,
        recording_id INTEGER,
        document_id INTEGER,
        content TEXT,
        embedding JSON,
        meta JSON
    )
    """
]

def build_test_user(user_id: int, username: str = "alice") -> User:
    return User(
        id=user_id,
        username=username,
        role="user",
        is_active=True,
        is_superuser=False,
        force_password_change=False,
        settings={"llm_provider": "gemini", "gemini_api_key": "fake-key"},
    )

@pytest.fixture
async def api_app() -> FastAPI:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    return app

@pytest.fixture
async def test_session_maker() -> sessionmaker:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as connection:
        for statement in SCHEMA_STATEMENTS:
            await connection.execute(text(statement))

    try:
        yield session_maker
    finally:
        await engine.dispose()

@pytest.fixture
async def client(api_app: FastAPI, test_session_maker: sessionmaker) -> AsyncClient:
    async def override_get_db():
        async with test_session_maker() as session:
            yield session

    api_app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=api_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client

    api_app.dependency_overrides.clear()

@pytest.fixture
def override_current_user(api_app: FastAPI):
    def _override(user_id: int, username: str = "alice") -> None:
        api_app.dependency_overrides[get_current_user] = lambda: build_test_user(
            user_id,
            username,
        )
    return _override

async def seed_data(test_session_maker: sessionmaker) -> None:
    async with test_session_maker() as session:
        # Seed user
        await session.execute(
            text(
                "INSERT INTO users (id, username, role, is_active, is_superuser, force_password_change, settings) "
                "VALUES (1, 'alice', 'user', 1, 0, 0, '{\"llm_provider\": \"gemini\", \"gemini_api_key\": \"fake-key\"}')"
            )
        )
        # Seed recording
        await session.execute(
            text(
                "INSERT INTO recordings (id, created_at, updated_at, name, public_id, status, is_archived, is_deleted, user_id, pipeline_generation) "
                "VALUES (21, :now, :now, 'Test Recording', 'rec-public-21', 'PROCESSED', 0, 0, 1, 'unified')"
            ),
            {"now": TEST_TIMESTAMP}
        )
        # Seed transcript
        await session.execute(
            text(
                "INSERT INTO transcripts (id, created_at, updated_at, recording_id, segments, notes, notes_status, transcript_status) "
                "VALUES (31, :now, :now, 21, '[{\"start\": 0.0, \"end\": 5.0, \"speaker\": \"LIVE_00\", \"text\": \"Hello world\"}]', 'Notes', 'idle', 'idle')"
            ),
            {"now": TEST_TIMESTAMP}
        )
        # Seed recording speaker
        await session.execute(
            text(
                "INSERT INTO recording_speakers (id, created_at, updated_at, public_id, recording_id, diarization_label, name, speaker_status, speaker_kind) "
                "VALUES (41, :now, :now, 'speaker-public-41', 21, 'LIVE_00', 'Dana', 'active', 'automated')"
            ),
            {"now": TEST_TIMESTAMP}
        )
        await session.commit()

@pytest.mark.anyio
@patch("backend.api.v1.endpoints.transcripts.get_llm_backend_with_secondary")
async def test_chat_delegates_embedding_to_celery(
    mock_get_llm_backend_with_secondary,
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
    monkeypatch,
) -> None:
    await seed_data(test_session_maker)
    override_current_user(1)

    # Mock the LLM backend ask_question_streaming method
    mock_llm = MagicMock()
    def mock_generator(*args, **kwargs):
        yield "Hello from AI"
    mock_llm.ask_question_streaming.side_effect = mock_generator
    mock_get_llm_backend_with_secondary.return_value = mock_llm

    # Track Celery send_task calls
    celery_tasks = []
    class MockTaskResult:
        def get(self, timeout=None):
            return [[0.1] * 384]

    def fake_send_task(task_name: str, args: list | None = None, **_: object):
        celery_tasks.append((task_name, args))
        return MockTaskResult()

    from backend.api.v1.endpoints import transcripts as transcripts_module
    monkeypatch.setattr(transcripts_module.celery_app, "send_task", fake_send_task)

    response = await client.post(
        "/api/v1/transcripts/rec-public-21/chat",
        json={"message": "What was discussed?"},
    )

    assert response.status_code == 200
    assert "Hello from AI" in response.text

    # Verify that the Celery task was called for text embedding
    assert len(celery_tasks) == 1
    assert celery_tasks[0][0] == "backend.worker.tasks.get_text_embedding_task"
    assert celery_tasks[0][1] == ["What was discussed?"]

@pytest.mark.anyio
async def test_extract_voiceprint_timeout(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
    monkeypatch,
) -> None:
    await seed_data(test_session_maker)
    override_current_user(1)

    # Force a timeout/exception in Celery task get
    def fake_send_task_timeout(task_name: str, args: list | None = None, **_: object):
        class TimingOutTaskResult:
            def get(self, timeout=None):
                raise TimeoutError("Celery task timed out")
        return TimingOutTaskResult()

    from backend.api.v1.endpoints import speakers as speakers_module
    monkeypatch.setattr(speakers_module.celery_app, "send_task", fake_send_task_timeout)

    response = await client.post(
        "/api/v1/speakers/recordings/rec-public-21/speakers/LIVE_00/voiceprint/extract"
    )

    # Timeout should raise 504 Gateway Timeout
    assert response.status_code == 504
    assert "timed out" in response.json()["detail"]

@pytest.mark.anyio
async def test_extract_all_voiceprints_timeout(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
    monkeypatch,
) -> None:
    await seed_data(test_session_maker)
    override_current_user(1)

    # Force a timeout/exception in Celery task get
    def fake_send_task_timeout(task_name: str, args: list | None = None, **_: object):
        class TimingOutTaskResult:
            def get(self, timeout=None):
                raise TimeoutError("Celery task timed out")
        return TimingOutTaskResult()

    from backend.api.v1.endpoints import speakers as speakers_module
    monkeypatch.setattr(speakers_module.celery_app, "send_task", fake_send_task_timeout)

    response = await client.post(
        "/api/v1/speakers/recordings/rec-public-21/voiceprints/extract-all"
    )

    assert response.status_code == 200
    res_data = response.json()
    # Batch should continue and mark extraction failed/timed out
    assert len(res_data["results"]) == 1
    assert res_data["results"][0]["success"] is False
    assert "timed out" in res_data["results"][0]["error"]

@patch("backend.processing.text_embedding.get_text_embedding_service")
def test_worker_text_embedding_task(mock_get_service):
    mock_service = MagicMock()
    mock_service.embed.return_value = [[0.2] * 384]
    mock_get_service.return_value = mock_service

    res = get_text_embedding_task("hello text")
    assert res == [[0.2] * 384]
