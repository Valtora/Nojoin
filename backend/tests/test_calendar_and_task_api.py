from __future__ import annotations

import json
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
from backend.core.encryption import encrypt_secret
from backend.models.user import User

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
    CREATE TABLE user_tasks (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        title VARCHAR(255) NOT NULL,
        due_at DATETIME,
        completed_at DATETIME,
        user_id INTEGER NOT NULL
    )
    """,
]


def build_test_user(user_id: int, username: str) -> User:
    return User(
        id=user_id,
        username=username,
        hashed_password="hashed-password",
        role="user",
        is_active=True,
        is_superuser=False,
        force_password_change=False,
    )


async def execute_sql(
    session_maker: sessionmaker,
    statement: str,
    params: dict[str, object],
) -> None:
    async with session_maker() as session:
        await session.execute(text(statement), params)
        await session.commit()


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
            "provider": "google",
            "provider_account_id": f"acct-{connection_id}",
            "email": "calendar-owner@example.com",
            "display_name": "Calendar Owner",
            "access_token_encrypted": None,
            "refresh_token_encrypted": None,
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
) -> None:
    await execute_sql(
        session_maker,
        """
        INSERT INTO user_tasks (
            id,
            created_at,
            updated_at,
            title,
            due_at,
            completed_at,
            user_id
        ) VALUES (
            :id,
            :created_at,
            :updated_at,
            :title,
            :due_at,
            :completed_at,
            :user_id
        )
        """,
        {
            "id": task_id,
            "created_at": TEST_TIMESTAMP,
            "updated_at": TEST_TIMESTAMP,
            "title": title,
            "due_at": None,
            "completed_at": None,
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


@pytest.fixture
async def api_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    for env_key in (
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
    def _override(user_id: int, username: str = "alice") -> None:
        api_app.dependency_overrides[get_current_user] = lambda: build_test_user(
            user_id,
            username,
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
