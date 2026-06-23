import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_user, get_db
from backend.api.v1.endpoints import system
from backend.main import create_app
from backend.models.task import (
    register_task_ownership,
)
from backend.models.user import User, UserRole

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        username VARCHAR(255) NOT NULL,
        hashed_password VARCHAR(255) NOT NULL DEFAULT '',
        is_active BOOLEAN NOT NULL DEFAULT 1,
        is_superuser BOOLEAN NOT NULL DEFAULT 0,
        force_password_change BOOLEAN NOT NULL DEFAULT 0,
        role VARCHAR(32) NOT NULL DEFAULT 'user',
        token_version INTEGER NOT NULL DEFAULT 0,
        settings JSON,
        has_seen_demo_recording BOOLEAN NOT NULL DEFAULT 0,
        invitation_id INTEGER
    )
    """,
    """
    CREATE TABLE async_task_ownerships (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        task_id VARCHAR(255) UNIQUE NOT NULL,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE
    )
    """,
]


@pytest.fixture
async def test_session_maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    async with engine.begin() as conn:
        for stmt in SCHEMA_STATEMENTS:
            await conn.execute(text(stmt))

    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.anyio
async def test_task_ownership_lifecycle_and_security(test_session_maker) -> None:
    # 1. Setup app
    app = create_app()

    # Mock task result
    class _MockTaskResult:
        status = "PROCESSING"
        info = {"progress": 42, "message": "Working..."}
        result = info

    original_async_result = system.AsyncResult
    system.AsyncResult = lambda task_id: _MockTaskResult()

    # Mock users
    user_a = User(
        id=101,
        username="user_a",
        hashed_password="pw",
        role=UserRole.USER,
        is_superuser=False,
    )
    user_b = User(
        id=102,
        username="user_b",
        hashed_password="pw",
        role=UserRole.USER,
        is_superuser=False,
    )
    admin_user = User(
        id=103,
        username="admin_user",
        hashed_password="pw",
        role=UserRole.ADMIN,
        is_superuser=False,
    )

    # We will override get_db to use our in-memory SQLite database
    async def override_get_db():
        async with test_session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    try:
        # Create users in the test db
        async with test_session_maker() as session:
            session.add(user_a)
            session.add(user_b)
            session.add(admin_user)
            await session.commit()

            # Register task ownership for user_a
            await register_task_ownership(session, "task-101", user_a.id)

        # Test Case A: User A queries their own task status -> SUCCESS (200)
        app.dependency_overrides[get_current_user] = lambda: user_a
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://test"
        ) as client:
            response = await client.get("/api/v1/system/tasks/task-101")
            assert response.status_code == 200
            assert response.json()["task_id"] == "task-101"
            assert response.json()["status"] == "PROCESSING"
            assert response.json()["progress"] == 42

        # Test Case B: User B queries User A's task status -> FAILURE (404)
        app.dependency_overrides[get_current_user] = lambda: user_b
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://test"
        ) as client:
            response = await client.get("/api/v1/system/tasks/task-101")
            assert response.status_code == 404
            assert response.json()["detail"] == "Task not found"

        # Test Case C: Querying an unregistered/invalid task -> FAILURE (404)
        app.dependency_overrides[get_current_user] = lambda: user_a
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://test"
        ) as client:
            response = await client.get("/api/v1/system/tasks/invalid-task")
            assert response.status_code == 404

        # Test Case D: Admin user queries User A's task status -> SUCCESS (200)
        app.dependency_overrides[get_current_user] = lambda: admin_user
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://test"
        ) as client:
            response = await client.get("/api/v1/system/tasks/task-101")
            assert response.status_code == 200

    finally:
        system.AsyncResult = original_async_result
        app.dependency_overrides.clear()
