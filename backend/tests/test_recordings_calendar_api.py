from __future__ import annotations

from datetime import UTC, datetime

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

TEST_TIMESTAMP = datetime(2026, 4, 12, 10, 0, 0)

RECORDINGS_SCHEMA = """
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
    is_archived BOOLEAN NOT NULL,
    is_deleted BOOLEAN NOT NULL,
    user_id INTEGER
)
"""


def build_test_user(
    user_id: int,
    username: str = "alice",
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


async def seed_recording(
    session_maker: sessionmaker,
    *,
    recording_id: int,
    user_id: int,
    created_at: datetime,
    is_archived: bool = False,
    is_deleted: bool = False,
) -> None:
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    is_archived, is_deleted, user_id
                ) VALUES (
                    :id, :created_at, :updated_at, :name, :public_id, :meeting_uid,
                    :audio_path, :status, 0, 0, :is_archived, :is_deleted, :user_id
                )
                """
            ),
            {
                "id": recording_id,
                "created_at": created_at,
                "updated_at": created_at,
                "name": f"Recording {recording_id}",
                "public_id": f"public-{recording_id}",
                "meeting_uid": f"meeting-{recording_id}",
                "audio_path": f"/audio/{recording_id}.wav",
                "status": "recorded",
                "is_archived": is_archived,
                "is_deleted": is_deleted,
                "user_id": user_id,
            },
        )
        await session.commit()


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
        await connection.execute(text(RECORDINGS_SCHEMA))

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


@pytest.mark.anyio
async def test_calendar_buckets_recordings_per_local_day(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    await seed_recording(
        test_session_maker,
        recording_id=1,
        user_id=1,
        created_at=datetime(2026, 4, 10, 9, 0, 0),
    )
    await seed_recording(
        test_session_maker,
        recording_id=2,
        user_id=1,
        created_at=datetime(2026, 4, 10, 15, 0, 0),
    )
    await seed_recording(
        test_session_maker,
        recording_id=3,
        user_id=1,
        created_at=datetime(2026, 4, 18, 11, 0, 0),
    )
    override_current_user(1)

    response = await client.get(
        "/api/v1/recordings/calendar",
        params={"month": "2026-04", "timezone": "UTC"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["month"] == "2026-04"
    assert payload["timezone"] == "UTC"
    assert payload["day_counts"] == [
        {"date": "2026-04-10", "count": 2},
        {"date": "2026-04-18", "count": 1},
    ]


@pytest.mark.anyio
async def test_calendar_excludes_other_users_recordings(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    await seed_recording(
        test_session_maker,
        recording_id=1,
        user_id=1,
        created_at=datetime(2026, 4, 5, 12, 0, 0),
    )
    await seed_recording(
        test_session_maker,
        recording_id=2,
        user_id=2,
        created_at=datetime(2026, 4, 5, 12, 0, 0),
    )
    override_current_user(1)

    response = await client.get(
        "/api/v1/recordings/calendar",
        params={"month": "2026-04", "timezone": "UTC"},
    )

    assert response.status_code == 200
    assert response.json()["day_counts"] == [{"date": "2026-04-05", "count": 1}]


@pytest.mark.anyio
async def test_calendar_excludes_deleted_and_archived_recordings(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    await seed_recording(
        test_session_maker,
        recording_id=1,
        user_id=1,
        created_at=datetime(2026, 4, 7, 12, 0, 0),
    )
    await seed_recording(
        test_session_maker,
        recording_id=2,
        user_id=1,
        created_at=datetime(2026, 4, 8, 12, 0, 0),
        is_deleted=True,
    )
    await seed_recording(
        test_session_maker,
        recording_id=3,
        user_id=1,
        created_at=datetime(2026, 4, 9, 12, 0, 0),
        is_archived=True,
    )
    override_current_user(1)

    response = await client.get(
        "/api/v1/recordings/calendar",
        params={"month": "2026-04", "timezone": "UTC"},
    )

    assert response.status_code == 200
    assert response.json()["day_counts"] == [{"date": "2026-04-07", "count": 1}]


@pytest.mark.anyio
async def test_calendar_buckets_by_local_day_in_non_utc_timezone(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    # 23:30 on 2026-05-31 UTC is 00:30 on 2026-06-01 in Europe/Madrid (UTC+2 in
    # summer). With the Madrid timezone the recording must land on 1 June, and
    # it must NOT appear in the May calendar at all.
    await seed_recording(
        test_session_maker,
        recording_id=1,
        user_id=1,
        created_at=datetime(2026, 5, 31, 23, 30, 0),
    )
    override_current_user(1, settings={"timezone": "Europe/Madrid"})

    may_response = await client.get(
        "/api/v1/recordings/calendar",
        params={"month": "2026-05"},
    )
    assert may_response.status_code == 200
    assert may_response.json()["timezone"] == "Europe/Madrid"
    assert may_response.json()["day_counts"] == []

    june_response = await client.get(
        "/api/v1/recordings/calendar",
        params={"month": "2026-06"},
    )
    assert june_response.status_code == 200
    assert june_response.json()["day_counts"] == [{"date": "2026-06-01", "count": 1}]


@pytest.mark.anyio
async def test_calendar_uses_user_setting_when_timezone_param_absent(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    # 02:30 on 2026-04-15 UTC is 22:30 on 2026-04-14 in America/New_York.
    await seed_recording(
        test_session_maker,
        recording_id=1,
        user_id=1,
        created_at=datetime(2026, 4, 15, 2, 30, 0),
    )
    override_current_user(1, settings={"timezone": "America/New_York"})

    response = await client.get(
        "/api/v1/recordings/calendar",
        params={"month": "2026-04"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["timezone"] == "America/New_York"
    assert payload["day_counts"] == [{"date": "2026-04-14", "count": 1}]


@pytest.mark.anyio
async def test_calendar_rejects_malformed_month(
    client: AsyncClient,
    override_current_user,
) -> None:
    override_current_user(1)

    response = await client.get(
        "/api/v1/recordings/calendar",
        params={"month": "not-a-month"},
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_calendar_route_not_captured_as_recording_id(
    client: AsyncClient,
    override_current_user,
    test_session_maker: sessionmaker,
) -> None:
    override_current_user(1)

    response = await client.get(
        "/api/v1/recordings/calendar",
        params={"month": "2026-04", "timezone": "UTC"},
    )

    # The 'calendar' segment must hit the calendar endpoint, not the
    # /{recording_id} catch-all (which would 404 on an unknown id).
    assert response.status_code == 200
    assert "day_counts" in response.json()
    assert datetime.now(UTC).tzinfo == UTC
