"""Two saves of a meeting's notes from the same base, on a real PostgreSQL.

A shared meeting's notes can be saved by several people at once. Each save
carries the revision its text was edited from, and ``update_notes`` locks the
transcript row while it compares. A save that arrives while another holds the
row waits for it, then sees the other save's notes and is refused with 409,
rather than overwriting them. SQLite has no row locks, so this needs Postgres;
skips unless NOJOIN_TEST_POSTGRES_URL names a server.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import psycopg
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.api.v1.endpoints.transcripts.routes_notes import NotesUpdate, update_notes
from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.tests.postgres_setup import migrate_empty_database
from backend.tests.test_timeline_notes_migration_postgres import _psycopg_url
from backend.utils.notes_revision import NOTES_CONFLICT_CODE, notes_revision

OWNER_ID = 1
RECORDING_ID = 10
PUBLIC_ID = "rec-10"
BASE_NOTES = "## Notes\n\n- First point"


@pytest.fixture
def database(postgres_test_url: str) -> Iterator[str]:
    """A database of its own, migrated to head."""
    admin_url = _psycopg_url(postgres_test_url)
    name = f"nojoin_notes_saves_{uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    try:
        migrate_empty_database(url, "head")
        yield url
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
async def maker(database: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        make_url(database).set(drivername="postgresql+asyncpg")
    )
    try:
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _seed(maker: async_sessionmaker[AsyncSession]) -> User:
    async with maker() as session:
        owner = User(id=OWNER_ID, username="mira", hashed_password="x", role="owner")
        session.add(owner)
        await session.flush()
        session.add(
            Recording(
                id=RECORDING_ID,
                name="Weekly sync",
                public_id=PUBLIC_ID,
                meeting_uid="m-10",
                audio_path="/data/10.wav",
                status=RecordingStatus.PROCESSED,
                user_id=OWNER_ID,
            )
        )
        await session.flush()
        session.add(
            Transcript(
                recording_id=RECORDING_ID,
                notes=BASE_NOTES,
                transcript_status="completed",
            )
        )
        await session.commit()
        return owner


@pytest.mark.anyio
async def test_a_save_waiting_on_another_is_refused_once_that_one_lands(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    owner = await _seed(maker)
    base = notes_revision(BASE_NOTES)

    # Bob's save is under way: it holds the row, from the same base.
    async with maker() as bob:
        transcript = (
            await bob.execute(
                select(Transcript)
                .where(Transcript.recording_id == RECORDING_ID)
                .with_for_update()
            )
        ).scalar_one()

        async def alice_saves() -> object:
            async with maker() as session:
                return await update_notes(
                    PUBLIC_ID,
                    NotesUpdate(notes="Alice's notes", base_revision=base),
                    db=session,
                    current_user=owner,
                )

        alice = asyncio.create_task(alice_saves())
        await asyncio.sleep(0.5)
        # Alice's save waits on the row rather than reading the old notes.
        assert not alice.done()

        transcript.notes = "Bob's notes"
        await bob.commit()

    with pytest.raises(HTTPException) as refused:
        await alice
    assert refused.value.status_code == 409
    assert refused.value.detail["code"] == NOTES_CONFLICT_CODE
    assert refused.value.detail["notes"] == "Bob's notes"

    async with maker() as session:
        stored = (
            await session.execute(
                select(Transcript.notes).where(Transcript.recording_id == RECORDING_ID)
            )
        ).scalar_one()
    assert stored == "Bob's notes"
