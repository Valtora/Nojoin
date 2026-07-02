"""Tests for the MCP People tools: get_speakers, list_people, import_people.

The tools are called directly (they are plain coroutines registered on the
FastMCP instance), with the authenticated user and granted scopes injected
through the same contextvars the auth middleware sets.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import HTTPException
from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlmodel import select

from backend.api.v1.api import api_router  # noqa: F401 - registers all model mappers
from backend.core.security import MCP_READ_SCOPE, MCP_WRITE_SCOPE
from backend.mcp_server.auth import current_mcp_scopes, current_mcp_user
from backend.mcp_server.server import (
    PersonImportEntry,
    get_speakers,
    import_people,
    list_people,
)
from backend.models.people_tag import PeopleTag, PeopleTagLink
from backend.models.speaker import GlobalSpeaker
from backend.models.user import User

TEST_TIMESTAMP = datetime(2026, 6, 1, 12, 0, 0)

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
    CREATE TABLE global_speakers (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        name VARCHAR(255) NOT NULL,
        embedding BLOB,
        user_id INTEGER,
        color VARCHAR(64),
        title VARCHAR(255),
        company VARCHAR(255),
        email VARCHAR(255),
        phone_number VARCHAR(64),
        notes TEXT,
        is_voiceprint_locked BOOLEAN NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE p_tags (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        name VARCHAR(255) NOT NULL,
        color VARCHAR(64),
        user_id INTEGER,
        parent_id INTEGER
    )
    """,
    """
    CREATE TABLE people_tags (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        global_speaker_id INTEGER NOT NULL,
        tag_id INTEGER NOT NULL,
        UNIQUE(global_speaker_id, tag_id)
    )
    """,
    """
    CREATE TABLE recordings (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        name VARCHAR(255) NOT NULL,
        public_id VARCHAR(36) NOT NULL,
        meeting_uid VARCHAR(36),
        audio_path VARCHAR(1024) NOT NULL,
        proxy_path VARCHAR(1024),
        celery_task_id VARCHAR(255),
        duration_seconds FLOAT,
        file_size_bytes INTEGER,
        status VARCHAR(32) NOT NULL,
        client_status VARCHAR(32),
        upload_progress INTEGER NOT NULL,
        processing_progress INTEGER NOT NULL,
        processing_step VARCHAR(255),
        processing_started_at DATETIME,
        processing_completed_at DATETIME,
        pipeline_generation VARCHAR(32) DEFAULT 'unified',
        is_archived BOOLEAN NOT NULL,
        is_deleted BOOLEAN NOT NULL,
        last_activity_at DATETIME,
        user_id INTEGER,
        calendar_event_id INTEGER
    )
    """,
    """
    CREATE TABLE recording_speakers (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        public_id VARCHAR(36),
        recording_id INTEGER NOT NULL,
        global_speaker_id INTEGER,
        diarization_label VARCHAR(255) NOT NULL,
        name VARCHAR(255),
        local_name VARCHAR(255),
        speaker_status VARCHAR(32) NOT NULL DEFAULT 'active',
        speaker_kind VARCHAR(32) NOT NULL DEFAULT 'automated',
        processing_run_id INTEGER,
        last_speaker_correction_event_id INTEGER,
        last_diarization_window_result_id INTEGER,
        first_seen_ms INTEGER,
        last_seen_ms INTEGER,
        identity_confidence FLOAT,
        identity_locked BOOLEAN NOT NULL DEFAULT 0,
        voice_snippet_path VARCHAR(1024),
        snippet_start FLOAT,
        snippet_end FLOAT,
        embedding BLOB,
        color VARCHAR(64),
        merged_into_id INTEGER,
        FOREIGN KEY(recording_id) REFERENCES recordings(id),
        FOREIGN KEY(global_speaker_id) REFERENCES global_speakers(id)
    )
    """,
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def session_maker():
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


@pytest.fixture
async def test_user(session_maker) -> User:
    async with session_maker() as session:
        user = User(
            username="alice",
            hashed_password="hashed",
            role="user",
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


@pytest.fixture
def mcp_context(monkeypatch, session_maker):
    """Point the tools' lazily imported session maker at the test database."""
    import backend.core.db as core_db

    monkeypatch.setattr(core_db, "async_session_maker", session_maker)


def bind_mcp_identity(
    user: User,
    scopes: frozenset[str] = frozenset({MCP_READ_SCOPE, MCP_WRITE_SCOPE}),
) -> None:
    """Set the request contextvars the auth middleware would set.

    Must be called inside the async test itself: the anyio runner copies the
    context when it starts, so values set in a synchronous fixture would not
    be visible to the test coroutine.
    """
    current_mcp_user.set(user)
    current_mcp_scopes.set(scopes)


async def seed_person(
    session_maker,
    *,
    person_id: int,
    name: str,
    user_id: int,
    title: str | None = None,
    notes: str | None = None,
) -> None:
    async with session_maker() as session:
        session.add(
            GlobalSpeaker(
                id=person_id,
                name=name,
                user_id=user_id,
                title=title,
                notes=notes,
            )
        )
        await session.commit()


async def seed_recording_with_speakers(session_maker, *, user_id: int) -> str:
    """A recording with one linked, one local-named, and one merged speaker."""
    public_id = "11111111-2222-3333-4444-555555555555"
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    is_archived, is_deleted, user_id
                ) VALUES (
                    1, :ts, :ts, 'Weekly sync', :public_id, 'uid-1',
                    '/tmp/a.wav', 'PROCESSED', 100, 100, 0, 0, :user_id
                )
                """
            ),
            {"ts": TEST_TIMESTAMP, "public_id": public_id, "user_id": user_id},
        )
        for row in (
            # Linked to the seeded person (id 11).
            "(1, :ts, :ts, 'rs-1', 1, 11, 'SPEAKER_00', NULL, NULL, NULL)",
            # Local-only name.
            "(2, :ts, :ts, 'rs-2', 1, NULL, 'SPEAKER_01', NULL, 'Guest', NULL)",
            # Merged away: must not appear in results.
            "(3, :ts, :ts, 'rs-3', 1, NULL, 'SPEAKER_02', NULL, NULL, 1)",
        ):
            await session.execute(
                text(
                    """
                    INSERT INTO recording_speakers (
                        id, created_at, updated_at, public_id, recording_id,
                        global_speaker_id, diarization_label, name, local_name,
                        merged_into_id
                    ) VALUES
                    """
                    + row
                ),
                {"ts": TEST_TIMESTAMP},
            )
        await session.commit()
    return public_id


@pytest.mark.anyio
async def test_list_people_returns_only_own_library(
    session_maker, test_user: User, mcp_context
):
    bind_mcp_identity(test_user)
    await seed_person(
        session_maker,
        person_id=11,
        name="Dana",
        user_id=test_user.id,
        title="CTO",
    )
    await seed_person(session_maker, person_id=12, name="Ben", user_id=test_user.id)
    await seed_person(session_maker, person_id=13, name="Zoe", user_id=999)

    people = await list_people()

    assert [person["name"] for person in people] == ["Ben", "Dana"]
    dana = people[1]
    assert dana["id"] == 11
    assert dana["title"] == "CTO"
    assert dana["recording_count"] == 0
    assert dana["has_voiceprint"] is False
    assert dana["tags"] == []


@pytest.mark.anyio
async def test_list_people_query_filter(session_maker, test_user: User, mcp_context):
    bind_mcp_identity(test_user)
    await seed_person(
        session_maker,
        person_id=11,
        name="Dana",
        user_id=test_user.id,
        notes="Acme procurement contact",
    )
    await seed_person(session_maker, person_id=12, name="Ben", user_id=test_user.id)

    people = await list_people(query="acme")

    assert [person["name"] for person in people] == ["Dana"]


@pytest.mark.anyio
async def test_get_speakers_resolves_names_and_person_links(
    session_maker, test_user: User, mcp_context
):
    bind_mcp_identity(test_user)
    await seed_person(
        session_maker,
        person_id=11,
        name="Dana",
        user_id=test_user.id,
        title="CTO",
    )
    public_id = await seed_recording_with_speakers(
        session_maker, user_id=test_user.id
    )

    result = await get_speakers(public_id)

    assert result["recording_id"] == public_id
    assert result["name"] == "Weekly sync"
    by_label = {s["diarization_label"]: s for s in result["speakers"]}
    # The merged speaker (SPEAKER_02) is excluded.
    assert set(by_label) == {"SPEAKER_00", "SPEAKER_01"}
    assert by_label["SPEAKER_00"]["display_name"] == "Dana"
    assert by_label["SPEAKER_00"]["person"] == {
        "id": 11,
        "name": "Dana",
        "title": "CTO",
        "company": None,
    }
    assert by_label["SPEAKER_01"]["display_name"] == "Guest"
    assert by_label["SPEAKER_01"]["person"] is None


@pytest.mark.anyio
async def test_get_speakers_rejects_other_users_recording(
    session_maker, test_user: User, mcp_context
):
    bind_mcp_identity(test_user)
    await seed_person(session_maker, person_id=11, name="Dana", user_id=999)
    public_id = await seed_recording_with_speakers(session_maker, user_id=999)

    with pytest.raises(HTTPException) as exc_info:
        await get_speakers(public_id)
    assert exc_info.value.status_code == 404


@pytest.mark.anyio
async def test_import_people_creates_people_with_tags(
    session_maker, test_user: User, mcp_context
):
    bind_mcp_identity(test_user)
    result = await import_people(
        [
            PersonImportEntry(
                name="Dana",
                title="CTO",
                company="Acme",
                email="dana@acme.test",
                tags=["Customer", "VIP"],
            ),
            PersonImportEntry(name="Ben", tags=["Customer"]),
        ]
    )

    assert result["summary"] == {"created": 2, "updated": 0, "skipped": 0, "error": 0}
    assert {entry["action"] for entry in result["results"]} == {"created"}

    async with session_maker() as session:
        people = (
            (
                await session.execute(
                    select(GlobalSpeaker).where(
                        GlobalSpeaker.user_id == test_user.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {person.name for person in people} == {"Dana", "Ben"}
        tags = (await session.execute(select(PeopleTag))).scalars().all()
        # "Customer" is created once and reused across entries.
        assert sorted(tag.name for tag in tags) == ["Customer", "VIP"]
        links = (await session.execute(select(PeopleTagLink))).scalars().all()
        assert len(links) == 3


@pytest.mark.anyio
async def test_import_people_updates_and_skips_existing(
    session_maker, test_user: User, mcp_context
):
    bind_mcp_identity(test_user)
    await seed_person(
        session_maker,
        person_id=11,
        name="Dana",
        user_id=test_user.id,
        title="Engineer",
        notes="Met at conference",
    )

    skipped = await import_people(
        [PersonImportEntry(name="Dana", title="CTO")], on_conflict="skip"
    )
    assert skipped["summary"]["skipped"] == 1

    updated = await import_people([PersonImportEntry(name="Dana", title="CTO")])
    assert updated["summary"]["updated"] == 1

    async with session_maker() as session:
        dana = (
            await session.execute(select(GlobalSpeaker).where(GlobalSpeaker.id == 11))
        ).scalar_one()
        assert dana.title == "CTO"
        # Fields not supplied by the import are left untouched.
        assert dana.notes == "Met at conference"


@pytest.mark.anyio
async def test_import_people_isolates_bad_entries(
    session_maker, test_user: User, mcp_context
):
    bind_mcp_identity(test_user)
    result = await import_people(
        [
            PersonImportEntry(name="   "),
            PersonImportEntry(name="Ben"),
        ]
    )

    assert result["summary"]["created"] == 1
    assert result["summary"]["error"] == 1
    async with session_maker() as session:
        people = (await session.execute(select(GlobalSpeaker))).scalars().all()
        assert [person.name for person in people] == ["Ben"]


@pytest.mark.anyio
async def test_import_people_requires_write_scope(
    session_maker, test_user: User, mcp_context
):
    bind_mcp_identity(test_user, scopes=frozenset({MCP_READ_SCOPE}))
    with pytest.raises(ToolError, match="read-only"):
        await import_people([PersonImportEntry(name="Dana")])

    async with session_maker() as session:
        people = (await session.execute(select(GlobalSpeaker))).scalars().all()
        assert people == []


@pytest.mark.anyio
async def test_import_people_rejects_empty_and_oversized_batches(
    test_user: User, mcp_context
):
    bind_mcp_identity(test_user)
    with pytest.raises(ToolError, match="at least one"):
        await import_people([])
    with pytest.raises(ToolError, match="At most"):
        await import_people(
            [PersonImportEntry(name=f"Person {i}") for i in range(201)]
        )
