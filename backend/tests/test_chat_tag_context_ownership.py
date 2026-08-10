"""Ownership regression test for tag-widened chat retrieval.

The chat endpoint widens RAG retrieval to recordings carrying the tag ids
the client supplies. Those ids are caller-controlled, so the widening query
must be constrained to the caller's own recordings; before the ownership
join was added, any tag id (including another user's) pulled that user's
recordings into the retrieval pool. The pgvector search itself cannot run
on SQLite, so this exercises the widening subquery directly.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.v1.endpoints.transcripts.routes_chat import (
    _tag_context_recording_ids,
)

# Register every ORM model so mappers configure via the recording/user FKs.
from backend.models import registry  # noqa: F401

_SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
    username VARCHAR NOT NULL, hashed_password VARCHAR NOT NULL, is_active BOOLEAN NOT NULL,
    is_superuser BOOLEAN NOT NULL, force_password_change BOOLEAN NOT NULL, role VARCHAR NOT NULL,
    token_version INTEGER NOT NULL, settings JSON, has_seen_demo_recording BOOLEAN NOT NULL,
    invitation_id INTEGER
);
CREATE TABLE recordings (
    max_speakers INTEGER,
    id INTEGER PRIMARY KEY, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
    name VARCHAR(255) NOT NULL, public_id VARCHAR(36) NOT NULL, meeting_uid VARCHAR(36) NOT NULL,
    audio_path VARCHAR(1024) NOT NULL, proxy_path VARCHAR(1024), celery_task_id VARCHAR(255),
    duration_seconds FLOAT, file_size_bytes INTEGER, status VARCHAR(32) NOT NULL,
    client_status VARCHAR(32), upload_progress INTEGER NOT NULL, processing_progress INTEGER NOT NULL,
    processing_step VARCHAR(255), processing_started_at TIMESTAMP, processing_completed_at TIMESTAMP,
    pipeline_generation VARCHAR(32) DEFAULT 'unified', is_archived BOOLEAN NOT NULL,
    is_deleted BOOLEAN NOT NULL, last_activity_at TIMESTAMP, user_id INTEGER, calendar_event_id INTEGER
);
CREATE TABLE tags (
    id INTEGER PRIMARY KEY, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
    name VARCHAR(255) NOT NULL, color VARCHAR(64), user_id INTEGER, parent_id INTEGER
);
CREATE TABLE recording_tags (
    id INTEGER PRIMARY KEY, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL,
    recording_id INTEGER NOT NULL, tag_id INTEGER NOT NULL
);
"""

_NOW = "2026-08-01 00:00:00"


async def _make_session_maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        for statement in _SCHEMA.split(";"):
            if statement.strip():
                await conn.execute(text(statement))
        for user_id, username in ((1, "owner"), (2, "other")):
            await conn.execute(
                text(
                    "INSERT INTO users (id, created_at, updated_at, username, "
                    "hashed_password, is_active, is_superuser, force_password_change, "
                    "role, token_version, settings, has_seen_demo_recording) "
                    "VALUES (:id, :n, :n, :username, 'x', 1, 0, 0, 'user', 0, '{}', 0)"
                ),
                {"id": user_id, "n": _NOW, "username": username},
            )
        for rec_id, user_id in ((1, 1), (2, 1), (3, 2)):
            await conn.execute(
                text(
                    "INSERT INTO recordings (id, created_at, updated_at, name, "
                    "public_id, meeting_uid, audio_path, status, upload_progress, "
                    "processing_progress, is_archived, is_deleted, user_id) "
                    "VALUES (:id, :n, :n, :name, :pid, :uid, :path, 'PROCESSED', "
                    "100, 100, 0, 0, :user_id)"
                ),
                {
                    "id": rec_id,
                    "n": _NOW,
                    "name": f"Rec {rec_id}",
                    "pid": f"p{rec_id}",
                    "uid": f"m{rec_id}",
                    "path": f"/a{rec_id}.wav",
                    "user_id": user_id,
                },
            )
        for tag_id, user_id in ((1, 1), (2, 2)):
            await conn.execute(
                text(
                    "INSERT INTO tags (id, created_at, updated_at, name, user_id) "
                    "VALUES (:id, :n, :n, :name, :user_id)"
                ),
                {"id": tag_id, "n": _NOW, "name": f"Tag {tag_id}", "user_id": user_id},
            )
        # User 1's tag on recordings 1 and 2; user 2's tag on recording 3.
        for link_id, rec_id, tag_id in ((1, 1, 1), (2, 2, 1), (3, 3, 2)):
            await conn.execute(
                text(
                    "INSERT INTO recording_tags (id, created_at, updated_at, "
                    "recording_id, tag_id) VALUES (:id, :n, :n, :rec, :tag)"
                ),
                {"id": link_id, "n": _NOW, "rec": rec_id, "tag": tag_id},
            )
    return engine, sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def test_own_tags_widen_to_own_recordings_only():
    async def _run():
        engine, maker = await _make_session_maker()
        async with maker() as session:
            result = await session.execute(_tag_context_recording_ids([1], 1))
            assert sorted(result.scalars().all()) == [1, 2]
        await engine.dispose()

    asyncio.run(_run())


def test_foreign_tag_ids_widen_to_nothing():
    async def _run():
        engine, maker = await _make_session_maker()
        async with maker() as session:
            # User 1 supplies user 2's tag id: recording 3 must not appear.
            result = await session.execute(_tag_context_recording_ids([2], 1))
            assert result.scalars().all() == []

            # Both ids at once still only reach user 1's own recordings.
            result = await session.execute(_tag_context_recording_ids([1, 2], 1))
            assert sorted(result.scalars().all()) == [1, 2]
        await engine.dispose()

    asyncio.run(_run())
