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
from backend.services.recording_identity_service import ensure_recording_meeting_uids

TEST_TIMESTAMP = datetime(2026, 4, 12, 12, 0, 0)
RECORDINGS_SCHEMA = """
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


async def insert_recording(
    session_maker: sessionmaker,
    *,
    recording_id: int,
    name: str,
    meeting_uid: str | None,
    user_id: int | None,
) -> None:
    async with session_maker() as session:
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
                    is_archived,
                    is_deleted,
                    user_id
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
                    :is_archived,
                    :is_deleted,
                    :user_id
                )
                """
            ),
            {
                "id": recording_id,
                "created_at": TEST_TIMESTAMP,
                "updated_at": TEST_TIMESTAMP,
                "name": name,
                "public_id": f"public-recording-{recording_id}",
                "meeting_uid": meeting_uid,
                "audio_path": f"data/recordings/{recording_id}.wav",
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
                "is_archived": False,
                "is_deleted": False,
                "user_id": user_id,
            },
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


@pytest.mark.anyio
async def test_ensure_recording_meeting_uids_backfills_missing_values(
    test_session_maker: sessionmaker,
) -> None:
    await insert_recording(
        test_session_maker,
        recording_id=1,
        name="Missing UID",
        meeting_uid=None,
        user_id=None,
    )
    await insert_recording(
        test_session_maker,
        recording_id=2,
        name="Existing UID",
        meeting_uid="existing-meeting-uid",
        user_id=None,
    )

    async with test_session_maker() as session:
        repaired_count = await ensure_recording_meeting_uids(session)

    async with test_session_maker() as session:
        result = await session.execute(
            text("SELECT id, meeting_uid FROM recordings ORDER BY id")
        )
        rows = result.all()

    assert repaired_count == 1
    assert rows[0][1] is not None
    assert rows[0][1] != ""
    assert rows[1][1] == "existing-meeting-uid"
    assert rows[0][1] != rows[1][1]


@pytest.mark.anyio
async def test_authenticated_recordings_list_includes_meeting_uid(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    await insert_recording(
        test_session_maker,
        recording_id=10,
        name="Authenticated meeting",
        meeting_uid="meeting-uid-visible",
        user_id=1,
    )
    override_current_user(1)

    response = await client.get("/api/v1/recordings/")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["meeting_uid"] == "meeting-uid-visible"
    assert payload[0]["name"] == "Authenticated meeting"