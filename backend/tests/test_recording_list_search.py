"""The recordings list's search, filters and paging order.

The meeting list pages as it scrolls and searches on the server (#900, #901),
so ``q`` has to find a meeting by its title, tags, the names its speakers show
under and its transcript, list each meeting once however many of those match,
and keep one fixed order for offset pages.

Alice (user 1) is signed in. Bob (user 2) owns meetings of his own. Every name
here is made up.
"""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool, StaticPool
from sqlmodel import Session, select

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.core.db import driver_urls
from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.models.recording import Recording, RecordingStatus
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.tag import RecordingTag, Tag
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.services.recording_search import like_pattern
from backend.tests.postgres_setup import fresh_schema
from backend.tests.sqlite_schemas import (
    GLOBAL_SPEAKERS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDING_TAGS_SCHEMA,
    RECORDINGS_SCHEMA,
    TAGS_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
    TRANSCRIPTS_SCHEMA,
)

pytestmark = pytest.mark.anyio

ALICE = 1
BOB = 2
START = datetime(2026, 10, 1, 9, 0)
SETTLING = {"eligible": True, "policy": {"enabled": True}, "phase": "final"}


async def _sqlite_engine() -> AsyncEngine:
    built = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with built.begin() as connection:
        for schema in (
            RECORDINGS_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            TRANSCRIPT_UTTERANCES_SCHEMA,
            GLOBAL_SPEAKERS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            RECORDING_TAGS_SCHEMA,
            TAGS_SCHEMA,
        ):
            await connection.execute(text(schema))
    return built


async def _postgres_engine(url: str) -> AsyncEngine:
    """The full schema on a real server: the search's SQL is Postgres's in use."""
    importlib.import_module("backend.models.registry")
    async_url, sync_url = driver_urls(url)
    schema = "recording_list_search_test"
    sync_engine = create_engine(
        sync_url, connect_args={"options": f"-csearch_path={schema},public"}
    )
    with sync_engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    fresh_schema(sync_engine, schema)
    with Session(sync_engine) as session:
        session.add_all(
            [
                User(id=ALICE, username="alice", hashed_password="x"),
                User(id=BOB, username="bob", hashed_password="x"),
            ]
        )
        session.commit()
    sync_engine.dispose()
    return create_async_engine(
        async_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f"{schema},public"}},
    )


@pytest.fixture(params=["sqlite", "postgres"])
async def engine(request: pytest.FixtureRequest) -> AsyncIterator[AsyncEngine]:
    if request.param == "postgres":
        built = await _postgres_engine(request.getfixturevalue("postgres_test_url"))
    else:
        built = await _sqlite_engine()
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


async def _meeting(
    session_maker: sessionmaker, recording_id: int, name: str, **details: Any
) -> str:
    """One meeting with what a search can match; returns its public id.

    ``details`` may give its ``owner``, ``created_at``, a snapshot
    ``transcript``, ``settling_lines`` (canonical lines that are its
    transcript), ``speakers`` (RecordingSpeaker fields, ``person`` for a
    People name) and ``tags`` by name.
    """
    owner: int = details.get("owner", ALICE)
    created_at: datetime | None = details.get("created_at")
    transcript: str | None = details.get("transcript")
    settling_lines: list[str] | None = details.get("settling_lines")
    speakers: list[dict[str, Any]] = details.get("speakers", [])
    tags: list[str] = details.get("tags", [])
    async with session_maker() as session:
        session.add(
            Recording(
                id=recording_id,
                public_id=f"rec-{recording_id}",
                meeting_uid=f"meeting-{recording_id}",
                name=name,
                audio_path=f"/nonexistent/{recording_id}.webm",
                status=RecordingStatus.PROCESSED,
                user_id=owner,
                created_at=created_at or START + timedelta(hours=recording_id),
            )
        )
        await session.flush()
        if transcript is not None or settling_lines is not None:
            session.add(
                Transcript(
                    recording_id=recording_id,
                    text=transcript or "",
                    segments=[],
                    settling_state=SETTLING if settling_lines is not None else None,
                )
            )
        for order, line in enumerate(settling_lines or []):
            session.add(
                TranscriptUtterance(
                    recording_id=recording_id,
                    text=line,
                    sort_key=f"{order:04d}",
                    start_ms=order * 1000,
                    end_ms=order * 1000 + 900,
                    state=TranscriptUtteranceState.FINALIZED,
                )
            )
        for index, fields in enumerate(speakers):
            fields = dict(fields)
            person = fields.pop("person", None)
            if person is not None:
                global_speaker = GlobalSpeaker(name=person, user_id=owner)
                session.add(global_speaker)
                await session.flush()
                fields["global_speaker_id"] = global_speaker.id
            session.add(
                RecordingSpeaker(
                    recording_id=recording_id,
                    diarization_label=f"SPEAKER_{index:02d}",
                    **fields,
                )
            )
        for tag_name in tags:
            # A user's tag names are unique, so a second meeting reuses the tag.
            tag = (
                await session.execute(
                    select(Tag).where(Tag.user_id == owner, Tag.name == tag_name)
                )
            ).scalar_one_or_none()
            if tag is None:
                tag = Tag(name=tag_name, color="blue", user_id=owner)
                session.add(tag)
                await session.flush()
            session.add(RecordingTag(recording_id=recording_id, tag_id=tag.id))
        await session.commit()
    return f"rec-{recording_id}"


async def _search(client: AsyncClient, q: str, **params: Any) -> list[str]:
    response = await client.get("/api/v1/recordings/", params={"q": q, **params})
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()]


async def test_search_finds_a_meeting_by_title_tag_transcript_and_live_lines(
    session_maker: sessionmaker, client: AsyncClient
) -> None:
    titled = await _meeting(session_maker, 1, "Lighthouse budget review")
    tagged = await _meeting(session_maker, 2, "Weekly sync", tags=["Lighthouse"])
    spoken = await _meeting(
        session_maker, 3, "Planning", transcript="We moved the lighthouse launch."
    )
    settled = await _meeting(
        session_maker,
        4,
        "Retro",
        settling_lines=["Nothing yet.", "The LIGHTHOUSE plan."],
    )
    await _meeting(session_maker, 5, "Unrelated", transcript="Nothing to see.")

    assert await _search(client, "lighthouse") == [settled, spoken, tagged, titled]


async def test_search_finds_the_name_each_speaker_shows_under(
    session_maker: sessionmaker, client: AsyncClient
) -> None:
    by_person = await _meeting(
        session_maker, 1, "Call one", speakers=[{"person": "Alice Example"}]
    )
    by_local = await _meeting(
        session_maker, 2, "Call two", speakers=[{"local_name": "Bob Sample (guest)"}]
    )
    by_legacy = await _meeting(
        session_maker, 3, "Call three", speakers=[{"name": "Carol Placeholder"}]
    )
    # A name given in this meeting wins over the person it was linked to.
    await _meeting(
        session_maker,
        4,
        "Call four",
        speakers=[{"person": "Alice Example", "local_name": "Dev Fictional"}],
    )
    # A speaker merged into another no longer shows under its own name.
    await _meeting(
        session_maker,
        5,
        "Call five",
        speakers=[{"local_name": "Erin Madeup", "merged_into_id": 1}],
    )
    # Bob's meetings never answer Alice's search.
    await _meeting(
        session_maker,
        6,
        "Bob's call",
        owner=BOB,
        speakers=[{"person": "Alice Example"}],
    )

    assert await _search(client, "alice example") == [by_person]
    assert await _search(client, "Sample") == [by_local]
    assert await _search(client, "carol") == [by_legacy]
    assert await _search(client, "Dev Fictional") == ["rec-4"]
    assert await _search(client, "Erin") == []


async def test_a_phrase_crosses_a_line_break_in_live_lines_only_in_order(
    session_maker: sessionmaker, client: AsyncClient
) -> None:
    crossing = await _meeting(
        session_maker,
        1,
        "Crossing",
        settling_lines=["We agreed that the quarterly", "lighthouse budget stands."],
    )
    await _meeting(
        session_maker,
        2,
        "Reversed",
        settling_lines=["lighthouse budget stands.", "We agreed that the quarterly"],
    )

    assert await _search(client, "quarterly lighthouse budget") == [crossing]
    assert await _search(client, "the quarterly") == ["rec-2", crossing]


async def test_a_meeting_matching_many_ways_is_listed_once(
    session_maker: sessionmaker, client: AsyncClient
) -> None:
    async with session_maker() as session:
        people = [GlobalSpeaker(name=f"Person {n}", user_id=ALICE) for n in range(3)]
        session.add_all(people)
        await session.commit()
        people_ids = [person.id for person in people]
    busy = await _meeting(
        session_maker,
        1,
        "Atlas kickoff",
        transcript="Atlas, atlas and more atlas.",
        speakers=[{"global_speaker_id": person} for person in people_ids]
        + [{"local_name": "Atlas Guest"}],
        tags=["Atlas", "Atlas Q4", "Clients"],
    )
    other = await _meeting(session_maker, 2, "Other", tags=["Customers"])

    assert await _search(client, "atlas") == [busy]
    response = await client.get(
        "/api/v1/recordings/", params={"speaker_ids": people_ids}
    )
    assert [item["id"] for item in response.json()] == [busy]
    async with session_maker() as session:
        tag_ids = [
            tag_id
            for (tag_id,) in (
                await session.execute(text("SELECT id FROM tags ORDER BY id"))
            ).all()
        ]
    response = await client.get("/api/v1/recordings/", params={"tag_ids": tag_ids})
    assert [item["id"] for item in response.json()] == [other, busy]


async def test_percent_and_underscore_are_matched_as_typed(
    session_maker: sessionmaker, client: AsyncClient
) -> None:
    discount = await _meeting(session_maker, 1, "50% off pricing")
    await _meeting(session_maker, 2, "500 seats")
    underscored = await _meeting(session_maker, 3, "Plan_B review")
    await _meeting(session_maker, 4, "Plan B review")

    assert await _search(client, "50%") == [discount]
    assert await _search(client, "plan_b") == [underscored]
    assert like_pattern("a\\b") == "%a\\\\b%"


async def test_a_blank_search_lists_everything(
    session_maker: sessionmaker, client: AsyncClient
) -> None:
    for recording_id in range(1, 4):
        await _meeting(session_maker, recording_id, f"Meeting {recording_id}")

    assert await _search(client, "   ") == ["rec-3", "rec-2", "rec-1"]


async def test_meetings_that_started_together_page_in_one_order_without_overlap(
    session_maker: sessionmaker, client: AsyncClient
) -> None:
    same_start = START + timedelta(days=1)
    for recording_id in range(1, 6):
        await _meeting(
            session_maker,
            recording_id,
            f"Imported {recording_id}",
            created_at=same_start,
            tags=[f"Batch {recording_id}"],
        )
    newest = await _meeting(
        session_maker, 6, "Newest", created_at=same_start + timedelta(hours=1)
    )

    pages = []
    for skip in range(0, 6, 2):
        response = await client.get(
            "/api/v1/recordings/", params={"skip": skip, "limit": 2}
        )
        pages.append(response.json())

    listed = [item["id"] for page in pages for item in page]
    assert listed == [newest, "rec-5", "rec-4", "rec-3", "rec-2", "rec-1"]
    # Each paged row carries its own tags.
    assert [
        [tag["name"] for tag in item["tags"]] for page in pages for item in page
    ] == [[], ["Batch 5"], ["Batch 4"], ["Batch 3"], ["Batch 2"], ["Batch 1"]]


async def test_search_results_page_and_carry_their_tags(
    session_maker: sessionmaker, client: AsyncClient
) -> None:
    for recording_id in range(1, 6):
        await _meeting(
            session_maker,
            recording_id,
            f"Roadmap {recording_id}",
            tags=["Planning", f"Quarter {recording_id}"],
        )
    await _meeting(session_maker, 6, "Lunch", tags=["Planning"])

    response = await client.get(
        "/api/v1/recordings/", params={"q": "roadmap", "skip": 2, "limit": 2}
    )

    assert [
        (item["id"], [tag["name"] for tag in item["tags"]]) for item in response.json()
    ] == [
        ("rec-3", ["Planning", "Quarter 3"]),
        ("rec-2", ["Planning", "Quarter 2"]),
    ]
