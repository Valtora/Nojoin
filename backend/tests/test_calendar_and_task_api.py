from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_admin_user, get_current_user, get_db
from backend.api.v1.api import api_router
from backend.core.encryption import encrypt_secret
from backend.models.user import User
from backend.utils.config_manager import config_manager

TEST_TIMESTAMP = datetime(2026, 4, 12, 10, 0, 0)
SCHEMA_STATEMENTS = [
    """
    CREATE TABLE calendar_provider_configs (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        provider VARCHAR(32) NOT NULL,
        client_id VARCHAR(512),
        client_secret_encrypted TEXT,
        tenant_id VARCHAR(255),
        enabled BOOLEAN NOT NULL
    )
    """,
    """
    CREATE TABLE calendar_connections (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        user_id INTEGER NOT NULL,
        provider VARCHAR(32) NOT NULL,
        provider_account_id VARCHAR(255) NOT NULL,
        email VARCHAR(320),
        display_name VARCHAR(255),
        access_token_encrypted TEXT,
        refresh_token_encrypted TEXT,
        granted_scopes JSON NOT NULL,
        token_expires_at DATETIME,
        sync_status VARCHAR(32) NOT NULL,
        sync_error VARCHAR(512),
        last_sync_started_at DATETIME,
        last_sync_completed_at DATETIME,
        last_synced_at DATETIME
    )
    """,
    """
    CREATE TABLE calendar_sources (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        connection_id INTEGER NOT NULL,
        provider_calendar_id VARCHAR(512) NOT NULL,
        name VARCHAR(255) NOT NULL,
        description TEXT,
        time_zone VARCHAR(128),
        colour VARCHAR(32),
        user_colour VARCHAR(32),
        is_primary BOOLEAN NOT NULL,
        is_read_only BOOLEAN NOT NULL,
        is_selected BOOLEAN NOT NULL,
        sync_cursor TEXT,
        last_synced_at DATETIME,
        sync_window_start DATETIME,
        sync_window_end DATETIME
    )
    """,
    """
    CREATE TABLE calendar_events (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        calendar_id INTEGER NOT NULL,
        provider_event_id VARCHAR(512) NOT NULL,
        title VARCHAR(512) NOT NULL,
        status VARCHAR(32) NOT NULL,
        is_all_day BOOLEAN NOT NULL,
        starts_at DATETIME,
        ends_at DATETIME,
        start_date DATE,
        end_date DATE,
        location_text TEXT,
        description TEXT,
        attendees JSON,
        meeting_url VARCHAR(2048),
        source_url VARCHAR(2048),
        external_updated_at DATETIME
    )
    """,
    """
    CREATE TABLE recordings (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        name VARCHAR NOT NULL,
        public_id VARCHAR(36) NOT NULL,
        meeting_uid VARCHAR(36) NOT NULL,
        audio_path VARCHAR NOT NULL,
        proxy_path VARCHAR,
        celery_task_id VARCHAR,
        duration_seconds FLOAT,
        file_size_bytes INTEGER,
        status VARCHAR NOT NULL,
        client_status VARCHAR,
        upload_progress INTEGER NOT NULL,
        processing_progress INTEGER NOT NULL,
        processing_step VARCHAR,
        processing_started_at DATETIME,
        processing_completed_at DATETIME,
        pipeline_generation VARCHAR(32) DEFAULT 'unified',
        is_archived BOOLEAN NOT NULL,
        is_deleted BOOLEAN NOT NULL,
        last_activity_at DATETIME,
        user_id INTEGER,
        calendar_event_id INTEGER
    )
    """,
    """
    CREATE TABLE global_speakers (
        id INTEGER PRIMARY KEY,
        name VARCHAR(255) NOT NULL
    )
    """,
    """
    CREATE TABLE recording_speakers (
        id INTEGER PRIMARY KEY,
        recording_id INTEGER NOT NULL,
        global_speaker_id INTEGER,
        diarization_label VARCHAR(255) NOT NULL,
        local_name VARCHAR(255),
        name VARCHAR(255),
        processing_run_id INTEGER,
        last_speaker_correction_event_id INTEGER,
        last_diarization_window_result_id INTEGER,
        merged_into_id INTEGER,
        speaker_status VARCHAR(32) NOT NULL DEFAULT 'active'
    )
    """,
    """
    CREATE TABLE tags (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        name VARCHAR(255) NOT NULL,
        color VARCHAR(32),
        user_id INTEGER,
        parent_id INTEGER
    )
    """,
    """
    CREATE TABLE recording_tags (
        id INTEGER PRIMARY KEY,
        recording_id INTEGER NOT NULL,
        tag_id INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE user_tasks (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        title VARCHAR(255) NOT NULL,
        body TEXT,
        due_at DATETIME,
        completed_at DATETIME,
        archived_at DATETIME,
        user_id INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE user_task_tags (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        task_id INTEGER NOT NULL,
        tag_id INTEGER NOT NULL,
        UNIQUE (task_id, tag_id)
    )
    """,
    """
    CREATE TABLE user_task_recordings (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        task_id INTEGER NOT NULL,
        recording_id INTEGER NOT NULL,
        UNIQUE (task_id, recording_id)
    )
    """,
]


def build_test_user(
    user_id: int,
    username: str,
    settings: dict[str, object] | None = None,
) -> User:
    return User(
        id=user_id,
        username=username,
        hashed_password="hashed-password",
        role="user",
        is_active=True,
        is_superuser=False,
        force_password_change=False,
        settings=settings or {},
    )


async def execute_sql(
    session_maker: sessionmaker,
    statement: str,
    params: dict[str, object],
) -> None:
    async with session_maker() as session:
        await session.execute(text(statement), params)
        await session.commit()


async def fetch_scalar(
    session_maker: sessionmaker,
    statement: str,
    params: dict[str, object] | None = None,
):
    async with session_maker() as session:
        result = await session.execute(text(statement), params or {})
        return result.scalar_one()


async def seed_provider_config(session_maker: sessionmaker, *, provider: str) -> None:
    await execute_sql(
        session_maker,
        """
        INSERT INTO calendar_provider_configs (
            id,
            created_at,
            updated_at,
            provider,
            client_id,
            client_secret_encrypted,
            tenant_id,
            enabled
        ) VALUES (
            :id,
            :created_at,
            :updated_at,
            :provider,
            :client_id,
            :client_secret_encrypted,
            :tenant_id,
            :enabled
        )
        """,
        {
            "id": 1 if provider == "google" else 2,
            "created_at": TEST_TIMESTAMP,
            "updated_at": TEST_TIMESTAMP,
            "provider": provider,
            "client_id": f"{provider}-client-id",
            "client_secret_encrypted": encrypt_secret("super-secret"),
            "tenant_id": "common" if provider == "microsoft" else None,
            "enabled": True,
        },
    )


async def seed_calendar_connection(
    session_maker: sessionmaker,
    *,
    connection_id: int,
    user_id: int,
    provider: str = "google",
    access_token_encrypted: str | None = None,
    refresh_token_encrypted: str | None = None,
) -> None:
    await execute_sql(
        session_maker,
        """
        INSERT INTO calendar_connections (
            id,
            created_at,
            updated_at,
            user_id,
            provider,
            provider_account_id,
            email,
            display_name,
            access_token_encrypted,
            refresh_token_encrypted,
            granted_scopes,
            token_expires_at,
            sync_status,
            sync_error,
            last_sync_started_at,
            last_sync_completed_at,
            last_synced_at
        ) VALUES (
            :id,
            :created_at,
            :updated_at,
            :user_id,
            :provider,
            :provider_account_id,
            :email,
            :display_name,
            :access_token_encrypted,
            :refresh_token_encrypted,
            :granted_scopes,
            :token_expires_at,
            :sync_status,
            :sync_error,
            :last_sync_started_at,
            :last_sync_completed_at,
            :last_synced_at
        )
        """,
        {
            "id": connection_id,
            "created_at": TEST_TIMESTAMP,
            "updated_at": TEST_TIMESTAMP,
            "user_id": user_id,
            "provider": provider,
            "provider_account_id": f"acct-{connection_id}",
            "email": "calendar-owner@example.com",
            "display_name": "Calendar Owner",
            "access_token_encrypted": access_token_encrypted,
            "refresh_token_encrypted": refresh_token_encrypted,
            "granted_scopes": json.dumps([]),
            "token_expires_at": None,
            "sync_status": "idle",
            "sync_error": None,
            "last_sync_started_at": None,
            "last_sync_completed_at": None,
            "last_synced_at": None,
        },
    )


async def seed_task(
    session_maker: sessionmaker,
    *,
    task_id: int,
    user_id: int,
    title: str,
    body: str | None = None,
    due_at: datetime | None = None,
    completed_at: datetime | None = None,
    archived_at: datetime | None = None,
) -> None:
    await execute_sql(
        session_maker,
        """
        INSERT INTO user_tasks (
            id,
            created_at,
            updated_at,
            title,
            body,
            due_at,
            completed_at,
            archived_at,
            user_id
        ) VALUES (
            :id,
            :created_at,
            :updated_at,
            :title,
            :body,
            :due_at,
            :completed_at,
            :archived_at,
            :user_id
        )
        """,
        {
            "id": task_id,
            "created_at": TEST_TIMESTAMP,
            "updated_at": TEST_TIMESTAMP,
            "title": title,
            "body": body,
            "due_at": due_at,
            "completed_at": completed_at,
            "archived_at": archived_at,
            "user_id": user_id,
        },
    )


async def seed_calendar_source(
    session_maker: sessionmaker,
    *,
    calendar_id: int,
    connection_id: int,
    provider_calendar_id: str,
    name: str,
    colour: str | None,
    user_colour: str | None = None,
    is_selected: bool = True,
) -> None:
    await execute_sql(
        session_maker,
        """
        INSERT INTO calendar_sources (
            id,
            created_at,
            updated_at,
            connection_id,
            provider_calendar_id,
            name,
            description,
            time_zone,
            colour,
            user_colour,
            is_primary,
            is_read_only,
            is_selected,
            sync_cursor,
            last_synced_at,
            sync_window_start,
            sync_window_end
        ) VALUES (
            :id,
            :created_at,
            :updated_at,
            :connection_id,
            :provider_calendar_id,
            :name,
            :description,
            :time_zone,
            :colour,
            :user_colour,
            :is_primary,
            :is_read_only,
            :is_selected,
            :sync_cursor,
            :last_synced_at,
            :sync_window_start,
            :sync_window_end
        )
        """,
        {
            "id": calendar_id,
            "created_at": TEST_TIMESTAMP,
            "updated_at": TEST_TIMESTAMP,
            "connection_id": connection_id,
            "provider_calendar_id": provider_calendar_id,
            "name": name,
            "description": None,
            "time_zone": "Europe/London",
            "colour": colour,
            "user_colour": user_colour,
            "is_primary": False,
            "is_read_only": False,
            "is_selected": is_selected,
            "sync_cursor": None,
            "last_synced_at": None,
            "sync_window_start": None,
            "sync_window_end": None,
        },
    )


async def seed_recording(
    session_maker: sessionmaker,
    *,
    recording_id: int,
    user_id: int,
    name: str,
    public_id: str,
    created_at: datetime,
    duration_seconds: float | None = None,
    status: str = "PROCESSED",
    calendar_event_id: int | None = None,
    is_archived: bool = False,
    is_deleted: bool = False,
) -> None:
    await execute_sql(
        session_maker,
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
            NULL,
            NULL,
            :duration_seconds,
            NULL,
            :status,
            NULL,
            0,
            100,
            NULL,
            NULL,
            NULL,
            :is_archived,
            :is_deleted,
            :user_id,
            :calendar_event_id
        )
        """,
        {
            "id": recording_id,
            "created_at": created_at,
            "updated_at": created_at,
            "name": name,
            "public_id": public_id,
            "meeting_uid": f"meeting-{recording_id}",
            "audio_path": f"/audio/{recording_id}.wav",
            "duration_seconds": duration_seconds,
            "status": status,
            "is_archived": is_archived,
            "is_deleted": is_deleted,
            "user_id": user_id,
            "calendar_event_id": calendar_event_id,
        },
    )


async def seed_global_speaker(
    session_maker: sessionmaker,
    *,
    speaker_id: int,
    name: str,
) -> None:
    await execute_sql(
        session_maker,
        """
        INSERT INTO global_speakers (id, name)
        VALUES (:id, :name)
        """,
        {
            "id": speaker_id,
            "name": name,
        },
    )


async def seed_recording_speaker(
    session_maker: sessionmaker,
    *,
    row_id: int,
    recording_id: int,
    diarization_label: str,
    global_speaker_id: int | None = None,
    local_name: str | None = None,
    name: str | None = None,
    merged_into_id: int | None = None,
    speaker_status: str | None = None,
) -> None:
    await execute_sql(
        session_maker,
        """
        INSERT INTO recording_speakers (
            id,
            recording_id,
            global_speaker_id,
            diarization_label,
            local_name,
            name,
            merged_into_id,
            speaker_status
        ) VALUES (
            :id,
            :recording_id,
            :global_speaker_id,
            :diarization_label,
            :local_name,
            :name,
            :merged_into_id,
            COALESCE(:speaker_status, 'active')
        )
        """,
        {
            "id": row_id,
            "recording_id": recording_id,
            "global_speaker_id": global_speaker_id,
            "diarization_label": diarization_label,
            "local_name": local_name,
            "name": name,
            "merged_into_id": merged_into_id,
            "speaker_status": speaker_status,
        },
    )


async def seed_tag(
    session_maker: sessionmaker,
    *,
    tag_id: int,
    name: str,
    user_id: int | None = 1,
    color: str | None = None,
) -> None:
    await execute_sql(
        session_maker,
        """
        INSERT INTO tags (id, created_at, updated_at, name, color, user_id)
        VALUES (:id, :created_at, :updated_at, :name, :color, :user_id)
        """,
        {
            "id": tag_id,
            "created_at": TEST_TIMESTAMP,
            "updated_at": TEST_TIMESTAMP,
            "name": name,
            "color": color,
            "user_id": user_id,
        },
    )


async def seed_user_task_tag(
    session_maker: sessionmaker,
    *,
    row_id: int,
    task_id: int,
    tag_id: int,
) -> None:
    await execute_sql(
        session_maker,
        """
        INSERT INTO user_task_tags (
            id,
            created_at,
            updated_at,
            task_id,
            tag_id
        ) VALUES (
            :id,
            :created_at,
            :updated_at,
            :task_id,
            :tag_id
        )
        """,
        {
            "id": row_id,
            "created_at": TEST_TIMESTAMP,
            "updated_at": TEST_TIMESTAMP,
            "task_id": task_id,
            "tag_id": tag_id,
        },
    )


async def seed_user_task_recording(
    session_maker: sessionmaker,
    *,
    row_id: int,
    task_id: int,
    recording_id: int,
) -> None:
    await execute_sql(
        session_maker,
        """
        INSERT INTO user_task_recordings (
            id,
            created_at,
            updated_at,
            task_id,
            recording_id
        ) VALUES (
            :id,
            :created_at,
            :updated_at,
            :task_id,
            :recording_id
        )
        """,
        {
            "id": row_id,
            "created_at": TEST_TIMESTAMP,
            "updated_at": TEST_TIMESTAMP,
            "task_id": task_id,
            "recording_id": recording_id,
        },
    )


async def seed_recording_tag(
    session_maker: sessionmaker,
    *,
    row_id: int,
    recording_id: int,
    tag_id: int,
) -> None:
    await execute_sql(
        session_maker,
        """
        INSERT INTO recording_tags (id, recording_id, tag_id)
        VALUES (:id, :recording_id, :tag_id)
        """,
        {
            "id": row_id,
            "recording_id": recording_id,
            "tag_id": tag_id,
        },
    )


@pytest.fixture
async def api_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    for env_key in (
        "WEB_APP_URL",
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "MICROSOFT_OAUTH_CLIENT_ID",
        "MICROSOFT_OAUTH_CLIENT_SECRET",
        "MICROSOFT_OAUTH_TENANT_ID",
    ):
        monkeypatch.delenv(env_key, raising=False)

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
    def _override(
        user_id: int,
        username: str = "alice",
        settings: dict[str, object] | None = None,
    ) -> None:
        api_app.dependency_overrides[get_current_user] = lambda: build_test_user(
            user_id,
            username,
            settings,
        )

    return _override


@pytest.fixture
def override_current_admin_user(api_app: FastAPI):
    def _override(user_id: int, username: str = "admin") -> None:
        api_app.dependency_overrides[get_current_admin_user] = lambda: User(
            id=user_id,
            username=username,
            hashed_password="hashed-password",
            role="admin",
            is_active=True,
            is_superuser=False,
            force_password_change=False,
        )

    return _override


@pytest.mark.anyio
async def test_calendar_overview_hides_admin_provider_metadata(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    await seed_provider_config(test_session_maker, provider="google")
    override_current_user(1)

    response = await client.get("/api/v1/calendar")

    assert response.status_code == 200
    payload = response.json()
    google_provider = next(
        provider for provider in payload["providers"] if provider["provider"] == "google"
    )
    assert google_provider == {
        "provider": "google",
        "display_name": "Google",
        "configured": True,
    }
    assert "client_id" not in google_provider
    assert "tenant_id" not in google_provider
    assert "has_client_secret" not in google_provider
    assert "source" not in google_provider
    assert "enabled" not in google_provider


@pytest.mark.anyio
async def test_admin_provider_status_uses_explicit_web_app_url_for_redirect_uri(
    client: AsyncClient,
    override_current_admin_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEB_APP_URL", "https://nojoin.example.com")
    override_current_admin_user(1)

    response = await client.get("/api/v1/calendar/admin/providers")

    assert response.status_code == 200
    google_provider = next(
        provider for provider in response.json() if provider["provider"] == "google"
    )
    assert (
        google_provider["redirect_uri"]
        == "https://nojoin.example.com/api/v1/calendar/oauth/google/callback"
    )


@pytest.mark.anyio
async def test_admin_provider_status_uses_configured_web_app_url_when_env_is_unset(
    client: AsyncClient,
    override_current_admin_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WEB_APP_URL", raising=False)
    monkeypatch.setitem(
        config_manager.config,
        "web_app_url",
        "https://config.nojoin.example.com",
    )
    override_current_admin_user(1)

    response = await client.get("/api/v1/calendar/admin/providers")

    assert response.status_code == 200
    microsoft_provider = next(
        provider for provider in response.json() if provider["provider"] == "microsoft"
    )
    assert (
        microsoft_provider["redirect_uri"]
        == "https://config.nojoin.example.com/api/v1/calendar/oauth/microsoft/callback"
    )


@pytest.mark.anyio
async def test_admin_provider_status_falls_back_to_environment_when_database_secret_is_invalid(
    client: AsyncClient,
    override_current_admin_user,
    test_session_maker: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await execute_sql(
        test_session_maker,
        """
        INSERT INTO calendar_provider_configs (
            id,
            created_at,
            updated_at,
            provider,
            client_id,
            client_secret_encrypted,
            tenant_id,
            enabled
        ) VALUES (
            :id,
            :created_at,
            :updated_at,
            :provider,
            :client_id,
            :client_secret_encrypted,
            :tenant_id,
            :enabled
        )
        """,
        {
            "id": 1,
            "created_at": TEST_TIMESTAMP,
            "updated_at": TEST_TIMESTAMP,
            "provider": "google",
            "client_id": "stale-google-client-id",
            "client_secret_encrypted": "not-a-valid-encrypted-secret",
            "tenant_id": None,
            "enabled": True,
        },
    )
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "env-google-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "env-google-client-secret")
    override_current_admin_user(1)

    caplog.set_level(logging.WARNING, logger="backend.services.calendar_service")
    response = await client.get("/api/v1/calendar/admin/providers")

    assert response.status_code == 200
    google_provider = next(
        provider for provider in response.json() if provider["provider"] == "google"
    )
    assert google_provider["source"] == "environment"
    assert google_provider["configured"] is True
    assert google_provider["client_id"] == "env-google-client-id"
    assert google_provider["has_client_secret"] is True
    assert "could not be decrypted" in caplog.text
    assert (
        await fetch_scalar(
            test_session_maker,
            "SELECT COUNT(*) FROM calendar_provider_configs WHERE provider = :provider",
            {"provider": "google"},
        )
        == 0
    )


@pytest.mark.anyio
async def test_calendar_overview_resets_connections_with_invalid_encrypted_tokens(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_calendar_connection(
        test_session_maker,
        connection_id=301,
        user_id=1,
        access_token_encrypted="not-a-valid-encrypted-secret",
    )
    await seed_calendar_source(
        test_session_maker,
        calendar_id=401,
        connection_id=301,
        provider_calendar_id="primary",
        name="Primary",
        colour="#ff6600",
    )
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "env-google-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "env-google-client-secret")
    override_current_user(1)

    response = await client.get("/api/v1/calendar")

    assert response.status_code == 200
    google_provider = next(
        provider for provider in response.json()["providers"] if provider["provider"] == "google"
    )
    assert google_provider == {
        "provider": "google",
        "display_name": "Google",
        "configured": True,
    }
    assert response.json()["connections"] == []
    assert (
        await fetch_scalar(
            test_session_maker,
            "SELECT COUNT(*) FROM calendar_connections WHERE id = :connection_id",
            {"connection_id": 301},
        )
        == 0
    )
    assert (
        await fetch_scalar(
            test_session_maker,
            "SELECT COUNT(*) FROM calendar_sources WHERE connection_id = :connection_id",
            {"connection_id": 301},
        )
        == 0
    )


@pytest.mark.anyio
async def test_calendar_dashboard_resets_connections_with_invalid_encrypted_tokens(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_calendar_connection(
        test_session_maker,
        connection_id=302,
        user_id=1,
        access_token_encrypted="not-a-valid-encrypted-secret",
    )
    await seed_calendar_source(
        test_session_maker,
        calendar_id=402,
        connection_id=302,
        provider_calendar_id="primary",
        name="Primary",
        colour="#ff6600",
    )
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "env-google-client-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "env-google-client-secret")
    override_current_user(1)

    response = await client.get(
        "/api/v1/calendar/dashboard",
        params={"month": "2026-04", "timezone": "UTC"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider_configured"] is True
    assert payload["connection_count"] == 0
    assert payload["state"] == "no_accounts"


@pytest.mark.anyio
async def test_calendar_dashboard_includes_unlinked_recordings_and_deduplicates_linked_recordings(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    await seed_provider_config(test_session_maker, provider="google")
    await seed_calendar_connection(test_session_maker, connection_id=301, user_id=1)
    await seed_calendar_source(
        test_session_maker,
        calendar_id=401,
        connection_id=301,
        provider_calendar_id="primary",
        name="Work",
        colour="#4285f4",
    )
    await execute_sql(
        test_session_maker,
        """
        INSERT INTO calendar_events (
            id,
            created_at,
            updated_at,
            calendar_id,
            provider_event_id,
            title,
            status,
            is_all_day,
            starts_at,
            ends_at,
            start_date,
            end_date,
            location_text,
            description,
            attendees,
            meeting_url,
            source_url,
            external_updated_at
        ) VALUES (
            :id,
            :created_at,
            :updated_at,
            :calendar_id,
            :provider_event_id,
            :title,
            'confirmed',
            0,
            :starts_at,
            :ends_at,
            NULL,
            NULL,
            NULL,
            NULL,
            NULL,
            NULL,
            NULL,
            NULL
        )
        """,
        {
            "id": 501,
            "created_at": TEST_TIMESTAMP,
            "updated_at": TEST_TIMESTAMP,
            "calendar_id": 401,
            "provider_event_id": "event-501",
            "title": "Roadmap review",
            "starts_at": datetime(2026, 4, 18, 9, 0, 0),
            "ends_at": datetime(2026, 4, 18, 10, 0, 0),
        },
    )
    await seed_recording(
        test_session_maker,
        recording_id=601,
        user_id=1,
        name="Roadmap follow-up",
        public_id="recording-601",
        created_at=datetime(2026, 4, 18, 13, 0, 0),
        duration_seconds=1800,
    )
    await seed_global_speaker(
        test_session_maker,
        speaker_id=801,
        name="Alex Morgan",
    )
    await seed_recording_speaker(
        test_session_maker,
        row_id=901,
        recording_id=601,
        diarization_label="SPEAKER_00",
        global_speaker_id=801,
    )
    await seed_recording_speaker(
        test_session_maker,
        row_id=902,
        recording_id=601,
        diarization_label="SPEAKER_01",
        local_name="Blair Chen",
    )
    # Seed an inactive speaker (should be excluded)
    await seed_recording_speaker(
        test_session_maker,
        row_id=903,
        recording_id=601,
        diarization_label="SPEAKER_02",
        local_name="Inactive Speaker",
        speaker_status="inactive",
    )
    # Seed an active, unnamed live speaker (should be excluded)
    await seed_recording_speaker(
        test_session_maker,
        row_id=904,
        recording_id=601,
        diarization_label="LIVE_01",
        local_name=None,
        speaker_status="active",
    )
    # Seed an active, named live speaker (should be included)
    await seed_recording_speaker(
        test_session_maker,
        row_id=905,
        recording_id=601,
        diarization_label="LIVE_02",
        local_name="Former UK Government Official",
        speaker_status="active",
    )
    await seed_tag(
        test_session_maker,
        tag_id=1001,
        name="Customer",
        color="orange",
    )
    await seed_recording_tag(
        test_session_maker,
        row_id=1101,
        recording_id=601,
        tag_id=1001,
    )
    await seed_recording(
        test_session_maker,
        recording_id=602,
        user_id=1,
        name="Linked roadmap meeting",
        public_id="recording-602",
        created_at=datetime(2026, 4, 18, 9, 5, 0),
        duration_seconds=3300,
        calendar_event_id=501,
    )
    override_current_user(1)

    response = await client.get(
        "/api/v1/calendar/dashboard",
        params={"month": "2026-04", "timezone": "UTC"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "ready"
    assert payload["day_counts"] == [{"date": "2026-04-18", "count": 2}]
    assert payload["recording_items"] == [
        {
            "id": "recording-601",
            "name": "Roadmap follow-up",
            "starts_at": "2026-04-18T13:00:00Z",
            "ends_at": "2026-04-18T13:30:00Z",
            "duration_seconds": 1800.0,
            "status": "PROCESSED",
            "speaker_names": ["Alex Morgan", "Blair Chen", "Former UK Government Official"],
            "tags": [
                {
                    "id": 1001,
                    "name": "Customer",
                    "color": "orange",
                }
            ],
        }
    ]
    assert len(payload["agenda_items"]) == 1
    assert payload["agenda_items"][0]["title"] == "Roadmap review"
    assert payload["agenda_items"][0]["linked_recordings"] == [
        {
            "id": "recording-602",
            "name": "Linked roadmap meeting",
            "starts_at": "2026-04-18T09:05:00Z",
            "ends_at": "2026-04-18T10:00:00Z",
            "duration_seconds": 3300.0,
            "status": "PROCESSED",
            "speaker_names": [],
            "tags": [],
        }
    ]


@pytest.mark.anyio
async def test_calendar_dashboard_surfaces_recordings_without_calendar_connections(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    await seed_recording(
        test_session_maker,
        recording_id=701,
        user_id=1,
        name="Standalone meeting",
        public_id="recording-701",
        created_at=datetime(2026, 4, 7, 12, 0, 0),
        duration_seconds=2700,
    )
    override_current_user(1)

    response = await client.get(
        "/api/v1/calendar/dashboard",
        params={"month": "2026-04", "timezone": "UTC"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider_configured"] is False
    assert payload["connection_count"] == 0
    assert payload["state"] == "ready"
    assert payload["agenda_items"] == []
    assert payload["recording_items"] == [
        {
            "id": "recording-701",
            "name": "Standalone meeting",
            "starts_at": "2026-04-07T12:00:00Z",
            "ends_at": "2026-04-07T12:45:00Z",
            "duration_seconds": 2700.0,
            "status": "PROCESSED",
            "speaker_names": [],
            "tags": [],
        }
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("raised_exception", "expected_location"),
    [
        (
            HTTPException(status_code=400, detail="Provider is not configured"),
            "/settings?tab=account&calendar=config-error&provider=google",
        ),
        (
            RuntimeError("boom"),
            "/settings?tab=account&calendar=error&provider=google",
        ),
    ],
)
async def test_oauth_start_failures_redirect_to_relative_account_settings(
    client: AsyncClient,
    override_current_user,
    monkeypatch: pytest.MonkeyPatch,
    raised_exception: Exception,
    expected_location: str,
) -> None:
    override_current_user(1)

    async def failing_start_authorisation(*args, **kwargs):
        raise raised_exception

    monkeypatch.setattr(
        "backend.api.v1.endpoints.calendar.start_authorisation",
        failing_start_authorisation,
    )

    response = await client.get("/api/v1/calendar/oauth/google/start")

    assert response.status_code == 303
    assert response.headers["location"] == expected_location


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "params", "expected_location"),
    [
        (
            "google",
            {"error": "access_denied"},
            "/settings?tab=account&calendar=cancelled&provider=google",
        ),
        (
            "microsoft",
            {
                "error": "server_error",
                "error_description": "AADSTS50194: application is not configured as multi-tenant",
            },
            "/settings?tab=account&calendar=tenant-config-error&provider=microsoft",
        ),
    ],
)
async def test_oauth_callback_input_errors_redirect_to_relative_account_settings(
    client: AsyncClient,
    override_current_user,
    provider: str,
    params: dict[str, str],
    expected_location: str,
) -> None:
    override_current_user(1)

    response = await client.get(f"/api/v1/calendar/oauth/{provider}/callback", params=params)

    assert response.status_code == 303
    assert response.headers["location"] == expected_location


@pytest.mark.anyio
async def test_oauth_callback_exception_redirects_to_relative_account_settings(
    client: AsyncClient,
    override_current_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    override_current_user(1)

    async def failing_handle_callback(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "backend.api.v1.endpoints.calendar.handle_callback",
        failing_handle_callback,
    )

    response = await client.get(
        "/api/v1/calendar/oauth/google/callback",
        params={"code": "code", "state": "state"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?tab=account&calendar=error&provider=google"


@pytest.mark.anyio
async def test_oauth_callback_success_redirects_to_relative_account_settings(
    client: AsyncClient,
    override_current_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    override_current_user(1)

    async def fake_handle_callback(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "backend.api.v1.endpoints.calendar.handle_callback",
        fake_handle_callback,
    )

    response = await client.get(
        "/api/v1/calendar/oauth/google/callback",
        params={"code": "code", "state": "state"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?tab=account&calendar=success&provider=google"


@pytest.mark.anyio
async def test_read_tasks_returns_only_current_users_tasks(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    await seed_task(test_session_maker, task_id=101, user_id=1, title="Own task")
    await seed_task(test_session_maker, task_id=202, user_id=2, title="Other task")
    override_current_user(1)

    response = await client.get("/api/v1/tasks/")

    assert response.status_code == 200
    assert [task["id"] for task in response.json()] == [101]


@pytest.mark.anyio
async def test_read_tasks_hides_archived_tasks_by_default(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    await seed_task(test_session_maker, task_id=101, user_id=1, title="Visible task")
    await seed_task(
        test_session_maker,
        task_id=202,
        user_id=1,
        title="Archived task",
        archived_at=TEST_TIMESTAMP,
    )
    override_current_user(1)

    response = await client.get("/api/v1/tasks/")

    assert response.status_code == 200
    assert [task["id"] for task in response.json()] == [101]


@pytest.mark.anyio
async def test_read_tasks_can_return_archived_tasks(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    await seed_task(test_session_maker, task_id=101, user_id=1, title="Visible task")
    await seed_task(
        test_session_maker,
        task_id=202,
        user_id=1,
        title="Archived task",
        archived_at=TEST_TIMESTAMP,
    )
    override_current_user(1)

    response = await client.get("/api/v1/tasks/", params={"status": "archived"})

    assert response.status_code == 200
    payload = response.json()
    assert [task["id"] for task in payload] == [202]
    assert payload[0]["archived_at"] is not None


@pytest.mark.anyio
async def test_read_tasks_returns_utc_instants_for_due_dates(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    await seed_task(
        test_session_maker,
        task_id=101,
        user_id=1,
        title="Own task",
        due_at=datetime(2026, 4, 13, 12, 30, 0),
    )
    override_current_user(1)

    response = await client.get("/api/v1/tasks/")

    assert response.status_code == 200
    payload = response.json()[0]
    assert datetime.fromisoformat(payload["due_at"].replace("Z", "+00:00")) == datetime(
        2026,
        4,
        13,
        12,
        30,
        0,
        tzinfo=UTC,
    )
    assert datetime.fromisoformat(payload["created_at"].replace("Z", "+00:00")).tzinfo == UTC
    assert datetime.fromisoformat(payload["updated_at"].replace("Z", "+00:00")).tzinfo == UTC


@pytest.mark.anyio
async def test_create_task_normalises_due_at_using_user_timezone(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    override_current_user(1, settings={"timezone": "Europe/London"})

    response = await client.post(
        "/api/v1/tasks/",
        json={"title": "Join planning call", "due_at": "2026-04-13T13:30:00"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert datetime.fromisoformat(payload["due_at"].replace("Z", "+00:00")) == datetime(
        2026,
        4,
        13,
        12,
        30,
        0,
        tzinfo=UTC,
    )

    async with test_session_maker() as session:
        stored_due_at = (
            await session.execute(
                text("SELECT due_at FROM user_tasks WHERE id = :task_id"),
                {"task_id": payload["id"]},
            )
        ).scalar_one()

    assert datetime.fromisoformat(str(stored_due_at)) == datetime(2026, 4, 13, 12, 30, 0)


@pytest.mark.anyio
async def test_create_task_assigns_existing_recording_tags(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    await seed_tag(test_session_maker, tag_id=301, name="Follow-up", color="orange")
    override_current_user(1)

    response = await client.post(
        "/api/v1/tasks/",
        json={
            "title": "Join planning call",
            "body": "Ask about rollout risks.",
            "tag_ids": [301],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["body"] == "Ask about rollout risks."
    assert payload["tags"] == [
        {
            "id": 301,
            "created_at": TEST_TIMESTAMP.isoformat().replace("+00:00", "Z"),
            "updated_at": TEST_TIMESTAMP.isoformat().replace("+00:00", "Z"),
            "name": "Follow-up",
            "color": "orange",
            "user_id": 1,
            "parent_id": None,
        }
    ]


@pytest.mark.anyio
async def test_create_task_links_owned_recordings(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    await seed_recording(
        test_session_maker,
        recording_id=401,
        user_id=1,
        name="Planning meeting",
        public_id="rec-planning",
        created_at=TEST_TIMESTAMP,
    )
    await seed_recording(
        test_session_maker,
        recording_id=402,
        user_id=1,
        name="Retrospective",
        public_id="rec-retro",
        created_at=TEST_TIMESTAMP,
    )
    override_current_user(1)

    response = await client.post(
        "/api/v1/tasks/",
        json={
            "title": "Follow up",
            "body": "Review both meetings.",
            "recording_ids": ["rec-planning", "rec-retro"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["body"] == "Review both meetings."
    assert payload["linked_recordings"] == [
        {
            "id": "rec-planning",
            "name": "Planning meeting",
            "created_at": TEST_TIMESTAMP.replace(tzinfo=UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "duration_seconds": None,
            "status": "PROCESSED",
            "is_archived": False,
            "is_deleted": False,
        },
        {
            "id": "rec-retro",
            "name": "Retrospective",
            "created_at": TEST_TIMESTAMP.replace(tzinfo=UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "duration_seconds": None,
            "status": "PROCESSED",
            "is_archived": False,
            "is_deleted": False,
        },
    ]


@pytest.mark.anyio
async def test_create_task_rejects_other_users_recording(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    await seed_recording(
        test_session_maker,
        recording_id=401,
        user_id=2,
        name="Other meeting",
        public_id="rec-other",
        created_at=TEST_TIMESTAMP,
    )
    override_current_user(1)

    response = await client.post(
        "/api/v1/tasks/",
        json={"title": "Follow up", "recording_ids": ["rec-other"]},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Task recording not found"}


@pytest.mark.anyio
async def test_create_task_rejects_other_users_tag(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    await seed_tag(test_session_maker, tag_id=301, name="Other tag", user_id=2)
    override_current_user(1)

    response = await client.post(
        "/api/v1/tasks/",
        json={"title": "Join planning call", "tag_ids": [301]},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Task tag not found"}


@pytest.mark.anyio
async def test_update_task_archives_and_unarchives_owned_task(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    await seed_task(test_session_maker, task_id=202, user_id=1, title="Own task")
    override_current_user(1)

    archive_response = await client.patch("/api/v1/tasks/202", json={"archived": True})

    assert archive_response.status_code == 200
    assert archive_response.json()["archived_at"] is not None

    unarchive_response = await client.patch("/api/v1/tasks/202", json={"archived": False})

    assert unarchive_response.status_code == 200
    assert unarchive_response.json()["archived_at"] is None


@pytest.mark.anyio
async def test_update_task_replaces_tags(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    await seed_task(test_session_maker, task_id=202, user_id=1, title="Own task")
    await seed_tag(test_session_maker, tag_id=301, name="Old", color="gray")
    await seed_tag(test_session_maker, tag_id=302, name="New", color="orange")
    await seed_user_task_tag(test_session_maker, row_id=401, task_id=202, tag_id=301)
    override_current_user(1)

    response = await client.patch("/api/v1/tasks/202", json={"tag_ids": [302]})

    assert response.status_code == 200
    assert [tag["id"] for tag in response.json()["tags"]] == [302]


@pytest.mark.anyio
async def test_update_task_replaces_linked_recordings(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    await seed_task(test_session_maker, task_id=202, user_id=1, title="Own task")
    await seed_recording(
        test_session_maker,
        recording_id=401,
        user_id=1,
        name="Old meeting",
        public_id="rec-old",
        created_at=TEST_TIMESTAMP,
    )
    await seed_recording(
        test_session_maker,
        recording_id=402,
        user_id=1,
        name="New meeting",
        public_id="rec-new",
        created_at=TEST_TIMESTAMP,
    )
    await seed_user_task_recording(
        test_session_maker,
        row_id=501,
        task_id=202,
        recording_id=401,
    )
    override_current_user(1)

    response = await client.patch(
        "/api/v1/tasks/202",
        json={"recording_ids": ["rec-new"]},
    )

    assert response.status_code == 200
    assert [recording["id"] for recording in response.json()["linked_recordings"]] == [
        "rec-new"
    ]


@pytest.mark.anyio
async def test_update_other_users_task_returns_404(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    await seed_task(test_session_maker, task_id=202, user_id=2, title="Other task")
    override_current_user(1)

    response = await client.patch("/api/v1/tasks/202", json={"title": "Updated"})

    assert response.status_code == 404
    assert response.json() == {"detail": "Task not found"}


@pytest.mark.anyio
async def test_delete_other_users_task_returns_404(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    await seed_task(test_session_maker, task_id=202, user_id=2, title="Other task")
    override_current_user(1)

    response = await client.delete("/api/v1/tasks/202")

    assert response.status_code == 404
    assert response.json() == {"detail": "Task not found"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("put", "/api/v1/calendar/connections/301/calendars", {"selected_calendar_ids": []}),
        ("put", "/api/v1/calendar/connections/301/calendars/401/colour", {"colour": "emerald"}),
        ("post", "/api/v1/calendar/connections/301/sync", None),
        ("delete", "/api/v1/calendar/connections/301", None),
    ],
)
async def test_calendar_connection_mutations_require_ownership(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
    method: str,
    path: str,
    payload: dict[str, object] | None,
) -> None:
    await seed_calendar_connection(test_session_maker, connection_id=301, user_id=2)
    override_current_user(1)

    request_kwargs = {"json": payload} if payload is not None else {}
    response = await client.request(method.upper(), path, **request_kwargs)

    assert response.status_code == 404
    assert response.json() == {"detail": "Calendar connection not found"}


@pytest.mark.anyio
async def test_update_calendar_colour_returns_effective_and_provider_values(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    await seed_calendar_connection(test_session_maker, connection_id=301, user_id=1)
    await seed_calendar_source(
        test_session_maker,
        calendar_id=401,
        connection_id=301,
        provider_calendar_id="primary",
        name="Primary calendar",
        colour="#4285f4",
    )
    override_current_user(1)

    update_response = await client.put(
        "/api/v1/calendar/connections/301/calendars/401/colour",
        json={"colour": "emerald"},
    )

    assert update_response.status_code == 200
    updated_calendar = update_response.json()["calendars"][0]
    assert updated_calendar["colour"] == "emerald"
    assert updated_calendar["custom_colour"] == "emerald"
    assert updated_calendar["provider_colour"] == "#4285f4"

    clear_response = await client.put(
        "/api/v1/calendar/connections/301/calendars/401/colour",
        json={"colour": None},
    )

    assert clear_response.status_code == 200
    cleared_calendar = clear_response.json()["calendars"][0]
    assert cleared_calendar["colour"] == "#4285f4"
    assert cleared_calendar["custom_colour"] is None
    assert cleared_calendar["provider_colour"] == "#4285f4"
