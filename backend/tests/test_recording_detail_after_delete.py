"""What ``GET /recordings/{id}`` answers once a recording is binned or deleted.

The browser keeps unsent notes per recording, and removes them for a recording
it finds deleted for good on another device (issue #636,
``frontend/src/lib/notes/deletedRecordingNotes.ts``). It goes by this read: a
404 ``Recording not found`` removes them, and anything else keeps them. So a
recording in the bin, which can be restored, must still answer its owner, and
the 404 must be the same for a recording deleted for good as for one of another
account's, which is why the browser confirms the account first.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.models.recording import Recording, RecordingStatus
from backend.tests.sqlite_schemas import TIMELINE_NOTES_SCHEMA
from backend.tests.test_recording_pause_resume import (
    RECORDING_SPEAKERS_SCHEMA,
    RECORDING_TAGS_SCHEMA,
    RECORDINGS_SCHEMA,
    TAGS_SCHEMA,
    TRANSCRIPTS_SCHEMA,
)

OWNER = 1
OTHER = 2
NOT_FOUND = {"detail": "Recording not found"}


@pytest.fixture
async def session_maker() -> AsyncIterator[sessionmaker]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        for schema in (
            RECORDINGS_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            RECORDING_TAGS_SCHEMA,
            TAGS_SCHEMA,
            TIMELINE_NOTES_SCHEMA,
        ):
            await connection.execute(text(schema))
    async with sessionmaker(engine, class_=AsyncSession)() as session:
        session.add(
            Recording(
                id=1,
                public_id="rec-1",
                meeting_uid="meeting-1",
                name="Weekly sync",
                audio_path="/nonexistent/meeting.webm",
                status=RecordingStatus.PROCESSED,
                user_id=OWNER,
            )
        )
        await session.commit()
    try:
        yield sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
def acting() -> SimpleNamespace:
    """The signed-in user, by id."""
    return SimpleNamespace(id=OWNER)


@pytest.fixture
async def client(
    session_maker: sessionmaker, acting: SimpleNamespace
) -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: acting
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as async_client:
        yield async_client


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("route", "flag"),
    [("soft-delete", "is_deleted"), ("archive", "is_archived")],
    ids=["binned", "archived"],
)
async def test_a_binned_or_archived_recording_still_answers_its_owner(
    client: AsyncClient, route: str, flag: str
) -> None:
    moved = await client.post(f"/api/v1/recordings/rec-1/{route}")
    assert moved.status_code == 200, moved.text

    read = await client.get("/api/v1/recordings/rec-1")

    assert read.status_code == 200, read.text
    assert read.json()[flag] is True


@pytest.mark.anyio
async def test_another_accounts_recording_answers_as_a_deleted_one_does(
    client: AsyncClient, session_maker: sessionmaker, acting: SimpleNamespace
) -> None:
    acting.id = OTHER
    elsewhere = await client.get("/api/v1/recordings/rec-1")

    acting.id = OWNER
    # As Delete Permanently and discard leave it: no row.
    async with session_maker() as session:
        await session.execute(text("DELETE FROM recordings WHERE public_id = 'rec-1'"))
        await session.commit()
    deleted = await client.get("/api/v1/recordings/rec-1")

    assert (elsewhere.status_code, elsewhere.json()) == (404, NOT_FOUND)
    assert (deleted.status_code, deleted.json()) == (404, NOT_FOUND)
