from __future__ import annotations

import importlib
import json
import logging
import pkgutil
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, BigInteger, Integer, text
from sqlalchemy.dialects.sqlite.base import SQLiteDialect
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import DefaultClause
from sqlmodel import SQLModel

import backend.models
from backend.api.deps import get_current_admin_user, get_current_user, get_db
from backend.api.v1.api import api_router
from backend.core.encryption import encrypt_secret
from backend.models.user import User
from backend.utils.config_manager import config_manager

TEST_TIMESTAMP = datetime(2026, 4, 12, 10, 0, 0)

# Register every model so all foreign-key targets resolve, then build the subset
# of tables these tests need straight from the ORM metadata. This keeps the test
# schema in lock-step with the models instead of a hand-maintained DDL copy that
# silently drifts whenever a column or table is added.
for _model_info in pkgutil.iter_modules(backend.models.__path__):
    importlib.import_module(f"backend.models.{_model_info.name}")

TEST_TABLE_NAMES = [
    "calendar_provider_configs",
    "calendar_connections",
    "calendar_sources",
    "calendar_events",
    "calendar_push_channels",
    "recordings",
    "global_speakers",
    "recording_speakers",
    "tags",
    "recording_tags",
    "user_tasks",
    "user_task_tags",
    "user_task_recordings",
]


def _sqlite_server_default(column) -> DefaultClause | None:
    """A SQLite server default mirroring the model's Python-side default.

    Raw-SQL seeds bypass ORM defaults, so scalar column defaults (and the
    timestamp mixin) need an equivalent server default to satisfy NOT NULL.
    """
    if column.name in ("created_at", "updated_at"):
        return DefaultClause(text("CURRENT_TIMESTAMP"))
    python_default = column.default
    if python_default is None or not getattr(python_default, "is_scalar", False):
        return None
    value = python_default.arg
    if isinstance(value, bool):
        return DefaultClause(text("1" if value else "0"))
    if isinstance(value, (int, float)):
        return DefaultClause(text(str(value)))
    if isinstance(value, str):
        return DefaultClause(text(f"'{value}'"))
    return None


def _create_test_schema(sync_connection) -> None:
    """Create the tables these tests use from the ORM metadata on SQLite.

    A few models carry Postgres-only column types (JSONB, pgvector) and server
    defaults (e.g. ``'[]'::json``) that SQLite cannot render. Those are
    temporarily coerced to SQLite-compatible equivalents for the CREATE and then
    restored, so the shared metadata is left untouched. SQLite-valid defaults
    (such as boolean ``false``) are preserved so inserts that omit the column
    still get the right value. Foreign keys to tables outside the subset are
    harmless because SQLite does not enforce them.
    """
    tables = [SQLModel.metadata.tables[name] for name in TEST_TABLE_NAMES]
    sqlite_dialect = SQLiteDialect()
    restore: list[tuple] = []
    for table in tables:
        for column in table.columns:
            server_default = column.server_default
            if server_default is not None and "::" in str(
                getattr(server_default, "arg", "")
            ):
                restore.append((column, "server_default", server_default))
                column.server_default = None
            elif server_default is None:
                replacement = _sqlite_server_default(column)
                if replacement is not None:
                    restore.append((column, "server_default", None))
                    column.server_default = replacement
            # Any column still NOT NULL without a server default relies on a
            # callable ORM default (e.g. a uuid factory) that raw-SQL seeds
            # bypass. Relax it to nullable so seeds may omit it; real inserts go
            # through the ORM and still populate it.
            if (
                not column.primary_key
                and not column.nullable
                and column.server_default is None
            ):
                restore.append((column, "nullable", column.nullable))
                column.nullable = True
            if isinstance(column.type, BigInteger):
                # SQLite only autoincrements a single-column INTEGER PRIMARY KEY;
                # a BIGINT PK is a plain NOT NULL column. Integer keeps 64-bit
                # storage while restoring rowid autoincrement.
                restore.append((column, "type", column.type))
                column.type = Integer()
            else:
                try:
                    column.type.compile(dialect=sqlite_dialect)
                except Exception:  # noqa: BLE001 -- boundary: Postgres-only type on SQLite
                    restore.append((column, "type", column.type))
                    column.type = JSON()
    try:
        SQLModel.metadata.create_all(sync_connection, tables=tables)
    finally:
        for column, attribute, value in restore:
            setattr(column, attribute, value)


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
        await connection.run_sync(_create_test_schema)

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
    async with AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as async_client:
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
        provider
        for provider in payload["providers"]
        if provider["provider"] == "google"
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
        provider
        for provider in response.json()["providers"]
        if provider["provider"] == "google"
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
            "speaker_names": [
                "Alex Morgan",
                "Blair Chen",
                "Former UK Government Official",
            ],
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
            "/settings/integrations?calendar=config-error&provider=google",
        ),
        (
            RuntimeError("boom"),
            "/settings/integrations?calendar=error&provider=google",
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
            "/settings/integrations?calendar=cancelled&provider=google",
        ),
        (
            "microsoft",
            {
                "error": "server_error",
                "error_description": "AADSTS50194: application is not configured as multi-tenant",
            },
            "/settings/integrations?calendar=tenant-config-error&provider=microsoft",
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

    response = await client.get(
        f"/api/v1/calendar/oauth/{provider}/callback", params=params
    )

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
    assert (
        response.headers["location"]
        == "/settings/integrations?calendar=error&provider=google"
    )


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
    assert (
        response.headers["location"]
        == "/settings/integrations?calendar=success&provider=google"
    )


# What a provider hands back during a connection, for the tests below. None of
# these may reach the log: they are the credentials and the account's address.
CONNECT_ACCESS_TOKEN = "provider-access-token-1234"
CONNECT_REFRESH_TOKEN = "provider-refresh-token-5678"
CONNECT_CODE = "authorisation-code-9012"
CONNECT_STATE = "oauth-state-3456"
CONNECT_EMAIL = "alice@example.com"
GOOGLE_CALENDAR_LIST_FORBIDDEN = {
    "error": {
        "code": 403,
        "message": "The caller does not have permission",
        "errors": [
            {
                "message": "The caller does not have permission",
                "domain": "global",
                "reason": "forbidden",
            }
        ],
        "status": "PERMISSION_DENIED",
    }
}
GRAPH_CALENDAR_LIST_DENIED = {
    "error": {
        "code": "ErrorAccessDenied",
        "message": "Access is denied. Check credentials and try again.",
        "innerError": {"request-id": "00000000-0000-0000-0000-000000000004"},
    }
}


def _backend_log(caplog: pytest.LogCaptureFixture) -> str:
    """What this codebase logged, without httpx's request lines, which carry
    the test client's own callback URL with its code and state."""
    return "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("backend")
    )


def _answer_provider(calendar_list_refusal: dict) -> httpx.MockTransport:
    """Google or Microsoft accepting the code and the account lookup, then
    refusing the calendar list with 403."""

    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": CONNECT_ACCESS_TOKEN,
                    "refresh_token": CONNECT_REFRESH_TOKEN,
                    "expires_in": 3599,
                    "scope": "openid email profile calendar.readonly",
                },
            )
        if path == "/v1/userinfo":
            return httpx.Response(
                200,
                json={"sub": "account-1", "email": CONNECT_EMAIL, "name": "Alice"},
            )
        if path == "/v1.0/me":
            return httpx.Response(
                200,
                json={"id": "account-1", "mail": CONNECT_EMAIL, "displayName": "Alice"},
            )
        if path in ("/calendar/v3/users/me/calendarList", "/v1.0/me/calendars"):
            return httpx.Response(403, json=calendar_list_refusal)
        raise AssertionError(f"unexpected provider request {request.method} {path}")

    return httpx.MockTransport(answer)


@pytest.fixture
def stub_provider(monkeypatch: pytest.MonkeyPatch):
    """Stand in for the provider and the sign-in state a callback needs.

    The service opens its own ``httpx.AsyncClient`` per request, so the class
    is swapped for one that defaults to the stub transport; the test client
    passes its own transport and is unaffected. The state lookup and the rate
    limit would otherwise reach for Redis.
    """
    from backend.api.v1.endpoints import calendar as calendar_endpoint
    from backend.services.calendar_service import sync as calendar_sync

    real_client = httpx.AsyncClient

    async def _no_rate_limit(*_args, **_kwargs) -> None:
        return None

    def _stub(provider: str, calendar_list_refusal: dict) -> None:
        transport = _answer_provider(calendar_list_refusal)

        class StubProviderClient(real_client):
            def __init__(self, *args, **kwargs) -> None:
                kwargs.setdefault("transport", transport)
                super().__init__(*args, **kwargs)

        async def _pop_state(state: str) -> dict[str, object] | None:
            if state != CONNECT_STATE:
                return None
            return {"user_id": 1, "provider": provider, "code_verifier": "verifier"}

        monkeypatch.setattr(httpx, "AsyncClient", StubProviderClient)
        monkeypatch.setattr(calendar_sync, "_pop_oauth_state", _pop_state)
        monkeypatch.setattr(calendar_endpoint, "enforce_rate_limit", _no_rate_limit)

    return _stub


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "refusal", "reason", "logged"),
    [
        (
            "google",
            GOOGLE_CALENDAR_LIST_FORBIDDEN,
            "forbidden",
            "HTTP 403 forbidden: The caller does not have permission",
        ),
        (
            "microsoft",
            GRAPH_CALENDAR_LIST_DENIED,
            "ErrorAccessDenied",
            "HTTP 403 ErrorAccessDenied: Access is denied. Check credentials and "
            "try again.",
        ),
    ],
)
async def test_oauth_callback_keeps_the_reason_a_provider_refused_the_calendars(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
    stub_provider,
    caplog: pytest.LogCaptureFixture,
    provider: str,
    refusal: dict,
    reason: str,
    logged: str,
) -> None:
    stub_provider(provider, refusal)
    await seed_provider_config(test_session_maker, provider=provider)
    override_current_user(1)
    caplog.set_level(logging.DEBUG, logger="backend")

    response = await client.get(
        f"/api/v1/calendar/oauth/{provider}/callback",
        params={"code": CONNECT_CODE, "state": CONNECT_STATE},
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"/settings/integrations?calendar=error&provider={provider}&reason={reason}"
    )
    logged_text = _backend_log(caplog)
    assert (
        f"Connecting a {provider} calendar failed at the calendar list step: {logged}"
        in logged_text
    )
    for secret in (
        CONNECT_ACCESS_TOKEN,
        CONNECT_REFRESH_TOKEN,
        CONNECT_CODE,
        CONNECT_STATE,
        CONNECT_EMAIL,
    ):
        assert secret not in logged_text
    assert (
        await fetch_scalar(
            test_session_maker, "SELECT COUNT(*) FROM calendar_connections"
        )
        == 0
    )


@pytest.mark.anyio
async def test_oauth_callback_logs_and_carries_a_refused_sign_in(
    client: AsyncClient,
    override_current_user,
    caplog: pytest.LogCaptureFixture,
) -> None:
    override_current_user(1)
    caplog.set_level(logging.DEBUG, logger="backend")

    response = await client.get(
        "/api/v1/calendar/oauth/microsoft/callback",
        params={
            "error": "unauthorized_client",
            "error_description": (
                f"AADSTS700016: Application not found for {CONNECT_EMAIL}."
            ),
        },
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/settings/integrations?calendar=error&provider=microsoft"
        "&reason=unauthorized_client"
    )
    logged_text = _backend_log(caplog)
    assert (
        "microsoft calendar sign-in came back with error unauthorized_client "
        "(AADSTS700016)"
    ) in logged_text
    assert CONNECT_EMAIL not in logged_text


@pytest.mark.anyio
async def test_oauth_callback_drops_a_reason_that_is_not_a_provider_code(
    client: AsyncClient,
    override_current_user,
    caplog: pytest.LogCaptureFixture,
) -> None:
    override_current_user(1)
    caplog.set_level(logging.DEBUG, logger="backend")

    response = await client.get(
        "/api/v1/calendar/oauth/google/callback",
        params={"error": "Call +44 20 7946 0000 to restore access"},
    )

    assert response.headers["location"] == (
        "/settings/integrations?calendar=error&provider=google"
    )
    logged_text = _backend_log(caplog)
    assert "google calendar sign-in came back with error (unrecognised)" in logged_text
    assert "7946" not in logged_text


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
async def test_read_tasks_filters_recording_and_task_ownership(
    client: AsyncClient,
    override_current_user: Callable[[int], None],
    test_session_maker: sessionmaker,
) -> None:
    for recording_id in (401, 402):
        await seed_recording(
            test_session_maker,
            recording_id=recording_id,
            user_id=1,
            name=f"Meeting {recording_id}",
            public_id=f"rec-{recording_id}",
            created_at=TEST_TIMESTAMP,
        )
    for task_id, user_id, recording_id in (
        (101, 1, 401),
        (102, 1, 402),
        (103, 2, 401),
        (104, 1, None),
    ):
        await seed_task(
            test_session_maker, task_id=task_id, user_id=user_id, title="Follow up"
        )
        if recording_id is not None:
            await seed_user_task_recording(
                test_session_maker,
                row_id=task_id,
                task_id=task_id,
                recording_id=recording_id,
            )
    override_current_user(1)

    for path in ("/api/v1/tasks", "/api/v1/tasks/"):
        response = await client.get(path, params={"recording_id": "rec-401"})
        assert response.status_code == 200
        assert [task["id"] for task in response.json()] == [101]
    unfiltered = await client.get("/api/v1/tasks/")
    assert unfiltered.status_code == 200
    assert {task["id"] for task in unfiltered.json()} == {101, 102, 104}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("public_id", "owner_id"), [("rec-foreign", 2), ("missing", None)]
)
async def test_read_tasks_requires_owned_recording(
    client: AsyncClient,
    override_current_user: Callable[[int], None],
    test_session_maker: sessionmaker,
    public_id: str,
    owner_id: int | None,
) -> None:
    if owner_id is not None:
        await seed_recording(
            test_session_maker,
            recording_id=401,
            user_id=owner_id,
            name="Someone else's meeting",
            public_id=public_id,
            created_at=TEST_TIMESTAMP,
        )
    override_current_user(1)

    response = await client.get("/api/v1/tasks/", params={"recording_id": public_id})

    assert response.status_code == 404
    assert response.json() == {"detail": "Recording not found"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "expected_ids"),
    [("open", {101}), ("archived", {103})],
)
async def test_read_tasks_recording_filter_preserves_statuses(
    client: AsyncClient,
    override_current_user: Callable[[int], None],
    test_session_maker: sessionmaker,
    status: str,
    expected_ids: set[int],
) -> None:
    await seed_recording(
        test_session_maker,
        recording_id=401,
        user_id=1,
        name="Meeting",
        public_id="rec-meeting",
        created_at=TEST_TIMESTAMP,
    )
    for task_id in (101, 102, 103, 104):
        await seed_task(
            test_session_maker,
            task_id=task_id,
            user_id=1,
            title="Follow up",
            completed_at=TEST_TIMESTAMP if task_id == 102 else None,
            archived_at=TEST_TIMESTAMP if task_id == 103 else None,
        )
        if task_id != 104:
            await seed_user_task_recording(
                test_session_maker,
                row_id=task_id,
                task_id=task_id,
                recording_id=401,
            )
    override_current_user(1)

    response = await client.get(
        "/api/v1/tasks/", params={"recording_id": "rec-meeting", "status": status}
    )

    assert response.status_code == 200
    assert {task["id"] for task in response.json()} == expected_ids


@pytest.mark.anyio
async def test_recording_tasks_preserve_multiple_links_through_completion_and_reopening(
    client: AsyncClient,
    override_current_user: Callable[[int], None],
    test_session_maker: sessionmaker,
) -> None:
    for recording_id in (401, 402):
        await seed_recording(
            test_session_maker,
            recording_id=recording_id,
            user_id=1,
            name=f"Meeting {recording_id}",
            public_id=f"rec-{recording_id}",
            created_at=TEST_TIMESTAMP,
        )
    override_current_user(1)
    created = await client.post(
        "/api/v1/tasks/",
        json={"title": "Shared follow-up", "recording_ids": ["rec-401", "rec-402"]},
    )
    assert created.status_code == 200
    task_id = created.json()["id"]

    for completed in (True, False):
        updated = await client.patch(
            f"/api/v1/tasks/{task_id}", json={"completed": completed}
        )
        assert updated.status_code == 200
        for public_id in ("rec-401", "rec-402"):
            listed = await client.get(
                "/api/v1/tasks/", params={"recording_id": public_id}
            )
            assert listed.status_code == 200
            assert [task["id"] for task in listed.json()] == [task_id]
            task = listed.json()[0]
            assert (task["completed_at"] is not None) is completed
            assert {link["id"] for link in task["linked_recordings"]} == {
                "rec-401",
                "rec-402",
            }


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
    assert (
        datetime.fromisoformat(payload["created_at"].replace("Z", "+00:00")).tzinfo
        == UTC
    )
    assert (
        datetime.fromisoformat(payload["updated_at"].replace("Z", "+00:00")).tzinfo
        == UTC
    )


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

    assert datetime.fromisoformat(str(stored_due_at)) == datetime(
        2026, 4, 13, 12, 30, 0
    )


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

    unarchive_response = await client.patch(
        "/api/v1/tasks/202", json={"archived": False}
    )

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
        (
            "put",
            "/api/v1/calendar/connections/301/calendars",
            {"selected_calendar_ids": []},
        ),
        (
            "put",
            "/api/v1/calendar/connections/301/calendars/401/colour",
            {"colour": "emerald"},
        ),
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


@pytest.mark.anyio
async def test_microsoft_webhook_echoes_validation_token(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/calendar/webhooks/microsoft",
        params={"validationToken": "validate-me-123"},
    )

    assert response.status_code == 200
    assert response.text == "validate-me-123"
    assert response.headers["content-type"].startswith("text/plain")


@pytest.mark.anyio
async def test_microsoft_webhook_rejects_oversized_validation_token(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/calendar/webhooks/microsoft",
        params={"validationToken": "x" * 600},
    )

    assert response.status_code == 400


@pytest.mark.anyio
async def test_microsoft_webhook_rejects_oversized_body(
    client: AsyncClient,
) -> None:
    oversized = b"x" * (1024 * 1024 + 100)
    response = await client.post(
        "/api/v1/calendar/webhooks/microsoft",
        content=oversized,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413


@pytest.mark.anyio
async def test_google_webhook_acknowledges_unknown_channel(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/calendar/webhooks/google",
        headers={
            "X-Goog-Channel-ID": "unknown-channel",
            "X-Goog-Channel-Token": "whatever",
            "X-Goog-Resource-State": "exists",
        },
    )

    assert response.status_code == 200


@pytest.mark.anyio
async def test_microsoft_webhook_acknowledges_notification_payload(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/calendar/webhooks/microsoft",
        json={"value": [{"subscriptionId": "unknown-sub", "clientState": "x"}]},
    )

    assert response.status_code == 202
