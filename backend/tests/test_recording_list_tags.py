"""The recordings list carries each meeting's tags.

The meeting list draws a tag's colour on each card, so the list returns every
tag a meeting carries, as the single-recording read does. The tags for a whole
page come from one query, however many meetings the page holds, rather than
one query per meeting.

Alice (user 1) is signed in. Bob (user 2) owns a meeting of his own.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.models.recording import Recording, RecordingStatus
from backend.models.tag import RecordingTag, Tag
from backend.services.recording_tag_reads import load_tags_by_recording
from backend.tests.sqlite_schemas import (
    RECORDING_SPEAKERS_SCHEMA,
    RECORDING_TAGS_SCHEMA,
    RECORDINGS_SCHEMA,
    TAGS_SCHEMA,
    TRANSCRIPTS_SCHEMA,
)
from backend.utils.db_batching import MAX_BIND_PARAMS

ALICE = 1
BOB = 2
START = datetime(2026, 10, 1, 9, 0)


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    built = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with built.begin() as connection:
        for schema in (
            RECORDINGS_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            RECORDING_TAGS_SCHEMA,
            TAGS_SCHEMA,
        ):
            await connection.execute(text(schema))
    try:
        yield built
    finally:
        await built.dispose()


@pytest.fixture
def session_maker(engine: AsyncEngine) -> sessionmaker:
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def client(session_maker: sessionmaker) -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=ALICE)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as async_client:
        yield async_client


def _statements(engine: AsyncEngine) -> list[str]:
    """Every statement the engine runs from here on."""
    seen: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, *_):
        seen.append(statement)

    return seen


async def _seed(
    session_maker: sessionmaker,
    meetings: int,
    *,
    tags_each: int = 0,
    owner: int = ALICE,
    first_id: int = 1,
) -> list[str]:
    """``meetings`` recordings, newest first, each with ``tags_each`` tags.

    Returns the meetings' public ids in the order the list shows them.
    """
    async with session_maker() as session:
        for offset in range(meetings):
            recording_id = first_id + offset
            session.add(
                Recording(
                    id=recording_id,
                    public_id=f"rec-{recording_id}",
                    meeting_uid=f"meeting-{recording_id}",
                    name=f"Meeting {recording_id}",
                    audio_path=f"/nonexistent/{recording_id}.webm",
                    status=RecordingStatus.PROCESSED,
                    user_id=owner,
                    created_at=START + timedelta(hours=recording_id),
                )
            )
            for index in range(tags_each):
                tag = Tag(
                    name=f"Topic {recording_id}-{index}",
                    color="blue",
                    user_id=owner,
                )
                session.add(tag)
                await session.flush()
                session.add(RecordingTag(recording_id=recording_id, tag_id=tag.id))
        await session.commit()
    return [f"rec-{first_id + offset}" for offset in reversed(range(meetings))]


async def _tag(
    session_maker: sessionmaker,
    recording_id: int,
    *tags: tuple[str, str | None, int | None],
) -> list[int]:
    """Applies new tags to a recording in order; returns their ids."""
    ids: list[int] = []
    async with session_maker() as session:
        for name, color, parent_id in tags:
            tag = Tag(name=name, color=color, user_id=ALICE, parent_id=parent_id)
            session.add(tag)
            await session.flush()
            assert tag.id is not None
            ids.append(tag.id)
            session.add(RecordingTag(recording_id=recording_id, tag_id=tag.id))
        await session.commit()
    return ids


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/api/v1/recordings", "/api/v1/recordings/"])
async def test_list_returns_each_meetings_tags_in_the_order_applied(
    session_maker: sessionmaker, client: AsyncClient, path: str
) -> None:
    await _seed(session_maker, 2)
    clients_id, _ = await _tag(
        session_maker, 2, ("Clients", "teal", None), ("Q4", None, None)
    )
    await _tag(session_maker, 2, ("Renewals", "rose", clients_id))

    response = await client.get(path)

    assert response.status_code == 200
    listed = {item["id"]: item["tags"] for item in response.json()}
    assert [
        (tag["name"], tag["color"], tag["parent_id"], tag["user_id"])
        for tag in listed["rec-2"]
    ] == [
        ("Clients", "teal", None, ALICE),
        ("Q4", None, None, ALICE),
        ("Renewals", "rose", clients_id, ALICE),
    ]
    assert all(isinstance(tag["id"], int) for tag in listed["rec-2"])
    # An untagged meeting lists an empty list, not a missing key.
    assert listed["rec-1"] == []


@pytest.mark.anyio
async def test_a_tag_filter_still_lists_the_meetings_other_tags(
    session_maker: sessionmaker, client: AsyncClient
) -> None:
    await _seed(session_maker, 2)
    clients_id, _ = await _tag(
        session_maker, 1, ("Clients", "teal", None), ("Hiring", "amber", None)
    )
    await _tag(session_maker, 2, ("Board", "red", None))

    response = await client.get("/api/v1/recordings/", params={"tag_ids": clients_id})

    assert response.status_code == 200
    assert [
        (item["id"], [tag["name"] for tag in item["tags"]]) for item in response.json()
    ] == [("rec-1", ["Clients", "Hiring"])]


@pytest.mark.anyio
async def test_list_tags_stay_with_their_own_meeting_and_owner(
    session_maker: sessionmaker, client: AsyncClient
) -> None:
    await _seed(session_maker, 2, tags_each=2)
    await _seed(session_maker, 1, tags_each=3, owner=BOB, first_id=10)

    response = await client.get("/api/v1/recordings/")

    assert response.status_code == 200
    assert [
        (item["id"], [tag["name"] for tag in item["tags"]]) for item in response.json()
    ] == [
        ("rec-2", ["Topic 2-0", "Topic 2-1"]),
        ("rec-1", ["Topic 1-0", "Topic 1-1"]),
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("meetings", [1, 3, 40])
async def test_a_page_reads_its_tags_in_one_query_whatever_its_size(
    engine: AsyncEngine,
    session_maker: sessionmaker,
    client: AsyncClient,
    meetings: int,
) -> None:
    order = await _seed(session_maker, meetings, tags_each=4)
    statements = _statements(engine)

    response = await client.get("/api/v1/recordings/", params={"limit": 100})

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == order
    assert all(len(item["tags"]) == 4 for item in response.json())
    # The page itself, then every tag on it. Never one per meeting.
    reads = [
        statement for statement in statements if statement.lstrip().startswith("SELECT")
    ]
    assert len(reads) == 2
    assert sum("recording_tags" in statement for statement in reads) == 1


@pytest.mark.anyio
async def test_an_empty_page_reads_no_tags(
    engine: AsyncEngine, client: AsyncClient
) -> None:
    statements = _statements(engine)

    response = await client.get("/api/v1/recordings/")

    assert response.status_code == 200
    assert response.json() == []
    assert not any("recording_tags" in statement for statement in statements)


@pytest.mark.anyio
async def test_the_tag_read_slices_a_page_past_the_bind_limit(
    engine: AsyncEngine, session_maker: sessionmaker
) -> None:
    # One id is one bind parameter, so a page over the limit is read in two
    # statements rather than one that Postgres would refuse.
    await _seed(session_maker, 1, tags_each=1)
    statements = _statements(engine)

    async with session_maker() as session:
        tags = await load_tags_by_recording(
            session, [1, *range(1_000_000, 1_000_000 + MAX_BIND_PARAMS)]
        )

    assert [tag.name for tag in tags[1]] == ["Topic 1-0"]
    assert list(tags) == [1]
    assert sum("recording_tags" in statement for statement in statements) == 2
