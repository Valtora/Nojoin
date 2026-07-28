"""Tests for the Nojoin MCP tools.

Read tools (get_speakers, list_people, get_documents, get_person) and write
tools (import_people, set_speaker_name, append_meeting_notes) are called
directly as the plain coroutines registered on the FastMCP instance, with
the authenticated user and granted scopes injected through the same
contextvars the auth middleware sets.
"""

from __future__ import annotations

import logging
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
from backend.mcp_server import server as mcp_server
from backend.mcp_server.auth import current_mcp_scopes, current_mcp_user
from backend.mcp_server.server import (
    PersonImportEntry,
    append_meeting_notes,
    get_documents,
    get_person,
    get_speakers,
    import_people,
    list_people,
    list_recordings,
    list_tags,
    set_speaker_name,
)
from backend.models.people_tag import PeopleTag, PeopleTagLink
from backend.models.speaker import GlobalSpeaker
from backend.models.transcript import Transcript
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
        has_seen_companion_retirement_notice BOOLEAN NOT NULL DEFAULT 0,
        invitation_id INTEGER
    )
    """,
    """
    CREATE TABLE global_speakers (
        embedding_version INTEGER,
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
        max_speakers INTEGER,
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
        embedding_version INTEGER,
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
    """
    CREATE TABLE transcripts (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        recording_id INTEGER UNIQUE,
        text TEXT,
        segments JSON,
        notes TEXT,
        user_notes TEXT,
        meeting_edge_focus TEXT,
        meeting_edge_payload JSON,
        meeting_edge_status VARCHAR DEFAULT 'idle',
        meeting_edge_error_message TEXT,
        meeting_edge_source_signature TEXT,
        speaker_name_suggestions JSON,
        notes_template_id INTEGER,
        notes_template_sections TEXT,
        notes_status VARCHAR,
        transcript_status VARCHAR,
        error_message TEXT
    )
    """,
    """
    CREATE TABLE documents (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        recording_id INTEGER NOT NULL,
        title VARCHAR(255) NOT NULL,
        file_path VARCHAR(1024) NOT NULL,
        file_type VARCHAR(128) NOT NULL DEFAULT 'text/plain',
        status VARCHAR(32) NOT NULL DEFAULT 'READY',
        error_message TEXT
    )
    """,
    """
    CREATE TABLE context_chunks (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        recording_id INTEGER NOT NULL,
        document_id INTEGER,
        content TEXT NOT NULL,
        embedding BLOB,
        meta JSON
    )
    """,
    """
    CREATE TABLE tags (
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
    CREATE TABLE recording_tags (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        recording_id INTEGER NOT NULL,
        tag_id INTEGER NOT NULL
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
    session_maker, *, person_id: int, name: str, user_id: int, **fields: str
) -> None:
    """Insert a GlobalSpeaker row; extra CRM fields (title, notes, ...) pass through."""
    async with session_maker() as session:
        session.add(GlobalSpeaker(id=person_id, name=name, user_id=user_id, **fields))
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
async def test_list_people_includes_person_tags(
    session_maker, test_user: User, mcp_context
):
    """Guard the analogue of the list_recordings tag bug: list_people must
    surface a person's People-library tags, not an empty list."""
    bind_mcp_identity(test_user)
    await seed_person(session_maker, person_id=11, name="Dana", user_id=test_user.id)
    async with session_maker() as session:
        session.add(PeopleTag(id=1, name="VIP", user_id=test_user.id))
        session.add(PeopleTagLink(global_speaker_id=11, tag_id=1))
        await session.commit()

    people = await list_people()

    assert people[0]["tags"] == ["VIP"]


@pytest.mark.anyio
async def test_list_tags_returns_all_beyond_one_page(
    session_maker, test_user: User, mcp_context
):
    """list_tags must page through read_tags rather than silently returning
    only the first page for a user with many tags."""
    bind_mcp_identity(test_user)
    total = mcp_server._TAG_PAGE_SIZE + 25
    async with session_maker() as session:
        for index in range(total):
            await session.execute(
                text(
                    """
                    INSERT INTO tags (id, created_at, updated_at, name, user_id)
                    VALUES (:id, :ts, :ts, :name, :uid)
                    """
                ),
                {
                    "id": index + 1,
                    "ts": TEST_TIMESTAMP,
                    "name": f"tag-{index:03d}",
                    "uid": test_user.id,
                },
            )
        await session.commit()

    tags = await list_tags()

    assert len(tags) == total


async def seed_recording_tag(
    session_maker, *, tag_id: int, recording_id: int, name: str, user_id: int
) -> None:
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO tags (id, created_at, updated_at, name, user_id)
                VALUES (:id, :ts, :ts, :name, :user_id)
                """
            ),
            {"id": tag_id, "ts": TEST_TIMESTAMP, "name": name, "user_id": user_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO recording_tags (created_at, updated_at, recording_id, tag_id)
                VALUES (:ts, :ts, :rec, :tag)
                """
            ),
            {"ts": TEST_TIMESTAMP, "rec": recording_id, "tag": tag_id},
        )
        await session.commit()


@pytest.mark.anyio
async def test_list_recordings_includes_tags_and_speakers(
    session_maker, test_user: User, mcp_context
):
    """Regression: list_recordings must report a recording's tags and speakers,
    not the always-empty projection the web list serializer returns."""
    bind_mcp_identity(test_user)
    await seed_person(session_maker, person_id=11, name="Dana", user_id=test_user.id)
    public_id = await seed_recording_with_speakers(session_maker, user_id=test_user.id)
    await seed_recording_tag(
        session_maker,
        tag_id=1,
        recording_id=1,
        name="Dealflow",
        user_id=test_user.id,
    )

    recordings = await list_recordings()

    assert len(recordings) == 1
    recording = recordings[0]
    assert recording["id"] == public_id
    assert recording["tags"] == ["Dealflow"]
    assert recording["is_archived"] is False
    assert recording["is_deleted"] is False
    # Linked person name resolves; the merged speaker is excluded.
    assert set(recording["speakers"]) == {"Dana", "Guest"}


async def seed_bare_recording(
    session_maker, *, recording_id: int, public_id: str, user_id: int, state: str
) -> None:
    """Seed a recording in one of: active, archived, deleted."""
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    is_archived, is_deleted, user_id
                ) VALUES (
                    :id, :ts, :ts, :name, :public_id, :uid,
                    '/tmp/a.wav', 'PROCESSED', 100, 100,
                    :archived, :deleted, :user_id
                )
                """
            ),
            {
                "id": recording_id,
                "ts": TEST_TIMESTAMP,
                "name": f"Recording {recording_id}",
                "public_id": public_id,
                "uid": f"uid-{recording_id}",
                "archived": 1 if state == "archived" else 0,
                "deleted": 1 if state == "deleted" else 0,
                "user_id": user_id,
            },
        )
        await session.commit()


@pytest.mark.anyio
async def test_list_recordings_covers_archived_and_deleted(
    session_maker, test_user: User, mcp_context
):
    """Search must reach archived and soft-deleted meetings, tagging each
    result with its state so the caller can distinguish them."""
    bind_mcp_identity(test_user)
    for recording_id, public_id, state in (
        (1, "rec-active", "active"),
        (2, "rec-archived", "archived"),
        (3, "rec-deleted", "deleted"),
    ):
        await seed_bare_recording(
            session_maker,
            recording_id=recording_id,
            public_id=public_id,
            user_id=test_user.id,
            state=state,
        )

    everything = await list_recordings()

    assert {r["id"] for r in everything} == {
        "rec-active",
        "rec-archived",
        "rec-deleted",
    }
    states = {r["id"]: (r["is_archived"], r["is_deleted"]) for r in everything}
    assert states["rec-active"] == (False, False)
    assert states["rec-archived"] == (True, False)
    assert states["rec-deleted"] == (False, True)


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
    public_id = await seed_recording_with_speakers(session_maker, user_id=test_user.id)

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
                    select(GlobalSpeaker).where(GlobalSpeaker.user_id == test_user.id)
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
        await import_people([PersonImportEntry(name=f"Person {i}") for i in range(201)])


# --- get_documents ---------------------------------------------------------


async def seed_document(
    session_maker,
    *,
    document_id: int,
    recording_id: int,
    title: str,
    chunks: list[str],
) -> None:
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, created_at, updated_at, recording_id, title,
                    file_path, file_type, status
                ) VALUES (
                    :id, :ts, :ts, :rec, :title,
                    :path, 'text/plain', 'READY'
                )
                """
            ),
            {
                "id": document_id,
                "ts": TEST_TIMESTAMP,
                "rec": recording_id,
                "title": title,
                "path": f"/data/documents/{document_id}.txt",
            },
        )
        for index, content in enumerate(chunks):
            await session.execute(
                text(
                    """
                    INSERT INTO context_chunks (
                        created_at, updated_at, recording_id, document_id, content
                    ) VALUES (:ts, :ts, :rec, :doc, :content)
                    """
                ),
                {
                    "ts": TEST_TIMESTAMP,
                    "rec": recording_id,
                    "doc": document_id,
                    "content": content,
                },
            )
        await session.commit()


@pytest.mark.anyio
async def test_get_documents_reconstructs_text_from_chunks(
    session_maker, test_user: User, mcp_context
):
    bind_mcp_identity(test_user)
    public_id = await seed_recording_with_speakers(session_maker, user_id=test_user.id)
    await seed_document(
        session_maker,
        document_id=5,
        recording_id=1,
        title="Agenda",
        chunks=["Part one. ", "Part two."],
    )

    docs = await get_documents(public_id)

    assert len(docs) == 1
    assert docs[0]["title"] == "Agenda"
    assert docs[0]["text"] == "Part one. Part two."
    assert docs[0]["text_truncated"] is False


@pytest.mark.anyio
async def test_get_documents_truncates_long_text(
    session_maker, test_user: User, mcp_context, monkeypatch
):
    bind_mcp_identity(test_user)
    monkeypatch.setattr(mcp_server, "_DOCUMENT_TEXT_CHAR_LIMIT", 10)
    public_id = await seed_recording_with_speakers(session_maker, user_id=test_user.id)
    await seed_document(
        session_maker,
        document_id=5,
        recording_id=1,
        title="Long",
        chunks=["0123456789ABCDEF"],
    )

    docs = await get_documents(public_id)

    assert docs[0]["text"] == "0123456789"
    assert docs[0]["text_truncated"] is True


@pytest.mark.anyio
async def test_get_documents_rejects_other_users_recording(
    session_maker, test_user: User, mcp_context
):
    bind_mcp_identity(test_user)
    public_id = await seed_recording_with_speakers(session_maker, user_id=999)

    with pytest.raises(HTTPException) as exc_info:
        await get_documents(public_id)
    assert exc_info.value.status_code == 404


# --- get_person ------------------------------------------------------------


@pytest.mark.anyio
async def test_get_person_includes_profile_tags_and_meetings(
    session_maker, test_user: User, mcp_context
):
    bind_mcp_identity(test_user)
    await seed_person(
        session_maker,
        person_id=11,
        name="Dana",
        user_id=test_user.id,
        title="CTO",
        company="Acme",
    )
    # Tag the person.
    async with session_maker() as session:
        session.add(PeopleTag(id=1, name="VIP", user_id=test_user.id))
        session.add(PeopleTagLink(global_speaker_id=11, tag_id=1))
        await session.commit()
    public_id = await seed_recording_with_speakers(session_maker, user_id=test_user.id)

    person = await get_person(11)

    assert person["id"] == 11
    assert person["name"] == "Dana"
    assert person["title"] == "CTO"
    assert person["company"] == "Acme"
    assert person["tags"] == ["VIP"]
    # Dana is linked to the recording via SPEAKER_00 (not the merged speaker).
    assert [m["id"] for m in person["meetings"]] == [public_id]
    assert person["meetings"][0]["name"] == "Weekly sync"


@pytest.mark.anyio
async def test_get_person_rejects_missing_or_foreign_person(
    session_maker, test_user: User, mcp_context
):
    bind_mcp_identity(test_user)
    await seed_person(session_maker, person_id=11, name="Zoe", user_id=999)

    with pytest.raises(ToolError, match="No person with id"):
        await get_person(11)  # belongs to another user
    with pytest.raises(ToolError, match="No person with id"):
        await get_person(999)  # does not exist


# --- set_speaker_name ------------------------------------------------------


@pytest.mark.anyio
async def test_set_speaker_name_maps_to_global_speaker_name_field(
    session_maker, test_user: User, mcp_context, monkeypatch
):
    """The tool must pass the name as SpeakerUpdate.global_speaker_name and
    surface the linked person from the delegated call."""
    from types import SimpleNamespace

    captured: dict = {}

    async def fake_update(recording_id, update, *, db, current_user):
        captured["recording_id"] = recording_id
        captured["update"] = update
        captured["user"] = current_user
        return [
            SimpleNamespace(
                diarization_label=update.diarization_label,
                global_speaker=SimpleNamespace(name=update.global_speaker_name),
            )
        ]

    monkeypatch.setattr(
        "backend.api.v1.endpoints.speakers.routes_recording.update_recording_speaker",
        fake_update,
    )
    bind_mcp_identity(test_user)

    result = await set_speaker_name("rec-1", "SPEAKER_00", "Dana")

    assert captured["recording_id"] == "rec-1"
    assert captured["update"].diarization_label == "SPEAKER_00"
    assert captured["update"].global_speaker_name == "Dana"
    assert captured["user"].id == test_user.id
    assert result["linked_person"] == "Dana"
    assert result["diarization_label"] == "SPEAKER_00"


@pytest.mark.anyio
async def test_set_speaker_name_requires_write_scope(
    session_maker, test_user: User, mcp_context, monkeypatch
):
    called = False

    async def fake_update(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(
        "backend.api.v1.endpoints.speakers.routes_recording.update_recording_speaker",
        fake_update,
    )
    bind_mcp_identity(test_user, scopes=frozenset({MCP_READ_SCOPE}))

    with pytest.raises(ToolError, match="read-only"):
        await set_speaker_name("rec-1", "SPEAKER_00", "Dana")
    assert called is False


# --- append_meeting_notes --------------------------------------------------


async def seed_transcript(session_maker, *, recording_id: int, user_notes: str) -> None:
    async with session_maker() as session:
        session.add(Transcript(recording_id=recording_id, user_notes=user_notes))
        await session.commit()


@pytest.fixture
def no_meeting_edge_dispatch(stub_meeting_edge_dispatch):
    """Stop the delegated notes update from dispatching a Meeting Edge refresh.

    Irrelevant to the append logic here. Delegates to the shared fixture, which
    patches every module that binds the dispatcher rather than only this one.
    """
    return stub_meeting_edge_dispatch


@pytest.mark.anyio
async def test_append_meeting_notes_preserves_existing(
    session_maker, test_user: User, mcp_context, no_meeting_edge_dispatch
):
    bind_mcp_identity(test_user)
    public_id = await seed_recording_with_speakers(session_maker, user_id=test_user.id)
    await seed_transcript(session_maker, recording_id=1, user_notes="First line.")

    result = await append_meeting_notes(public_id, "Second line.")

    assert result["user_notes"] == "First line.\n\nSecond line."
    async with session_maker() as session:
        transcript = (await session.execute(select(Transcript))).scalar_one()
        assert transcript.user_notes == "First line.\n\nSecond line."


@pytest.mark.anyio
async def test_append_meeting_notes_creates_notes_when_absent(
    session_maker, test_user: User, mcp_context, no_meeting_edge_dispatch
):
    bind_mcp_identity(test_user)
    public_id = await seed_recording_with_speakers(session_maker, user_id=test_user.id)

    result = await append_meeting_notes(public_id, "Fresh note.")

    assert result["user_notes"] == "Fresh note."


@pytest.mark.anyio
async def test_append_meeting_notes_rejects_empty_text_and_read_only(
    session_maker, test_user: User, mcp_context
):
    public_id = await seed_recording_with_speakers(session_maker, user_id=test_user.id)

    bind_mcp_identity(test_user)
    with pytest.raises(ToolError, match="must not be empty"):
        await append_meeting_notes(public_id, "   ")

    bind_mcp_identity(test_user, scopes=frozenset({MCP_READ_SCOPE}))
    with pytest.raises(ToolError, match="read-only"):
        await append_meeting_notes(public_id, "blocked")


# --- tool-call logging -----------------------------------------------------

MCP_LOGGER = "backend.mcp_server.server"


@pytest.mark.anyio
async def test_tool_logging_records_success(
    session_maker, test_user: User, mcp_context, caplog
):
    bind_mcp_identity(test_user)
    with caplog.at_level(logging.INFO, logger=MCP_LOGGER):
        await list_people()

    records = [r for r in caplog.records if r.name == MCP_LOGGER]
    assert any(
        r.levelno == logging.INFO
        and "mcp tool list_people ok" in r.getMessage()
        and f"user={test_user.id}" in r.getMessage()
        for r in records
    )


@pytest.mark.anyio
async def test_tool_logging_redacts_free_text(
    session_maker, test_user: User, mcp_context, no_meeting_edge_dispatch, caplog
):
    bind_mcp_identity(test_user)
    public_id = await seed_recording_with_speakers(session_maker, user_id=test_user.id)
    secret = "CONFIDENTIAL-NOTE-BODY-9137"

    with caplog.at_level(logging.INFO, logger=MCP_LOGGER):
        await append_meeting_notes(public_id, secret)

    messages = "\n".join(r.getMessage() for r in caplog.records if r.name == MCP_LOGGER)
    # The note body must never reach the log; only its length is recorded.
    assert secret not in messages
    assert "text=<str:" in messages
    assert "append_meeting_notes ok" in messages


@pytest.mark.anyio
async def test_tool_logging_warns_on_rejection(
    session_maker, test_user: User, mcp_context, caplog
):
    bind_mcp_identity(test_user, scopes=frozenset({MCP_READ_SCOPE}))
    with caplog.at_level(logging.INFO, logger=MCP_LOGGER):
        with pytest.raises(ToolError):
            await import_people([PersonImportEntry(name="Dana")])

    assert any(
        r.levelno == logging.WARNING and "import_people rejected" in r.getMessage()
        for r in caplog.records
        if r.name == MCP_LOGGER
    )
