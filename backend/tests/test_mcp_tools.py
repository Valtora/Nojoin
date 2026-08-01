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
from backend.core.security import MCP_DESTROY_SCOPE, MCP_READ_SCOPE, MCP_WRITE_SCOPE
from backend.mcp_server import server as mcp_server
from backend.mcp_server.auth import current_mcp_scopes, current_mcp_user
from backend.mcp_server.server import (
    append_meeting_notes,
    get_documents,
    get_transcript_utterances,
    list_recordings,
    list_tags,
)
from backend.mcp_server.tools_manage import (
    archive_recording,
    attach_document,
    correct_utterance_text,
    destroy_recording,
    link_calendar_event,
    list_calendar_events,
    rename_recording,
    restore_recording,
    tag_recording,
    trash_recording,
    untag_recording,
)
from backend.mcp_server.tools_people import (
    PersonImportEntry,
    get_person,
    get_speakers,
    import_people,
    list_people,
    set_speaker_name,
)
from backend.mcp_server.tools_search import search_context
from backend.mcp_server.tools_tasks import (
    create_task,
    delete_task,
    list_tasks,
    update_task,
)
from backend.models.people_tag import PeopleTag, PeopleTagLink
from backend.models.pipeline import (
    TranscriptUtterance,
    TranscriptUtteranceEvent,
    TranscriptUtteranceState,
)
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
        notes_stale_documents BOOLEAN DEFAULT 0,
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
        file_size_bytes INTEGER,
        status VARCHAR(32) NOT NULL DEFAULT 'READY',
        error_message TEXT,
        parse_mode VARCHAR(32) NOT NULL DEFAULT 'VISUAL',
        parse_warning TEXT,
        page_count INTEGER,
        pages_parsed INTEGER NOT NULL DEFAULT 0,
        parse_stage VARCHAR(64)
    )
    """,
    """
    CREATE TABLE document_pages (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        document_id INTEGER NOT NULL,
        page_number INTEGER NOT NULL,
        title VARCHAR(512),
        content TEXT NOT NULL DEFAULT '',
        parse_mode VARCHAR(32) NOT NULL DEFAULT 'STRUCTURAL',
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
        document_page_id INTEGER,
        content TEXT NOT NULL,
        embedding BLOB,
        meta JSON,
        embedding_version INTEGER
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
    """
    CREATE TABLE transcript_utterances (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        public_id VARCHAR(36) NOT NULL,
        recording_id INTEGER NOT NULL,
        sort_key VARCHAR(64) NOT NULL,
        start_ms INTEGER NOT NULL,
        end_ms INTEGER NOT NULL,
        text TEXT NOT NULL,
        speaker_label VARCHAR(255),
        recording_speaker_id INTEGER,
        state VARCHAR(32) NOT NULL,
        source_kind VARCHAR(255) NOT NULL,
        processing_run_id INTEGER,
        last_utterance_event_id INTEGER,
        last_diarization_window_result_id INTEGER,
        revision INTEGER NOT NULL,
        overlap_group_id VARCHAR(64),
        overlap_rank INTEGER NOT NULL,
        manual_text_locked BOOLEAN NOT NULL,
        manual_speaker_locked BOOLEAN NOT NULL,
        speaker_assignment_source VARCHAR(32) NOT NULL,
        speaker_assignment_authority VARCHAR(32) NOT NULL,
        text_confidence FLOAT,
        speaker_confidence FLOAT,
        confidence_payload JSON
    )
    """,
    """
    CREATE TABLE transcript_utterance_events (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        recording_id INTEGER NOT NULL,
        utterance_id INTEGER NOT NULL,
        processing_run_id INTEGER,
        actor_user_id INTEGER,
        event_type VARCHAR(64) NOT NULL,
        source VARCHAR(64) NOT NULL,
        old_values JSON,
        new_values JSON,
        resulting_revision INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE chat_messages (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        recording_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        role VARCHAR(32) NOT NULL,
        content TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE user_tasks (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        title VARCHAR(255) NOT NULL,
        body TEXT,
        due_at DATETIME,
        completed_at DATETIME,
        archived_at DATETIME,
        user_id INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE user_task_tags (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        task_id INTEGER NOT NULL,
        tag_id INTEGER NOT NULL,
        UNIQUE(task_id, tag_id)
    )
    """,
    """
    CREATE TABLE user_task_recordings (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        task_id INTEGER NOT NULL,
        recording_id INTEGER NOT NULL,
        UNIQUE(task_id, recording_id)
    )
    """,
    """
    CREATE TABLE calendar_connections (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        user_id INTEGER NOT NULL,
        provider VARCHAR(32) NOT NULL,
        provider_account_id VARCHAR(255) NOT NULL,
        email VARCHAR(320),
        display_name VARCHAR(255),
        access_token_encrypted TEXT,
        refresh_token_encrypted TEXT,
        granted_scopes JSON NOT NULL DEFAULT '[]',
        token_expires_at DATETIME,
        sync_status VARCHAR(32) NOT NULL DEFAULT 'idle',
        sync_error VARCHAR(512),
        last_sync_started_at DATETIME,
        last_sync_completed_at DATETIME,
        last_synced_at DATETIME
    )
    """,
    """
    CREATE TABLE calendar_sources (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        connection_id INTEGER NOT NULL,
        provider_calendar_id VARCHAR(512) NOT NULL,
        name VARCHAR(255) NOT NULL,
        description TEXT,
        time_zone VARCHAR(128),
        colour VARCHAR(32),
        user_colour VARCHAR(32),
        is_primary BOOLEAN NOT NULL DEFAULT 0,
        is_read_only BOOLEAN NOT NULL DEFAULT 0,
        is_selected BOOLEAN NOT NULL DEFAULT 0,
        sync_cursor TEXT,
        last_synced_at DATETIME,
        sync_window_start DATETIME,
        sync_window_end DATETIME
    )
    """,
    """
    CREATE TABLE calendar_events (
        id INTEGER PRIMARY KEY,
        created_at DATETIME NOT NULL,
        updated_at DATETIME NOT NULL,
        calendar_id INTEGER NOT NULL,
        provider_event_id VARCHAR(512) NOT NULL,
        title VARCHAR(512) NOT NULL,
        status VARCHAR(32) NOT NULL DEFAULT 'confirmed',
        is_all_day BOOLEAN NOT NULL DEFAULT 0,
        starts_at DATETIME,
        ends_at DATETIME,
        start_date DATE,
        end_date DATE,
        location_text TEXT,
        description TEXT,
        attendees JSON,
        meeting_url VARCHAR(2048),
        source_url VARCHAR(2048),
        external_updated_at DATETIME,
        UNIQUE(calendar_id, provider_event_id)
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


async def seed_canonical_transcript(session_maker, *, recording_id: int = 1) -> None:
    """A transcript with two active canonical utterances and one superseded.

    Events 1 and 2 create u-1 and u-2; event 3 supersedes u-3, leaving the
    recording's revision cursor at 3.
    """
    async with session_maker() as session:
        session.add(
            Transcript(
                recording_id=recording_id,
                segments=[],
                notes_status="completed",
                transcript_status="completed",
            )
        )
        session.add_all(
            [
                TranscriptUtterance(
                    id=1,
                    public_id="u-1",
                    recording_id=recording_id,
                    sort_key="0001",
                    start_ms=0,
                    end_ms=1500,
                    text="Hello everyone.",
                    speaker_label="SPEAKER_00",
                    recording_speaker_id=1,
                    state=TranscriptUtteranceState.STABLE,
                    source_kind="final",
                ),
                TranscriptUtterance(
                    id=2,
                    public_id="u-2",
                    recording_id=recording_id,
                    sort_key="0002",
                    start_ms=1500,
                    end_ms=4000,
                    text="Morning.",
                    speaker_label="SPEAKER_01",
                    recording_speaker_id=2,
                    state=TranscriptUtteranceState.STABLE,
                    source_kind="final",
                ),
                TranscriptUtterance(
                    id=3,
                    public_id="u-3",
                    recording_id=recording_id,
                    sort_key="0003",
                    start_ms=4000,
                    end_ms=6000,
                    text="Replaced line.",
                    speaker_label="SPEAKER_01",
                    recording_speaker_id=2,
                    state=TranscriptUtteranceState.SUPERSEDED,
                    source_kind="final",
                ),
            ]
        )
        session.add_all(
            [
                TranscriptUtteranceEvent(
                    id=1,
                    recording_id=recording_id,
                    utterance_id=1,
                    event_type="create",
                ),
                TranscriptUtteranceEvent(
                    id=2,
                    recording_id=recording_id,
                    utterance_id=2,
                    event_type="create",
                ),
                TranscriptUtteranceEvent(
                    id=3,
                    recording_id=recording_id,
                    utterance_id=3,
                    event_type="supersede",
                ),
            ]
        )
        await session.commit()


@pytest.mark.anyio
async def test_list_recordings_reports_sync_fields(
    session_maker, test_user: User, mcp_context
):
    """A polling client must be able to see processing state and the
    canonical revision cursor without fetching any transcript."""
    bind_mcp_identity(test_user)
    await seed_person(session_maker, person_id=11, name="Dana", user_id=test_user.id)
    await seed_recording_with_speakers(session_maker, user_id=test_user.id)
    await seed_canonical_transcript(session_maker)

    recordings = await list_recordings()

    assert len(recordings) == 1
    recording = recordings[0]
    assert recording["status"] == "PROCESSED"
    assert recording["transcript_status"] == "completed"
    assert recording["notes_status"] == "completed"
    assert recording["transcript_revision"] == 3
    assert recording["updated_at"] == TEST_TIMESTAMP.isoformat()


@pytest.mark.anyio
async def test_list_recordings_sync_fields_without_transcript(
    session_maker, test_user: User, mcp_context
):
    """A recording with no transcript row reports null statuses and a zero
    cursor rather than failing."""
    bind_mcp_identity(test_user)
    await seed_bare_recording(
        session_maker,
        recording_id=1,
        public_id="rec-bare",
        user_id=test_user.id,
        state="active",
    )

    recordings = await list_recordings()

    assert recordings[0]["transcript_status"] is None
    assert recordings[0]["notes_status"] is None
    assert recordings[0]["transcript_revision"] == 0


@pytest.mark.anyio
async def test_get_transcript_utterances_full_snapshot(
    session_maker, test_user: User, mcp_context
):
    bind_mcp_identity(test_user)
    await seed_person(session_maker, person_id=11, name="Dana", user_id=test_user.id)
    public_id = await seed_recording_with_speakers(session_maker, user_id=test_user.id)
    await seed_canonical_transcript(session_maker)

    result = await get_transcript_utterances(public_id)

    assert result["recording_id"] == public_id
    assert result["revision"] == 3
    # Only active utterances appear in a snapshot; the superseded one does not.
    assert [u["id"] for u in result["utterances"]] == ["u-1", "u-2"]
    first = result["utterances"][0]
    assert first["start_ms"] == 0
    assert first["end_ms"] == 1500
    assert first["text"] == "Hello everyone."
    assert first["state"] == "stable"
    assert first["text_manually_edited"] is False
    assert result["tombstones"] == []
    assert result["total_utterances"] == 2
    assert result["next_offset"] is None
    assert {s["diarization_label"] for s in result["speakers"]} == {
        "SPEAKER_00",
        "SPEAKER_01",
    }


@pytest.mark.anyio
async def test_get_transcript_utterances_pages_with_limit_and_offset(
    session_maker, test_user: User, mcp_context
):
    """Long transcripts page: each page slices utterances only, while the
    revision, tombstones, and speakers stay complete on every page."""
    bind_mcp_identity(test_user)
    await seed_person(session_maker, person_id=11, name="Dana", user_id=test_user.id)
    public_id = await seed_recording_with_speakers(session_maker, user_id=test_user.id)
    await seed_canonical_transcript(session_maker)

    first_page = await get_transcript_utterances(public_id, limit=1)
    assert [u["id"] for u in first_page["utterances"]] == ["u-1"]
    assert first_page["total_utterances"] == 2
    assert first_page["next_offset"] == 1
    assert first_page["revision"] == 3
    assert len(first_page["speakers"]) == 2

    second_page = await get_transcript_utterances(
        public_id, limit=1, offset=first_page["next_offset"]
    )
    assert [u["id"] for u in second_page["utterances"]] == ["u-2"]
    assert second_page["next_offset"] is None
    assert second_page["revision"] == 3
    assert len(second_page["speakers"]) == 2

    # Out-of-range offset yields an empty page, not an error.
    past_end = await get_transcript_utterances(public_id, offset=10)
    assert past_end["utterances"] == []
    assert past_end["total_utterances"] == 2
    assert past_end["next_offset"] is None


@pytest.mark.anyio
async def test_get_transcript_utterances_delta_returns_tombstones(
    session_maker, test_user: User, mcp_context
):
    bind_mcp_identity(test_user)
    await seed_person(session_maker, person_id=11, name="Dana", user_id=test_user.id)
    public_id = await seed_recording_with_speakers(session_maker, user_id=test_user.id)
    await seed_canonical_transcript(session_maker)

    result = await get_transcript_utterances(public_id, after_revision=2)

    assert result["revision"] == 3
    assert result["utterances"] == []
    assert result["tombstones"] == ["u-3"]


@pytest.mark.anyio
async def test_get_transcript_utterances_current_cursor_is_empty_delta(
    session_maker, test_user: User, mcp_context
):
    bind_mcp_identity(test_user)
    await seed_person(session_maker, person_id=11, name="Dana", user_id=test_user.id)
    public_id = await seed_recording_with_speakers(session_maker, user_id=test_user.id)
    await seed_canonical_transcript(session_maker)

    result = await get_transcript_utterances(public_id, after_revision=3)

    assert result["revision"] == 3
    assert result["utterances"] == []
    assert result["tombstones"] == []


@pytest.mark.anyio
async def test_get_transcript_utterances_rejects_bad_cursor_and_foreign_recording(
    session_maker, test_user: User, mcp_context
):
    bind_mcp_identity(test_user)
    await seed_person(session_maker, person_id=11, name="Dana", user_id=999)
    public_id = await seed_recording_with_speakers(session_maker, user_id=999)
    await seed_canonical_transcript(session_maker)

    with pytest.raises(ToolError):
        await get_transcript_utterances(public_id, after_revision=-1)

    with pytest.raises(HTTPException) as exc_info:
        await get_transcript_utterances(public_id)
    assert exc_info.value.status_code == 404


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


async def seed_document(  # noqa: PLR0913 - one argument per seeded column
    session_maker,
    *,
    document_id: int,
    recording_id: int,
    title: str,
    pages: list[str],
    page_titles: list[str] | None = None,
) -> None:
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, created_at, updated_at, recording_id, title,
                    file_path, file_type, status, parse_mode, pages_parsed,
                    page_count
                ) VALUES (
                    :id, :ts, :ts, :rec, :title,
                    :path, 'text/plain', 'READY', 'VISUAL', :count, :count
                )
                """
            ),
            {
                "id": document_id,
                "ts": TEST_TIMESTAMP,
                "rec": recording_id,
                "title": title,
                "path": f"/data/documents/{document_id}.txt",
                "count": len(pages),
            },
        )
        for index, content in enumerate(pages):
            await session.execute(
                text(
                    """
                    INSERT INTO document_pages (
                        created_at, updated_at, document_id, page_number,
                        title, content, parse_mode
                    ) VALUES (
                        :ts, :ts, :doc, :page, :page_title, :content, 'STRUCTURAL'
                    )
                    """
                ),
                {
                    "ts": TEST_TIMESTAMP,
                    "doc": document_id,
                    "page": index + 1,
                    "page_title": (page_titles or [None] * len(pages))[index],
                    "content": content,
                },
            )
        await session.commit()


@pytest.mark.anyio
async def test_get_documents_assembles_text_from_pages(
    session_maker, test_user: User, mcp_context
):
    bind_mcp_identity(test_user)
    public_id = await seed_recording_with_speakers(session_maker, user_id=test_user.id)
    await seed_document(
        session_maker,
        document_id=5,
        recording_id=1,
        title="Agenda",
        pages=["Part one.", "Part two."],
        page_titles=[None, "Budget"],
    )

    docs = await get_documents(public_id)

    assert len(docs) == 1
    assert docs[0]["title"] == "Agenda"
    # Pages are labelled and joined in order. The old implementation
    # concatenated overlapping chunks, which repeated text at every boundary.
    assert docs[0]["text"] == "[Page 1]\nPart one.\n\n[Page 2: Budget]\nPart two."
    assert docs[0]["page_count"] == 2
    assert docs[0]["text_truncated"] is False


@pytest.mark.anyio
async def test_get_documents_skips_pages_with_no_content(
    session_maker, test_user: User, mcp_context
):
    """A blank slide is normal and must not leave an empty labelled section."""
    bind_mcp_identity(test_user)
    public_id = await seed_recording_with_speakers(session_maker, user_id=test_user.id)
    await seed_document(
        session_maker,
        document_id=6,
        recording_id=1,
        title="Deck",
        pages=["Real content.", "   ", "More content."],
    )

    docs = await get_documents(public_id)

    assert docs[0]["text"] == "[Page 1]\nReal content.\n\n[Page 3]\nMore content."


@pytest.mark.anyio
async def test_get_documents_truncates_long_text(
    session_maker, test_user: User, mcp_context, monkeypatch
):
    bind_mcp_identity(test_user)
    monkeypatch.setattr(mcp_server, "_DOCUMENT_TEXT_CHAR_LIMIT", 18)
    public_id = await seed_recording_with_speakers(session_maker, user_id=test_user.id)
    await seed_document(
        session_maker,
        document_id=5,
        recording_id=1,
        title="Long",
        pages=["0123456789ABCDEF"],
    )

    docs = await get_documents(public_id)

    # "[Page 1]\n" is 9 characters, so an 18-character budget keeps the label
    # and the first nine characters of the body.
    assert docs[0]["text"] == "[Page 1]\n012345678"
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

MCP_LOGGER = "backend.mcp_server.tool_logging"


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


# --- Agentic surface: management, tasks, calendar, search ---


@pytest.mark.anyio
async def test_recording_lifecycle_tools_roundtrip(
    session_maker, test_user: User, mcp_context
):
    """Rename, tag, archive, bin, and restore through the MCP tools, each
    delegating to the same endpoint coroutine the web client uses."""
    bind_mcp_identity(test_user)
    await seed_bare_recording(
        session_maker,
        recording_id=1,
        public_id="rec-1",
        user_id=test_user.id,
        state="active",
    )

    renamed = await rename_recording("rec-1", "Quarterly planning")
    assert renamed["id"] == "rec-1"
    assert renamed["name"] == "Quarterly planning"
    # Mutation responses are self-verifying: updated_at moves, the
    # transcript cursor does not.
    assert renamed["updated_at"]
    assert renamed["transcript_revision"] == 0

    tagged = await tag_recording("rec-1", "Planning")
    assert tagged["tag"]["name"] == "Planning"
    listed = await list_recordings()
    assert listed[0]["tags"] == ["Planning"]

    removed = await untag_recording("rec-1", "Planning")
    assert removed == {"recording_id": "rec-1", "removed_tag": "Planning"}

    archived = await archive_recording("rec-1")
    assert archived["is_archived"] is True
    restored = await restore_recording("rec-1")
    assert restored["is_archived"] is False

    binned = await trash_recording("rec-1")
    assert binned["is_deleted"] is True
    restored_again = await restore_recording("rec-1")
    assert restored_again["is_deleted"] is False


@pytest.mark.anyio
async def test_write_tools_reject_read_only_grant(
    session_maker, test_user: User, mcp_context
):
    """Every mutating tool refuses a grant that only carries mcp:read."""
    bind_mcp_identity(test_user, scopes=frozenset({MCP_READ_SCOPE}))
    await seed_bare_recording(
        session_maker,
        recording_id=1,
        public_id="rec-1",
        user_id=test_user.id,
        state="active",
    )

    with pytest.raises(ToolError):
        await rename_recording("rec-1", "New name")
    with pytest.raises(ToolError):
        await create_task("Follow up")
    with pytest.raises(ToolError):
        await attach_document("rec-1", "Brief", "content")


@pytest.mark.anyio
async def test_destroy_recording_requires_opt_in_scope(
    session_maker, test_user: User, mcp_context
):
    """A read+write grant cannot destroy; the destroy scope can, and the
    recording row is gone afterwards."""
    await seed_bare_recording(
        session_maker,
        recording_id=1,
        public_id="rec-1",
        user_id=test_user.id,
        state="active",
    )

    bind_mcp_identity(test_user)  # read + write, no destroy
    with pytest.raises(ToolError):
        await destroy_recording("rec-1")

    bind_mcp_identity(
        test_user,
        scopes=frozenset({MCP_READ_SCOPE, MCP_WRITE_SCOPE, MCP_DESTROY_SCOPE}),
    )
    result = await destroy_recording("rec-1")
    assert result == {"id": "rec-1", "destroyed": True}

    async with session_maker() as session:
        remaining = await session.execute(text("SELECT COUNT(*) FROM recordings"))
        assert remaining.scalar() == 0


@pytest.mark.anyio
async def test_task_tools_roundtrip(session_maker, test_user: User, mcp_context):
    bind_mcp_identity(test_user)
    await seed_bare_recording(
        session_maker,
        recording_id=1,
        public_id="rec-1",
        user_id=test_user.id,
        state="active",
    )

    created = await create_task(
        "Follow up with Dana",
        body="Send the revised deck.",
        due_at="2026-08-15T12:00:00",
        recording_ids=["rec-1"],
    )
    assert created["title"] == "Follow up with Dana"
    assert created["completed_at"] is None
    assert [linked["id"] for linked in created["recordings"]] == ["rec-1"]

    listed = await list_tasks()
    assert [task["id"] for task in listed] == [created["id"]]

    completed = await update_task(created["id"], completed=True)
    assert completed["completed_at"] is not None

    deleted = await delete_task(created["id"])
    assert deleted == {"id": created["id"], "deleted": True}
    assert await list_tasks(status="all") == []


@pytest.mark.anyio
async def test_attach_document_creates_document_and_dispatches_parse(  # noqa: PLR0913 - fixtures
    session_maker,
    test_user: User,
    mcp_context,
    stub_celery_dispatch,
    tmp_path,
    monkeypatch,
):
    import backend.api.v1.endpoints.documents as documents_module

    monkeypatch.setattr(documents_module, "DOCUMENTS_DIR", str(tmp_path))
    bind_mcp_identity(test_user)
    await seed_bare_recording(
        session_maker,
        recording_id=1,
        public_id="rec-1",
        user_id=test_user.id,
        state="active",
    )

    result = await attach_document("rec-1", "Meeting brief", "# Brief\n\nAgenda.")

    assert result["recording_id"] == "rec-1"
    assert result["title"] == "Meeting brief"
    assert result["status"] == "PENDING"
    assert [name for name, _, _ in stub_celery_dispatch] == [
        "backend.worker.tasks.process_document_task"
    ]
    async with session_maker() as session:
        row = (
            await session.execute(
                text("SELECT title, file_type, parse_mode FROM documents")
            )
        ).one()
    assert row == ("Meeting brief", "text/markdown", "STRUCTURAL")
    written = list(tmp_path.iterdir())
    assert len(written) == 1
    assert written[0].read_text().startswith("# Brief")


@pytest.mark.anyio
async def test_correct_utterance_text_stamps_mcp_source(
    session_maker, test_user: User, mcp_context, no_meeting_edge_dispatch
):
    """An MCP correction lands like a web edit but is attributed to the
    connector in the event log."""
    bind_mcp_identity(test_user)
    await seed_person(session_maker, person_id=11, name="Dana", user_id=test_user.id)
    public_id = await seed_recording_with_speakers(session_maker, user_id=test_user.id)
    await seed_canonical_transcript(session_maker)

    result = await correct_utterance_text(public_id, "u-1", "Hello, corrected.")

    assert result["recording_id"] == public_id
    assert result["utterance_id"] == "u-1"
    # The edit appends an update_text event plus a manual-lock event, so the
    # cursor advances from the seeded 3 to 5.
    assert result["revision"] == 5

    async with session_maker() as session:
        utterance = (
            await session.execute(
                text(
                    "SELECT text, manual_text_locked, revision "
                    "FROM transcript_utterances WHERE public_id = 'u-1'"
                )
            )
        ).one()
        assert utterance[0] == "Hello, corrected."
        assert bool(utterance[1]) is True
        events = (
            await session.execute(
                text(
                    "SELECT event_type, source, actor_user_id "
                    "FROM transcript_utterance_events WHERE id > 3 ORDER BY id"
                )
            )
        ).all()
    assert [event[1] for event in events] == ["mcp"] * len(events)
    assert {event[2] for event in events} == {test_user.id}


async def seed_calendar_event(
    session_maker, *, event_id: int, user_id: int, title: str
) -> None:
    """One connection, one source, one event for the given user."""
    from backend.models.calendar import (
        CalendarConnection,
        CalendarEvent,
        CalendarSource,
    )

    async with session_maker() as session:
        connection = CalendarConnection(
            id=event_id * 10,
            user_id=user_id,
            provider="google",
            provider_account_id=f"acct-{user_id}-{event_id}",
            sync_status="idle",
        )
        session.add(connection)
        await session.flush()
        source = CalendarSource(
            id=event_id * 10,
            connection_id=connection.id,
            provider_calendar_id=f"cal-{event_id}",
            name="Work",
        )
        session.add(source)
        await session.flush()
        session.add(
            CalendarEvent(
                id=event_id,
                calendar_id=source.id,
                provider_event_id=f"evt-{event_id}",
                title=title,
                starts_at=datetime(2026, 8, 5, 10, 0, 0),
                ends_at=datetime(2026, 8, 5, 11, 0, 0),
            )
        )
        await session.commit()


@pytest.mark.anyio
async def test_calendar_event_tools_scope_to_owner(
    session_maker, test_user: User, mcp_context
):
    bind_mcp_identity(test_user)
    await seed_bare_recording(
        session_maker,
        recording_id=1,
        public_id="rec-1",
        user_id=test_user.id,
        state="active",
    )
    await seed_calendar_event(
        session_maker, event_id=1, user_id=test_user.id, title="Planning sync"
    )
    await seed_calendar_event(
        session_maker, event_id=2, user_id=999, title="Someone else's standup"
    )

    events = await list_calendar_events()
    assert [event["id"] for event in events] == [1]
    assert events[0]["title"] == "Planning sync"

    linked = await link_calendar_event("rec-1", 1)
    assert linked["linked"] is True
    async with session_maker() as session:
        stored = await session.execute(
            text("SELECT calendar_event_id FROM recordings WHERE id = 1")
        )
        assert stored.scalar() == 1

    # Another user's event id must be invisible, not linkable.
    with pytest.raises(HTTPException) as exc_info:
        await link_calendar_event("rec-1", 2)
    assert exc_info.value.status_code == 404

    unlinked = await link_calendar_event("rec-1")
    assert unlinked["linked"] is False


@pytest.mark.anyio
async def test_searchable_recording_ids_scopes_to_owner(
    session_maker, test_user: User, mcp_context
):
    """The search widening subquery never leaves the caller's own,
    non-deleted recordings."""
    from backend.services.context_search import searchable_recording_ids

    await seed_bare_recording(
        session_maker,
        recording_id=1,
        public_id="rec-own",
        user_id=test_user.id,
        state="active",
    )
    await seed_bare_recording(
        session_maker,
        recording_id=2,
        public_id="rec-foreign",
        user_id=999,
        state="active",
    )
    await seed_bare_recording(
        session_maker,
        recording_id=3,
        public_id="rec-binned",
        user_id=test_user.id,
        state="deleted",
    )

    async with session_maker() as session:
        result = await session.execute(searchable_recording_ids(test_user.id))
        assert result.scalars().all() == [1]


@pytest.mark.anyio
async def test_search_context_resolves_speakers_and_provenance(
    session_maker, test_user: User, mcp_context, monkeypatch
):
    """The tool shapes chunk hits into provenance-carrying results and
    resolves diarization labels to display names."""
    import backend.core.task_dispatch as task_dispatch_module
    import backend.services.context_search as context_search_module
    from backend.models.context_chunk import ContextChunk

    bind_mcp_identity(test_user)
    await seed_person(session_maker, person_id=11, name="Dana", user_id=test_user.id)
    await seed_recording_with_speakers(session_maker, user_id=test_user.id)

    class _FakeEmbeddingTask:
        def get(self, timeout=None):
            return [[0.0] * 512]

    async def fake_dispatch(name, args=None, **kwargs):
        assert name == "backend.worker.tasks.get_text_embedding_task"
        return _FakeEmbeddingTask()

    transcript_chunk = ContextChunk(
        recording_id=1,
        content="SPEAKER_00: we agreed to ship the context layer in Q3.",
        meta={"source": "transcript", "start": 61.0, "end": 74.5},
    )
    document_chunk = ContextChunk(
        recording_id=1,
        document_id=5,
        content="Q3 roadmap: context layer, evaluation harness.",
        meta={"document_title": "Roadmap", "page_number": 2, "page_title": "Q3"},
    )

    async def fake_search(db, **kwargs):
        assert kwargs["user_id"] == test_user.id
        return [(transcript_chunk, 0.12), (document_chunk, 0.31)]

    monkeypatch.setattr(task_dispatch_module, "dispatch_task", fake_dispatch)
    monkeypatch.setattr(context_search_module, "search_context_chunks", fake_search)

    result = await search_context("what did we agree about the context layer?")

    first, second = result["results"]
    assert first["source"] == "transcript"
    assert first["content"].startswith("Dana: we agreed")
    assert first["start"] == 61.0
    assert first["recording_id"] == "11111111-2222-3333-4444-555555555555"
    assert first["distance"] == 0.12
    assert second["source"] == "document"
    assert second["document_title"] == "Roadmap"
    assert second["page_number"] == 2


@pytest.mark.anyio
async def test_rename_expected_name_guard(session_maker, test_user: User, mcp_context):
    """The optional compare-and-swap refuses a rename when the name moved
    under the caller, instead of clobbering it."""
    bind_mcp_identity(test_user)
    await seed_bare_recording(
        session_maker,
        recording_id=1,
        public_id="rec-1",
        user_id=test_user.id,
        state="active",
    )

    with pytest.raises(ToolError) as exc_info:
        await rename_recording("rec-1", "New name", expected_name="Stale name")
    assert "conflict" in str(exc_info.value).lower()

    renamed = await rename_recording("rec-1", "New name", expected_name="Recording 1")
    assert renamed["name"] == "New name"


@pytest.mark.anyio
async def test_lifecycle_payloads_are_self_verifying(
    session_maker, test_user: User, mcp_context
):
    """Archive and restore echo updated_at and the transcript cursor, so
    the write response alone proves no transcript disturbance."""
    bind_mcp_identity(test_user)
    await seed_person(session_maker, person_id=11, name="Dana", user_id=test_user.id)
    public_id = await seed_recording_with_speakers(session_maker, user_id=test_user.id)
    await seed_canonical_transcript(session_maker)

    archived = await archive_recording(public_id)
    assert archived["is_archived"] is True
    assert archived["updated_at"]
    assert archived["transcript_revision"] == 3

    restored = await restore_recording(public_id)
    assert restored["is_archived"] is False
    assert restored["transcript_revision"] == 3


@pytest.mark.anyio
async def test_list_recordings_match_field_hint(
    session_maker, test_user: User, mcp_context
):
    """With a query, each result carries the best-effort match_field hint;
    without one, the key is absent."""
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
    async with session_maker() as session:
        await session.execute(
            text(
                "INSERT INTO transcripts (id, created_at, updated_at, recording_id, "
                "text, segments, notes_status, transcript_status) VALUES "
                "(1, :ts, :ts, 1, 'we reviewed the quarterly figures', '[]', "
                "'completed', 'completed')"
            ),
            {"ts": TEST_TIMESTAMP},
        )
        # The endpoint's search matches RecordingSpeaker.name; the compact
        # payload prefers local_name, so set both to the same value.
        await session.execute(
            text("UPDATE recording_speakers SET name = 'Guest' WHERE id = 2")
        )
        await session.commit()

    by_query = {}
    for query in ("Weekly", "Dealflow", "Guest", "quarterly"):
        results = await list_recordings(query=query)
        assert [r["id"] for r in results] == [public_id], query
        by_query[query] = results[0]["match_field"]

    assert by_query == {
        "Weekly": "name",  # "Weekly sync" title
        "Dealflow": "tag",
        "Guest": "speaker",  # display name carries the searched value
        "quarterly": "content",  # only the transcript text contains it
    }

    unqueried = await list_recordings()
    assert "match_field" not in unqueried[0]
