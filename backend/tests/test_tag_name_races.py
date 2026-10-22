"""A tag name saved by another request between the check and the write.

Every route that creates or renames a tag by name checks the name first, and
the unique index on ``(user_id, name)`` refuses the write when another request
saved the name in between. Each test makes the first name lookup miss and
commits that name from a second connection, as the other request would, then
expects the answer the check gives: the existing tag where a route reuses a
name, and 400 where it refuses one. Alice (user 1) is signed in.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.core.security import MCP_READ_SCOPE, MCP_WRITE_SCOPE
from backend.mcp_server.auth import current_mcp_scopes, current_mcp_user
from backend.mcp_server.tools_manage import tag_recording
from backend.mcp_server.tools_people import PersonImportEntry, import_people
from backend.models.people_tag import PeopleTag
from backend.services import tag_ownership
from backend.tests.sqlite_schemas import (
    GLOBAL_SPEAKERS_SCHEMA,
    P_TAGS_SCHEMA,
    PEOPLE_TAGS_SCHEMA,
    RECORDINGS_SCHEMA,
    TAGS_SCHEMA,
)

NOW = "2026-10-22 00:00:00"
RECORDING = "11111111-2222-3333-4444-555555555555"
BOARD = 1  # Alice's recording tag, and her people tag Client
CLIENT = 1
THEIRS = 500  # the tag the other request saves

RECORDING_TAGS_SCHEMA = """
CREATE TABLE recording_tags (
    id INTEGER PRIMARY KEY,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    recording_id INTEGER,
    tag_id INTEGER
)
"""


@pytest.fixture
async def session_maker(tmp_path):
    # A file, so the other request's connection is a second one.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tags.db'}")
    async with engine.begin() as connection:
        for schema in (
            GLOBAL_SPEAKERS_SCHEMA,
            P_TAGS_SCHEMA,
            PEOPLE_TAGS_SCHEMA,
            RECORDINGS_SCHEMA,
            RECORDING_TAGS_SCHEMA,
            TAGS_SCHEMA,
        ):
            await connection.execute(text(schema))
        for table, name in (("tags", "Board"), ("p_tags", "Client")):
            await connection.execute(
                text(
                    f"INSERT INTO {table} (id, created_at, updated_at, name, user_id)"
                    " VALUES (1, :now, :now, :name, 1)"
                ),
                {"now": NOW, "name": name},
            )
        await connection.execute(
            text(
                "INSERT INTO recordings (id, created_at, updated_at, name, public_id,"
                " meeting_uid, audio_path, status, upload_progress,"
                " processing_progress, is_archived, is_deleted, user_id)"
                " VALUES (1, :now, :now, 'Weekly sync', :public_id, 'uid-1',"
                " '/tmp/a.wav', 'PROCESSED', 100, 100, 0, 0, 1)"
            ),
            {"now": NOW, "public_id": RECORDING},
        )
    try:
        yield sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def client(session_maker):
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1, username="alice", settings={}, force_password_change=False
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as async_client:
        yield async_client


@pytest.fixture
def mcp_user(monkeypatch, session_maker):
    """Alice with both MCP scopes, on the test database."""
    import backend.core.db as core_db

    monkeypatch.setattr(core_db, "async_session_maker", session_maker)
    return SimpleNamespace(id=1, username="alice")


def _bind(user) -> None:
    # Inside the test coroutine: anyio copies the context when it starts.
    current_mcp_user.set(user)
    current_mcp_scopes.set(frozenset({MCP_READ_SCOPE, MCP_WRITE_SCOPE}))


def _race(monkeypatch, session_maker, table: str, name: str) -> None:
    """Make the first name lookup miss while another request saves ``name``."""
    real_find = tag_ownership.find_tag_by_name
    calls = 0

    async def find_after_the_other_request(db, model, wanted, user_id, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            return await real_find(db, model, wanted, user_id, **kwargs)
        async with session_maker() as other:
            await other.execute(
                text(
                    f"INSERT INTO {table} (id, created_at, updated_at, name, color,"
                    " user_id) VALUES (:id, :now, :now, :name, 'teal', 1)"
                ),
                {"id": THEIRS, "now": NOW, "name": name},
            )
            await other.commit()
        return None

    monkeypatch.setattr(tag_ownership, "find_tag_by_name", find_after_the_other_request)


async def _rows(session_maker, statement: str) -> list[tuple]:
    async with session_maker() as session:
        return [tuple(row) for row in (await session.execute(text(statement))).all()]


async def _names(session_maker, table: str) -> list[tuple]:
    return await _rows(session_maker, f"SELECT id, name FROM {table} ORDER BY id")


async def _links(session_maker) -> list[tuple]:
    return await _rows(
        session_maker, "SELECT recording_id, tag_id FROM recording_tags ORDER BY id"
    )


@pytest.mark.anyio
async def test_creating_a_tag_returns_the_one_saved_first(
    client, session_maker, monkeypatch
) -> None:
    _race(monkeypatch, session_maker, "tags", "Agenda")

    response = await client.post("/api/v1/tags/", json={"name": "Agenda"})

    assert response.status_code == 200
    assert (response.json()["id"], response.json()["name"]) == (THEIRS, "Agenda")
    assert await _names(session_maker, "tags") == [(BOARD, "Board"), (THEIRS, "Agenda")]


@pytest.mark.anyio
async def test_tagging_a_meeting_uses_the_tag_saved_first(
    client, session_maker, monkeypatch
) -> None:
    _race(monkeypatch, session_maker, "tags", "Agenda")

    response = await client.post(
        f"/api/v1/tags/recordings/{RECORDING}", json={"name": "Agenda"}
    )

    assert (response.status_code, response.json()["id"]) == (200, THEIRS)
    assert await _links(session_maker) == [(1, THEIRS)]
    assert len(await _names(session_maker, "tags")) == 2


@pytest.mark.anyio
async def test_batch_tagging_uses_the_tag_saved_first(
    client, session_maker, monkeypatch
) -> None:
    _race(monkeypatch, session_maker, "tags", "Agenda")

    response = await client.post(
        "/api/v1/tags/batch/add",
        json={"recording_ids": [RECORDING], "tag_name": "Agenda"},
    )

    assert response.status_code == 200
    assert (response.json()["count"], response.json()["tag"]["id"]) == (1, THEIRS)
    assert await _links(session_maker) == [(1, THEIRS)]


@pytest.mark.anyio
async def test_the_mcp_tag_recording_tool_uses_the_tag_saved_first(
    session_maker, mcp_user, monkeypatch
) -> None:
    _bind(mcp_user)
    _race(monkeypatch, session_maker, "tags", "Agenda")

    result = await tag_recording(RECORDING, "Agenda")

    assert result["tag"] == {"id": THEIRS, "name": "Agenda"}
    assert await _links(session_maker) == [(1, THEIRS)]


@pytest.mark.anyio
async def test_creating_a_people_tag_answers_400(
    client, session_maker, monkeypatch
) -> None:
    _race(monkeypatch, session_maker, "p_tags", "Vendor")

    response = await client.post("/api/v1/people-tags/", json={"name": "Vendor"})

    assert (response.status_code, response.json()) == (
        400,
        {"detail": "Tag already exists"},
    )
    assert await _names(session_maker, "p_tags") == [
        (CLIENT, "Client"),
        (THEIRS, "Vendor"),
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("table", "route", "tag_id", "name"),
    [
        ("tags", "/api/v1/tags", BOARD, "Board"),
        ("p_tags", "/api/v1/people-tags", CLIENT, "Client"),
    ],
    ids=["recording-tags", "people-tags"],
)
async def test_a_rename_answers_400_and_keeps_the_old_name(  # noqa: PLR0913 - three fixtures, four parameters
    client, session_maker, monkeypatch, table, route, tag_id, name
) -> None:
    _race(monkeypatch, session_maker, table, "Agenda")

    response = await client.patch(f"{route}/{tag_id}", json={"name": "Agenda"})

    assert (response.status_code, response.json()) == (
        400,
        {"detail": "Tag with this name already exists"},
    )
    assert await _names(session_maker, table) == [(tag_id, name), (THEIRS, "Agenda")]


@pytest.mark.anyio
async def test_a_people_import_links_the_tag_saved_first(
    session_maker, mcp_user, monkeypatch
) -> None:
    _bind(mcp_user)
    _race(monkeypatch, session_maker, "p_tags", "Customer")

    result = await import_people(
        [PersonImportEntry(name="Alice Example", tags=["Customer", "Client"])]
    )

    assert result["summary"] == {"created": 1, "updated": 0, "skipped": 0, "error": 0}
    assert await _rows(
        session_maker, "SELECT tag_id FROM people_tags ORDER BY tag_id"
    ) == [(CLIENT,), (THEIRS,)]
    assert len(await _names(session_maker, "p_tags")) == 2


@pytest.mark.anyio
async def test_a_commit_the_index_did_not_refuse_is_raised(monkeypatch) -> None:
    class RefusedSession:
        rolled_back = False

        async def commit(self) -> None:
            raise IntegrityError("UPDATE p_tags", {}, Exception("a foreign key"))

        async def rollback(self) -> None:
            self.rolled_back = True

    async def nobody_holds_it(*args, **kwargs):
        return None

    monkeypatch.setattr(tag_ownership, "find_tag_by_name", nobody_holds_it)
    session = RefusedSession()
    tag = PeopleTag(id=CLIENT, name="Client", user_id=1)

    with pytest.raises(IntegrityError):
        await tag_ownership.commit_tag_name(session, tag)
    assert session.rolled_back

    async def someone_holds_it(*args, **kwargs):
        return PeopleTag(id=THEIRS, name="Client", user_id=1)

    monkeypatch.setattr(tag_ownership, "find_tag_by_name", someone_holds_it)
    with pytest.raises(HTTPException) as refused:
        await tag_ownership.commit_tag_name(session, tag)
    assert (refused.value.status_code, refused.value.detail) == (
        400,
        "Tag with this name already exists",
    )
