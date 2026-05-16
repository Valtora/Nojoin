from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_companion_bootstrap_user, get_db
from backend.api.v1.api import api_router
from backend.core import security

TEST_TIMESTAMP = datetime(2026, 4, 22, 17, 0, 0)
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
    is_archived BOOLEAN NOT NULL,
    is_deleted BOOLEAN NOT NULL,
    user_id INTEGER,
    calendar_event_id INTEGER
)
"""


def build_test_user(user_id: int = 1, username: str = "alice"):
    return SimpleNamespace(
        id=user_id,
        username=username,
        force_password_change=False,
    )


async def insert_recording(
    session_maker: sessionmaker,
    *,
    recording_id: int,
    public_id: str,
    user_id: int,
    status: str,
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
                "name": f"Recording {recording_id}",
                "public_id": public_id,
                "meeting_uid": f"meeting-{recording_id}",
                "audio_path": f"data/recordings/{recording_id}.wav",
                "proxy_path": None,
                "celery_task_id": None,
                "duration_seconds": None,
                "file_size_bytes": None,
                "status": status,
                "client_status": "UPLOADING" if status == "UPLOADING" else None,
                "upload_progress": 0,
                "processing_progress": 0,
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
    api_app.dependency_overrides[get_current_companion_bootstrap_user] = lambda: build_test_user()

    transport = ASGITransport(app=api_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client

    api_app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_refresh_upload_token_reissues_scoped_companion_token(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    public_id = "cfd87c80-3f97-4b69-a1dc-8f87cc6d1179"
    await insert_recording(
        test_session_maker,
        recording_id=2026042216115799,
        public_id=public_id,
        user_id=1,
        status="UPLOADING",
    )

    response = await client.post(f"/api/v1/recordings/{public_id}/upload-token")

    assert response.status_code == 200
    payload = response.json()
    assert payload["recording_id"] == public_id

    decoded = security.decode_access_token(payload["upload_token"])
    assert decoded["token_type"] == security.COMPANION_TOKEN_TYPE
    assert decoded["sub"] == "alice"
    assert decoded["recording_public_id"] == public_id
    assert security.COMPANION_RECORDING_SCOPE in decoded["scopes"]


@pytest.mark.anyio
async def test_refresh_upload_token_rejects_completed_recordings(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    public_id = "50759737-14df-4475-8bc7-68bb6c3df0e8"
    await insert_recording(
        test_session_maker,
        recording_id=2026042217000001,
        public_id=public_id,
        user_id=1,
        status="QUEUED",
    )

    response = await client.post(f"/api/v1/recordings/{public_id}/upload-token")

    assert response.status_code == 409
    assert response.json()["detail"] == "Recording is no longer accepting companion uploads"
