"""The timeline notes routes on a real PostgreSQL, against the migrated schema.

Here the foreign keys, the unique ids and the row locks are Postgres's own, so
what a create does when its insert is refused, and what racing writes do, is
shown here. The caller is loaded through the request's session, as in
production. Skips unless NOJOIN_TEST_POSTGRES_URL names a server; CI sets it
for the backend suite.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import select

import backend.api.v1.endpoints.transcripts.routes_timeline_notes as notes_routes
from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.models.recording import Recording
from backend.tests.test_timeline_notes_api import OWNER_ID, _session_user
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
async def maker(database: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        make_url(database).set(drivername="postgresql+asyncpg")
    )
    try:
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def client(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:

    async def _no_refresh(recording_id: int, *, enabled: bool = True) -> None:
        return None

    monkeypatch.setattr(notes_routes, "_dispatch_meeting_edge_refresh", _no_refresh)
    application = FastAPI()
    application.include_router(api_router, prefix="/api/v1")

    async def _db() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            yield session

    application.dependency_overrides[get_db] = _db
    application.dependency_overrides[get_current_user] = _session_user(OWNER_ID)
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://testserver"
    ) as async_client:
        yield async_client


@pytest.mark.anyio
async def test_a_retried_create_after_a_delete_does_not_make_the_note_again(
    client: AsyncClient,
) -> None:
    note_id, token = str(uuid4()), str(uuid4())
    first = await client.post(BASE, json={"id": note_id, "kind": "head", "body": "A"})
    assert first.status_code == 201
    deleted = await client.delete(f"{BASE}/{note_id}", params={"write_token": token})
    assert deleted.status_code == 204

    retry = await client.post(
        BASE, json={"id": note_id, "kind": "head", "body": "A", "write_token": token}
    )

    assert retry.status_code == 410
    assert retry.json()["detail"]["code"] == "note_deleted_by_you"
    assert (await client.get(BASE)).json() == []


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
    assert clash.json()["detail"]["code"] == "note_id_in_use"
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

    # The insert's refusal rolls the session back, expiring the request's user,
    # which is loaded in that session: the route must not read it afterwards.
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "recording_not_found"


@pytest.mark.anyio
async def test_two_deletes_of_one_note_race_to_one_tombstone(
    client: AsyncClient, database: str
) -> None:
    note = await client.post(BASE, json={"kind": "head", "body": "Both clear it"})
    url = f"{BASE}/{note.json()['id']}"

    first, second = await asyncio.gather(
        client.delete(url, params={"write_token": str(uuid4())}),
        client.delete(url, params={"write_token": str(uuid4())}),
    )

    # The second waits on the first's row lock, then finds the tombstone.
    statuses = sorted([first.status_code, second.status_code])
    assert statuses == [204, 410], (first.text, second.text)
    with psycopg.connect(database) as conn:
        tombstones = conn.execute(
            "SELECT count(*) FROM timeline_note_tombstones"
        ).fetchone()
    assert tombstones == (1,)


@pytest.mark.anyio
async def test_deleting_a_recording_removes_its_notes_and_tombstones(
    client: AsyncClient, database: str, maker: async_sessionmaker[AsyncSession]
) -> None:
    # One recording goes through the ORM, as the delete routes do; the other
    # by a bulk statement, as discarding a capture does.
    for base in (BASE, f"/api/v1/transcripts/{OTHER_REC}/timeline-notes"):
        await client.post(base, json={"kind": "head", "body": "Kept until the end"})
        gone = (await client.post(base, json={"kind": "tail", "body": "x"})).json()
        assert (await client.delete(f"{base}/{gone['id']}")).status_code == 204
    async with maker() as session:
        recording = (
            await session.execute(select(Recording).where(Recording.public_id == REC))
        ).scalar_one()
        await session.delete(recording)
        await session.commit()
    with psycopg.connect(database) as conn:
        conn.execute("DELETE FROM recordings WHERE public_id = %s", (OTHER_REC,))
        notes = conn.execute("SELECT count(*) FROM timeline_notes").fetchone()
        tombstones = conn.execute(
            "SELECT count(*) FROM timeline_note_tombstones"
        ).fetchone()
    assert (notes, tombstones) == ((0,), (0,))
