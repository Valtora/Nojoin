"""A meeting holds each recording tag once.

``recording_tags`` carries a unique constraint on ``(recording_id, tag_id)``
(revision fb5a849e7f5b). The routes look for a link before they add one, and
when another request adds it in between, the constraint refuses the second
insert and the route answers as if it had found the link. Untagging deletes
every matching link, and a restore adds an archived link once.

The race tests commit the other request's link from a second connection just
before this request inserts its own. Alice (user 1) is signed in.
"""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, select

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.core.security import MCP_READ_SCOPE, MCP_WRITE_SCOPE
from backend.mcp_server.auth import current_mcp_scopes, current_mcp_user
from backend.mcp_server.tools_manage import untag_recording
from backend.services import tag_ownership
from backend.tests.backup_harness import (
    TestRecording,
    TestRecordingTag,
    TestTag,
    build_test_context,
    patch_backup_manager,
)
from backend.tests.sqlite_schemas import (
    RECORDING_TAGS_SCHEMA,
    RECORDINGS_SCHEMA,
    TAGS_SCHEMA,
)
from backend.tests.test_backup_legacy_owners import _restore, _row, _write_archive

NOW = "2026-10-22 00:00:00"
FIRST = "11111111-2222-3333-4444-555555555555"
SECOND = "66666666-7777-8888-9999-000000000000"
BOARD = 1

# The table as it was before the constraint, which could hold a link twice.
UNCONSTRAINED_RECORDING_TAGS_SCHEMA = RECORDING_TAGS_SCHEMA.replace(
    ",\n    UNIQUE (recording_id, tag_id)", ""
)


@asynccontextmanager
async def _database(path: Path, recording_tags_schema: str, links: int = 0):
    """Alice's Board tag and two meetings, with ``links`` Board links on the first.

    A file, so the other request's connection is a second one.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        for schema in (RECORDINGS_SCHEMA, recording_tags_schema, TAGS_SCHEMA):
            await connection.execute(text(schema))
        await connection.execute(
            text(
                "INSERT INTO tags (id, created_at, updated_at, name, user_id)"
                " VALUES (:id, :now, :now, 'Board', 1)"
            ),
            {"id": BOARD, "now": NOW},
        )
        for recording_id, public_id in ((1, FIRST), (2, SECOND)):
            await connection.execute(
                text(
                    "INSERT INTO recordings (id, created_at, updated_at, name,"
                    " public_id, meeting_uid, audio_path, status, upload_progress,"
                    " processing_progress, is_archived, is_deleted, user_id)"
                    " VALUES (:id, :now, :now, 'Weekly sync', :public_id, :uid,"
                    " :path, 'PROCESSED', 100, 100, 0, 0, 1)"
                ),
                {
                    "id": recording_id,
                    "now": NOW,
                    "public_id": public_id,
                    "uid": f"uid-{recording_id}",
                    "path": f"/tmp/{recording_id}.wav",
                },
            )
        for _ in range(links):
            await connection.execute(
                text(
                    "INSERT INTO recording_tags (created_at, updated_at,"
                    " recording_id, tag_id) VALUES (:now, :now, 1, :tag)"
                ),
                {"now": NOW, "tag": BOARD},
            )
    try:
        yield SimpleNamespace(
            path=path,
            engine=engine,
            session_maker=sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            ),
        )
    finally:
        await engine.dispose()


@pytest.fixture
async def database(tmp_path):
    async with _database(tmp_path / "tags.db", RECORDING_TAGS_SCHEMA) as built:
        yield built


@pytest.fixture
async def unconstrained(tmp_path):
    """The table without the constraint, holding the Board link twice on meeting 1."""
    async with _database(
        tmp_path / "tags.db", UNCONSTRAINED_RECORDING_TAGS_SCHEMA, links=2
    ) as built:
        yield built


def _client(session_maker) -> AsyncClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1, username="alice", settings={}, force_password_change=False
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


def _other_request_links_first(database, recording_id: int) -> None:
    """Before this request's first link insert, another request saves that link."""
    raced = False

    @event.listens_for(database.engine.sync_engine, "before_cursor_execute", named=True)
    def link_it_first(statement: str, **_: Any) -> None:
        nonlocal raced
        if raced or not statement.upper().startswith("INSERT INTO RECORDING_TAGS"):
            return
        raced = True
        other = sqlite3.connect(database.path)
        try:
            other.execute(
                "INSERT INTO recording_tags (created_at, updated_at, recording_id,"
                " tag_id) VALUES (?, ?, ?, ?)",
                (NOW, NOW, recording_id, BOARD),
            )
            other.commit()
        finally:
            other.close()


async def _links(session_maker) -> list[tuple]:
    async with session_maker() as session:
        rows = await session.execute(
            text(
                "SELECT recording_id, tag_id FROM recording_tags ORDER BY recording_id"
            )
        )
        return [tuple(row) for row in rows.all()]


@pytest.mark.anyio
async def test_tagging_a_meeting_another_request_tagged_keeps_one_link(
    database,
) -> None:
    _other_request_links_first(database, 1)

    async with _client(database.session_maker) as client:
        response = await client.post(
            f"/api/v1/tags/recordings/{FIRST}", json={"name": "Board"}
        )

    assert response.status_code == 200
    assert (response.json()["id"], response.json()["name"]) == (BOARD, "Board")
    assert await _links(database.session_maker) == [(1, BOARD)]


@pytest.mark.anyio
async def test_batch_tagging_counts_only_the_links_it_added(database) -> None:
    _other_request_links_first(database, 1)

    async with _client(database.session_maker) as client:
        response = await client.post(
            "/api/v1/tags/batch/add",
            json={"recording_ids": [FIRST, SECOND], "tag_name": "Board"},
        )

    assert response.status_code == 200
    assert (response.json()["count"], response.json()["tag"]["id"]) == (1, BOARD)
    assert await _links(database.session_maker) == [(1, BOARD), (2, BOARD)]


@pytest.mark.anyio
async def test_batch_tagging_with_a_new_name_creates_the_tag_with_its_links(
    database,
) -> None:
    async with _client(database.session_maker) as client:
        response = await client.post(
            "/api/v1/tags/batch/add",
            json={"recording_ids": [FIRST, SECOND, FIRST], "tag_name": "Agenda"},
        )

    assert response.status_code == 200
    agenda = response.json()["tag"]
    assert (response.json()["count"], agenda["name"]) == (2, "Agenda")
    assert await _links(database.session_maker) == [
        (1, agenda["id"]),
        (2, agenda["id"]),
    ]


@pytest.mark.anyio
async def test_untagging_deletes_every_link_to_the_tag(unconstrained) -> None:
    async with _client(unconstrained.session_maker) as client:
        response = await client.delete(f"/api/v1/tags/recordings/{FIRST}/Board")

    assert (response.status_code, response.json()) == (200, {"ok": True})
    assert await _links(unconstrained.session_maker) == []


@pytest.mark.anyio
async def test_the_mcp_untag_recording_tool_deletes_every_link(
    unconstrained, monkeypatch
) -> None:
    import backend.core.db as core_db

    monkeypatch.setattr(core_db, "async_session_maker", unconstrained.session_maker)
    current_mcp_user.set(SimpleNamespace(id=1, username="alice"))
    current_mcp_scopes.set(frozenset({MCP_READ_SCOPE, MCP_WRITE_SCOPE}))

    result = await untag_recording(FIRST, "Board")

    assert result == {"recording_id": FIRST, "removed_tag": "Board"}
    assert await _links(unconstrained.session_maker) == []


class _RefusedSession:
    """A session whose link insert the database refuses."""

    @asynccontextmanager
    async def begin_nested(self):
        yield

    def add(self, row: Any) -> None:
        raise IntegrityError("INSERT INTO recording_tags", {}, Exception("refused"))


@pytest.mark.anyio
async def test_a_refused_link_nobody_else_saved_is_raised(monkeypatch) -> None:
    async def never_linked(*args):
        return None

    monkeypatch.setattr(tag_ownership, "find_recording_tag", never_linked)
    with pytest.raises(IntegrityError):
        await tag_ownership.link_recording_tag(_RefusedSession(), 1, BOARD)

    lookups = iter([None, 7])

    async def linked_by_the_other_request(*args):
        return next(lookups)

    monkeypatch.setattr(
        tag_ownership, "find_recording_tag", linked_by_the_other_request
    )
    assert await tag_ownership.link_recording_tag(_RefusedSession(), 1, BOARD) is False


# A restore, on the surrogate tables in ``backup_harness``, which carry the
# constraint. The archive holds the Board link on meeting 10 twice, as an
# install before the constraint could.
MODES = pytest.mark.parametrize(
    "modes",
    [{}, {"overwrite_existing": True}, {"clear_existing": True}],
    ids=["skip", "overwrite", "clear"],
)


@pytest.fixture
def target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_ENCRYPTION_KEY", "target-encryption-key")
    context = build_test_context(tmp_path / "target")
    patch_backup_manager(monkeypatch, context)
    return context


def _archive_tables() -> dict[str, list[dict[str, Any]]]:
    return {
        "users": [_row(id=1, username="alice", hashed_password="x", settings={})],
        "recordings": [
            _row(
                id=recording_id,
                name=f"Meeting {recording_id}",
                meeting_uid=f"tag-once-{recording_id}",
                audio_path=f"recordings/tag-once-{recording_id}.opus",
                status="PROCESSED",
                user_id=1,
            )
            for recording_id in (10, 11)
        ],
        "tags": [
            _row(id=tag_id, name=name, color="blue", user_id=1)
            for tag_id, name in ((1, "Board"), (2, "Finance"))
        ],
        "recording_tags": [
            _row(id=link_id, recording_id=recording_id, tag_id=tag_id)
            for link_id, recording_id, tag_id in (
                (1, 10, 1),
                (2, 10, 1),
                (3, 11, 1),
                (4, 11, 2),
            )
        ],
    }


@pytest.mark.anyio
@MODES
async def test_an_archive_holding_a_link_twice_restores_it_once(
    tmp_path: Path, target, modes: dict[str, bool]
) -> None:
    zip_path = _write_archive(tmp_path / "old.zip", _archive_tables())

    job = await _restore(zip_path, "tag-once", **modes)

    assert (job["status"], job["warnings"]) == ("completed", {"skipped": {}})
    with Session(target.sync_engine) as session:
        meetings = {r.id: r.meeting_uid for r in session.exec(select(TestRecording))}
        tags = {t.id: t.name for t in session.exec(select(TestTag))}
        links = sorted(
            (meetings[link.recording_id], tags[link.tag_id])
            for link in session.exec(select(TestRecordingTag))
        )
    assert links == [
        ("tag-once-10", "Board"),
        ("tag-once-11", "Board"),
        ("tag-once-11", "Finance"),
    ]
    await target.async_engine.dispose()
