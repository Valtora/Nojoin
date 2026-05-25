from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.models.user import User

TEST_TIMESTAMP = datetime(2026, 5, 25, 12, 0, 0)
SCHEMA_STATEMENTS = [
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
    CREATE TABLE recordings (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        name VARCHAR(255) NOT NULL,
        public_id VARCHAR(36) NOT NULL,
        meeting_uid VARCHAR(36),
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
]


def build_test_user(user_id: int, username: str = "alice") -> User:
    return User(
        id=user_id,
        username=username,
        hashed_password="hashed-password",
        role="user",
        is_active=True,
        is_superuser=False,
        force_password_change=False,
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


async def seed_linked_speaker(session_maker: sessionmaker) -> None:
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO global_speakers (
                    id,
                    created_at,
                    updated_at,
                    name,
                    user_id,
                    color,
                    is_voiceprint_locked
                ) VALUES (
                    :id,
                    :created_at,
                    :updated_at,
                    :name,
                    :user_id,
                    :color,
                    :is_voiceprint_locked
                )
                """
            ),
            {
                "id": 11,
                "created_at": TEST_TIMESTAMP,
                "updated_at": TEST_TIMESTAMP,
                "name": "Dana",
                "user_id": 1,
                "color": "violet",
                "is_voiceprint_locked": False,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id,
                    created_at,
                    updated_at,
                    name,
                    public_id,
                    meeting_uid,
                    audio_path,
                    proxy_path,
                    celery_task_id,
                    duration_seconds,
                    file_size_bytes,
                    status,
                    client_status,
                    upload_progress,
                    processing_progress,
                    processing_step,
                    processing_started_at,
                    processing_completed_at,
                    pipeline_generation,
                    is_archived,
                    is_deleted,
                    user_id,
                    calendar_event_id
                ) VALUES (
                    :id,
                    :created_at,
                    :updated_at,
                    :name,
                    :public_id,
                    :meeting_uid,
                    :audio_path,
                    :proxy_path,
                    :celery_task_id,
                    :duration_seconds,
                    :file_size_bytes,
                    :status,
                    :client_status,
                    :upload_progress,
                    :processing_progress,
                    :processing_step,
                    :processing_started_at,
                    :processing_completed_at,
                    :pipeline_generation,
                    :is_archived,
                    :is_deleted,
                    :user_id,
                    :calendar_event_id
                )
                """
            ),
            {
                "id": 21,
                "created_at": TEST_TIMESTAMP,
                "updated_at": TEST_TIMESTAMP,
                "name": "Meeting",
                "public_id": "rec-public-21",
                "meeting_uid": "meeting-21",
                "audio_path": "data/recordings/21.wav",
                "proxy_path": None,
                "celery_task_id": None,
                "duration_seconds": 60.0,
                "file_size_bytes": 1024,
                "status": "PROCESSED",
                "client_status": None,
                "upload_progress": 100,
                "processing_progress": 100,
                "processing_step": None,
                "processing_started_at": None,
                "processing_completed_at": None,
                "pipeline_generation": "unified",
                "is_archived": False,
                "is_deleted": False,
                "user_id": 1,
                "calendar_event_id": None,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id,
                    created_at,
                    updated_at,
                    public_id,
                    recording_id,
                    global_speaker_id,
                    diarization_label,
                    name,
                    local_name,
                    speaker_status,
                    speaker_kind,
                    processing_run_id,
                    last_speaker_correction_event_id,
                    last_diarization_window_result_id,
                    first_seen_ms,
                    last_seen_ms,
                    identity_confidence,
                    identity_locked,
                    voice_snippet_path,
                    snippet_start,
                    snippet_end,
                    embedding,
                    has_voiceprint,
                    color,
                    merged_into_id
                ) VALUES (
                    :id,
                    :created_at,
                    :updated_at,
                    :public_id,
                    :recording_id,
                    :global_speaker_id,
                    :diarization_label,
                    :name,
                    :local_name,
                    :speaker_status,
                    :speaker_kind,
                    :processing_run_id,
                    :last_speaker_correction_event_id,
                    :last_diarization_window_result_id,
                    :first_seen_ms,
                    :last_seen_ms,
                    :identity_confidence,
                    :identity_locked,
                    :voice_snippet_path,
                    :snippet_start,
                    :snippet_end,
                    :embedding,
                    :has_voiceprint,
                    :color,
                    :merged_into_id
                )
                """
            ),
            {
                "id": 31,
                "created_at": TEST_TIMESTAMP,
                "updated_at": TEST_TIMESTAMP,
                "public_id": "speaker-public-31",
                "recording_id": 21,
                "global_speaker_id": 11,
                "diarization_label": "LIVE_00",
                "name": "Dana",
                "local_name": None,
                "speaker_status": "active",
                "speaker_kind": "automated",
                "processing_run_id": None,
                "last_speaker_correction_event_id": None,
                "last_diarization_window_result_id": None,
                "first_seen_ms": None,
                "last_seen_ms": None,
                "identity_confidence": None,
                "identity_locked": False,
                "voice_snippet_path": None,
                "snippet_start": None,
                "snippet_end": None,
                "embedding": None,
                "has_voiceprint": False,
                "color": None,
                "merged_into_id": None,
            },
        )
        await session.commit()


@pytest.mark.anyio
async def test_recording_speaker_color_updates_stay_meeting_local(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    await seed_linked_speaker(test_session_maker)
    override_current_user(1)

    response = await client.put(
        "/api/v1/speakers/recordings/rec-public-21/speakers/LIVE_00/color",
        json={"color": "orange"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "success", "color": "orange"}

    async with test_session_maker() as session:
        result = await session.execute(
            text(
                """
                SELECT rs.color, gs.color
                FROM recording_speakers rs
                JOIN global_speakers gs ON gs.id = rs.global_speaker_id
                WHERE rs.id = 31
                """
            )
        )
        recording_color, global_color = result.one()

    assert recording_color == "orange"
    assert global_color == "violet"