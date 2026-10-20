"""The timeline notes routes on a real PostgreSQL, against the migrated schema.

SQLite in the main API tests enforces neither the foreign keys nor the unique
index the way Postgres does, so what a create does when its insert is refused
is shown here. Skips unless NOJOIN_TEST_POSTGRES_URL names a server; CI sets it
for the backend suite.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import backend.api.v1.endpoints.transcripts.routes_timeline_notes as notes_routes
from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.tests.test_timeline_notes_api import OWNER_ID, _user
from backend.tests.test_timeline_notes_migration_postgres import _alembic, _psycopg_url

REC = "rec-pg"
OTHER_REC = "rec-pg-other"
BASE = f"/api/v1/transcripts/{REC}/timeline-notes"


def _seed(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        conn.execute(
            "INSERT INTO users (id, created_at, updated_at, username, hashed_password,"
            " is_active, is_superuser, role, force_password_change)"
            " VALUES (%s, now(), now(), 'mira', 'x', true, false, 'user', false)",
            (OWNER_ID,),
        )
        for row_id, public_id in ((10, REC), (11, OTHER_REC)):
            conn.execute(
                "INSERT INTO recordings (id, created_at, updated_at, name, public_id,"
                " meeting_uid, audio_path, status, upload_progress,"
                " processing_progress, is_archived, is_deleted, user_id) VALUES"
                " (%s, now(), now(), 'Meeting', %s, %s, %s, 'PROCESSED', 100, 100,"
                " false, false, %s)",
                (row_id, public_id, public_id, f"/data/{public_id}.wav", OWNER_ID),
            )


@pytest.fixture
def database(postgres_test_url: str) -> Iterator[str]:
    """A database of its own, migrated to head and seeded."""
    admin_url = _psycopg_url(postgres_test_url)
    name = f"nojoin_timeline_notes_api_{uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    try:
        _alembic(url, "upgrade", "head")
        _seed(url)
        yield url
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
async def client(
    database: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:

    async def _no_refresh(recording_id: int, *, enabled: bool = True) -> None:
        return None

    monkeypatch.setattr(notes_routes, "_dispatch_meeting_edge_refresh", _no_refresh)
    engine = create_async_engine(
        make_url(database).set(drivername="postgresql+asyncpg")
    )
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    application = FastAPI()
    application.include_router(api_router, prefix="/api/v1")

    async def _db() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            yield session

    application.dependency_overrides[get_db] = _db
    application.dependency_overrides[get_current_user] = lambda: _user(OWNER_ID, "mira")
    try:
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as async_client:
            yield async_client
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_a_retried_create_after_a_delete_makes_the_note_anew(
    client: AsyncClient,
) -> None:
    # Deleted ids are not remembered, so the server cannot tell this retry from
    # a first create. The notes box guards against it (headNoteSync.ts).
    note_id = str(uuid4())
    first = await client.post(BASE, json={"id": note_id, "kind": "head", "body": "A"})
    assert first.status_code == 201
    assert (await client.delete(f"{BASE}/{note_id}")).status_code == 204

    retry = await client.post(BASE, json={"id": note_id, "kind": "head", "body": "A"})

    assert retry.status_code == 201
    assert retry.json()["revision"] == 1
    assert [note["id"] for note in (await client.get(BASE)).json()] == [note_id]


@pytest.mark.anyio
async def test_an_id_held_on_another_recording_is_refused_as_in_use(
    client: AsyncClient,
) -> None:
    note_id = str(uuid4())
    elsewhere = await client.post(
        f"/api/v1/transcripts/{OTHER_REC}/timeline-notes",
        json={"id": note_id, "kind": "head", "body": "Elsewhere"},
    )
    assert elsewhere.status_code == 201

    clash = await client.post(
        BASE, json={"id": note_id, "kind": "head", "body": "Here"}
    )

    assert clash.status_code == 409
    assert clash.json()["detail"] == notes_routes.NOTE_ID_TAKEN_DETAIL
    assert (await client.get(BASE)).json() == []


@pytest.mark.anyio
async def test_a_recording_deleted_during_a_create_is_not_reported_as_an_id_in_use(
    client: AsyncClient, database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owned = notes_routes._get_owned_recording
    calls = 0

    async def _then_deleted(*args, **kwargs):
        # The recording is found, then deleted elsewhere before the insert.
        nonlocal calls
        calls += 1
        recording = await owned(*args, **kwargs)
        if calls == 1:
            with psycopg.connect(database, autocommit=True) as conn:
                conn.execute("DELETE FROM recordings WHERE public_id = %s", (REC,))
        return recording

    monkeypatch.setattr(notes_routes, "_get_owned_recording", _then_deleted)

    response = await client.post(
        BASE, json={"id": str(uuid4()), "kind": "head", "body": "Too late"}
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Recording not found"
