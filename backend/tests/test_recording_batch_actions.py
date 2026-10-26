"""The batch recording routes follow the single-recording rules (issue #737).

The recordings list acts on a selection through ``/recordings/batch/*`` and on
one recording through ``/recordings/{id}/*``, and the two must agree: a
restore puts a meeting back in Recordings wherever it was binned from, archive
leaves a binned meeting alone, and permanent delete destroys only what is in
the bin, revoking its task first. Each batch route answers with what it
changed, which the client reports and undoes, never with what it was sent.

Permanent delete cascades through tables the SQLite schema lacks, so its tests
run on PostgreSQL and skip unless NOJOIN_TEST_POSTGRES_URL names a server.
"""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool
from sqlmodel import Session

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.celery_app import celery_app
from backend.core.db import driver_urls
from backend.models.recording import Recording, RecordingStatus
from backend.models.user import User, UserRole
from backend.tests.postgres_setup import fresh_schema
from backend.tests.sqlite_schemas import (
    RECORDING_TAGS_SCHEMA,
    RECORDINGS_SCHEMA,
    TAGS_SCHEMA,
)

OWNER = 1
OTHER = 2
NOW = "2026-10-26 00:00:00"

# public id: (owner, is_archived, is_deleted)
LIBRARY = {
    "inbox": (OWNER, False, False),
    "archived": (OWNER, True, False),
    "binned": (OWNER, False, True),
    "binned-from-archived": (OWNER, True, True),
    "theirs": (OTHER, False, True),
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def session_maker() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        for schema in (RECORDINGS_SCHEMA, TAGS_SCHEMA, RECORDING_TAGS_SCHEMA):
            await connection.execute(text(schema))
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        for index, (public_id, (owner, archived, deleted)) in enumerate(
            LIBRARY.items(), start=1
        ):
            session.add(
                Recording(
                    id=index,
                    public_id=public_id,
                    meeting_uid=f"meeting-{index}",
                    name=public_id,
                    audio_path=f"/nonexistent/{public_id}.wav",
                    status=RecordingStatus.PROCESSED,
                    is_archived=archived,
                    is_deleted=deleted,
                    user_id=owner,
                )
            )
        await session.commit()
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest.fixture
async def client(session_maker: async_sessionmaker) -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=OWNER)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as async_client:
        yield async_client


async def _flags(maker: async_sessionmaker, public_id: str) -> tuple[bool, bool]:
    async with maker() as session:
        row = (
            await session.execute(
                text(
                    "SELECT is_archived, is_deleted FROM recordings"
                    " WHERE public_id = :public_id"
                ),
                {"public_id": public_id},
            )
        ).one()
    return bool(row.is_archived), bool(row.is_deleted)


def _summary(response) -> tuple[int, int, list[str]]:
    assert response.status_code == 200, response.text
    body = response.json()
    return body["count"], body["skipped"], body["recording_ids"]


@pytest.mark.anyio
@pytest.mark.parametrize("route", ["single", "batch"])
async def test_a_meeting_binned_from_archived_is_restored_to_recordings(
    client: AsyncClient, session_maker: async_sessionmaker, route: str
) -> None:
    if route == "single":
        response = await client.post("/api/v1/recordings/binned-from-archived/restore")
    else:
        response = await client.post(
            "/api/v1/recordings/batch/restore",
            json={"recording_ids": ["binned-from-archived"]},
        )

    assert response.status_code == 200, response.text
    assert await _flags(session_maker, "binned-from-archived") == (False, False)


@pytest.mark.anyio
async def test_batch_archive_leaves_a_binned_meeting_alone_and_does_not_count_it(
    client: AsyncClient, session_maker: async_sessionmaker
) -> None:
    response = await client.post(
        "/api/v1/recordings/batch/archive",
        json={"recording_ids": ["inbox", "binned", "archived", "gone"]},
    )

    assert _summary(response) == (1, 3, ["inbox"])
    assert await _flags(session_maker, "inbox") == (True, False)
    assert await _flags(session_maker, "binned") == (False, True)


@pytest.mark.anyio
async def test_batch_restore_counts_only_the_meetings_it_brought_back(
    client: AsyncClient, session_maker: async_sessionmaker
) -> None:
    response = await client.post(
        "/api/v1/recordings/batch/restore",
        json={"recording_ids": ["inbox", "archived", "binned"]},
    )

    assert _summary(response) == (2, 1, ["archived", "binned"])
    for public_id in ("inbox", "archived", "binned"):
        assert await _flags(session_maker, public_id) == (False, False)


@pytest.mark.anyio
async def test_batch_bin_counts_once_per_meeting_and_only_the_users_own(
    client: AsyncClient, session_maker: async_sessionmaker
) -> None:
    response = await client.post(
        "/api/v1/recordings/batch/soft-delete",
        json={"recording_ids": ["archived", "archived", "binned", "theirs"]},
    )

    assert _summary(response) == (1, 2, ["archived"])
    # Binned from Archived, so it keeps its archive flag until it is restored.
    assert await _flags(session_maker, "archived") == (True, True)


@pytest.mark.anyio
async def test_batch_tagging_reports_the_meetings_it_tagged_and_untagged(
    client: AsyncClient, session_maker: async_sessionmaker
) -> None:
    async with session_maker() as session:
        await session.execute(
            text(
                "INSERT INTO tags (id, created_at, updated_at, name, user_id)"
                " VALUES (1, :now, :now, 'Board', :owner)"
            ),
            {"now": NOW, "owner": OWNER},
        )
        await session.execute(
            text(
                "INSERT INTO recording_tags (created_at, updated_at, recording_id, tag_id)"
                " VALUES (:now, :now, 2, 1)"
            ),
            {"now": NOW},
        )
        await session.commit()

    added = await client.post(
        "/api/v1/tags/batch/add",
        json={"recording_ids": ["inbox", "archived"], "tag_name": "Board"},
    )
    assert _summary(added) == (1, 1, ["inbox"])

    # The undo removes the tag from what the add changed, and nothing else.
    removed = await client.post(
        "/api/v1/tags/batch/remove",
        json={"recording_ids": ["inbox"], "tag_name": "Board"},
    )
    assert _summary(removed) == (1, 0, ["inbox"])
    async with session_maker() as session:
        links = (
            await session.execute(text("SELECT recording_id FROM recording_tags"))
        ).all()
    assert [link.recording_id for link in links] == [2]


# --- Permanent delete, on PostgreSQL -------------------------------------------

SCHEMA = "recording_batch_actions_test"


@pytest.fixture
def pg(postgres_test_url, tmp_path, monkeypatch, stub_celery_dispatch):
    importlib.import_module("backend.models.registry")
    async_url, sync_url = driver_urls(postgres_test_url)
    sync_engine = create_engine(
        sync_url, connect_args={"options": f"-csearch_path={SCHEMA},public"}
    )
    with sync_engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    fresh_schema(sync_engine, SCHEMA)
    async_engine = create_async_engine(
        async_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    maker = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )

    recordings_dir = tmp_path / "data" / "recordings"
    documents_dir = tmp_path / "data" / "documents"
    recordings_dir.mkdir(parents=True)
    documents_dir.mkdir(parents=True)
    monkeypatch.setenv("RECORDINGS_DIR", str(recordings_dir))
    monkeypatch.setenv("DOCUMENTS_DIR", str(documents_dir))

    revoked: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        celery_app.control,
        "revoke",
        lambda task_id, **options: revoked.append((task_id, options)),
    )

    with Session(sync_engine) as session:
        owner = User(username="owner", hashed_password="x", role=UserRole.OWNER)
        session.add(owner)
        session.commit()
        session.refresh(owner)
    user = owner

    app = FastAPI()
    app.include_router(api_router)

    async def override_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user

    def add(name: str, *, is_deleted: bool, task_id: str | None = None) -> str:
        audio = recordings_dir / f"{name}.wav"
        audio.write_bytes(b"RIFF")
        with Session(sync_engine) as session:
            recording = Recording(
                name=name,
                audio_path=str(audio),
                status=RecordingStatus.PROCESSED,
                is_deleted=is_deleted,
                celery_task_id=task_id,
                user_id=user.id,
            )
            session.add(recording)
            session.commit()
            return recording.public_id

    def exists(public_id: str) -> bool:
        with sync_engine.connect() as connection:
            return (
                connection.execute(
                    text("SELECT count(*) FROM recordings WHERE public_id = :p"),
                    {"p": public_id},
                ).scalar_one()
                == 1
            )

    async def request(method: str, url: str, **kwargs):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            return await client.request(method, url, **kwargs)

    try:
        yield SimpleNamespace(
            add=add,
            exists=exists,
            request=request,
            revoked=revoked,
            recordings_dir=recordings_dir,
        )
    finally:
        sync_engine.dispose()


@pytest.mark.anyio
async def test_permanent_delete_keeps_a_meeting_restored_since_it_was_selected(pg):
    """The data-loss path: in Deleted, A and B are selected, A is restored from
    its own menu, and the bar's Delete Permanently still sends both."""
    first = pg.add("first", is_deleted=True)
    second = pg.add("second", is_deleted=True)
    restored = await pg.request("POST", f"/recordings/{first}/restore")
    assert restored.status_code == 200, restored.text

    response = await pg.request(
        "POST", "/recordings/batch/permanent", json={"recording_ids": [first, second]}
    )

    assert _summary(response) == (1, 1, [second])
    assert pg.exists(first) and not pg.exists(second)
    assert (pg.recordings_dir / "first.wav").is_file()
    assert not (pg.recordings_dir / "second.wav").exists()


@pytest.mark.anyio
async def test_permanent_delete_revokes_the_task_of_each_meeting_it_deletes(pg):
    binned = pg.add("binned", is_deleted=True, task_id="task-binned")
    listed = pg.add("listed", is_deleted=False, task_id="task-listed")

    response = await pg.request(
        "POST", "/recordings/batch/permanent", json={"recording_ids": [binned, listed]}
    )

    assert _summary(response) == (1, 1, [binned])
    assert pg.revoked == [("task-binned", {"terminate": True})]
    assert pg.exists(listed)
