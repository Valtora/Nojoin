"""Tests for People (global speaker) deletion demotion semantics.

Deleting a person must succeed regardless of the pipeline generation of the
recordings they appear in: the global record and voiceprint go away, while
each linked recording speaker keeps the person's name as a recording-local
name (regression test for the 409 previously raised when any linked
recording was not ``unified``).
"""

from __future__ import annotations

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
from backend.tests.sqlite_schemas import (
    GLOBAL_SPEAKERS_SCHEMA,
    P_TAGS_SCHEMA,
    PEOPLE_TAGS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDINGS_SCHEMA,
)

PERSON_ID = 33
PERSON_NAME = "Anu Panesar"


def build_test_user(user_id: int = 1, username: str = "alice"):
    return SimpleNamespace(
        id=user_id,
        username=username,
        settings={},
        force_password_change=False,
    )


@pytest.fixture
async def test_session_maker() -> sessionmaker:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as connection:
        for schema in (
            RECORDINGS_SCHEMA,
            GLOBAL_SPEAKERS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            PEOPLE_TAGS_SCHEMA,
            P_TAGS_SCHEMA,
        ):
            await connection.execute(text(schema))

    try:
        yield session_maker
    finally:
        await engine.dispose()


@pytest.fixture
async def client(test_session_maker: sessionmaker) -> AsyncClient:
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def override_get_db():
        async with test_session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: build_test_user()

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as async_client:
        yield async_client

    app.dependency_overrides.clear()


async def _seed_person_with_recording(
    session_maker: sessionmaker,
    *,
    pipeline_generation: str | None,
    owner_user_id: int = 1,
) -> None:
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    pipeline_generation, is_archived, is_deleted, user_id
                ) VALUES (
                    1, :now, :now, 'Legacy meeting', 'legacy-rec', 'meeting-uid-1',
                    '/tmp/legacy.wav', 'PROCESSED', 0, 100, :pipeline_generation,
                    0, 0, :owner_user_id
                )
                """
            ),
            {
                "now": "2026-04-08 00:00:00",
                "pipeline_generation": pipeline_generation,
                "owner_user_id": owner_user_id,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO global_speakers (
                    id, created_at, updated_at, user_id, name, embedding,
                    is_voiceprint_locked
                ) VALUES (
                    :person_id, :now, :now, :owner_user_id, :person_name,
                    '[0.1, 0.2, 0.3]', 0
                )
                """
            ),
            {
                "now": "2026-04-08 00:00:00",
                "person_id": PERSON_ID,
                "person_name": PERSON_NAME,
                "owner_user_id": owner_user_id,
            },
        )
        # One speaker relying entirely on the global link for its display
        # name, one that already carries a recording-local name.
        await session.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, public_id, recording_id,
                    global_speaker_id, diarization_label, local_name, name,
                    speaker_status, speaker_kind, identity_locked
                ) VALUES
                    (1, :now, :now, 'speaker-public-1', 1, :person_id,
                     'SPEAKER_03', NULL, NULL, 'active', 'automated', 0),
                    (2, :now, :now, 'speaker-public-2', 1, :person_id,
                     'SPEAKER_05', 'Custom Anu', :person_name, 'active',
                     'automated', 0)
                """
            ),
            {
                "now": "2026-04-08 00:00:00",
                "person_id": PERSON_ID,
                "person_name": PERSON_NAME,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO p_tags (id, created_at, updated_at, name, user_id)
                VALUES (7, :now, :now, 'Clients', :owner_user_id)
                """
            ),
            {"now": "2026-04-08 00:00:00", "owner_user_id": owner_user_id},
        )
        await session.execute(
            text(
                """
                INSERT INTO people_tags (
                    id, created_at, updated_at, tag_id, global_speaker_id
                ) VALUES (1, :now, :now, 7, :person_id)
                """
            ),
            {"now": "2026-04-08 00:00:00", "person_id": PERSON_ID},
        )
        await session.commit()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "pipeline_generation",
    [None, "legacy_backfilled", "legacy_reprocess_required", "unified"],
)
async def test_delete_person_succeeds_for_any_pipeline_generation(
    client: AsyncClient,
    test_session_maker: sessionmaker,
    pipeline_generation: str | None,
) -> None:
    await _seed_person_with_recording(
        test_session_maker, pipeline_generation=pipeline_generation
    )

    response = await client.delete(f"/api/v1/speakers/{PERSON_ID}")

    assert response.status_code == 200
    assert response.json() == {"ok": True}

    async with test_session_maker() as session:
        remaining_people = (
            await session.execute(text("SELECT COUNT(*) FROM global_speakers"))
        ).scalar_one()
        remaining_tag_links = (
            await session.execute(
                text("SELECT COUNT(*) FROM people_tags WHERE global_speaker_id = :pid"),
                {"pid": PERSON_ID},
            )
        ).scalar_one()
        speakers = (
            await session.execute(
                text(
                    "SELECT id, global_speaker_id, local_name FROM recording_speakers ORDER BY id"
                )
            )
        ).all()

    assert remaining_people == 0
    assert remaining_tag_links == 0

    # Both speakers are unlinked; the one without a recording-local name
    # inherits the person's name so the meeting display does not degrade to
    # the raw diarization label, and an existing local name is untouched.
    assert [tuple(row) for row in speakers] == [
        (1, None, PERSON_NAME),
        (2, None, "Custom Anu"),
    ]


@pytest.mark.anyio
async def test_delete_person_owned_by_other_user_returns_404(
    client: AsyncClient,
    test_session_maker: sessionmaker,
) -> None:
    await _seed_person_with_recording(
        test_session_maker, pipeline_generation="unified", owner_user_id=2
    )

    response = await client.delete(f"/api/v1/speakers/{PERSON_ID}")

    assert response.status_code == 404

    async with test_session_maker() as session:
        remaining_people = (
            await session.execute(text("SELECT COUNT(*) FROM global_speakers"))
        ).scalar_one()

    assert remaining_people == 1
