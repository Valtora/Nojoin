"""People are scoped to their owner.

The voiceprint dialog matches a speaker against the signed-in user's own
people, links it only to one of them, and a person it creates is theirs. The
demo meeting's people belong to the user it is seeded for. Alice (user 1) owns
Dana; Bob (user 2) owns Robin, whose voiceprint is the extracted voice itself.
"""

from __future__ import annotations

import json
import math
from collections import Counter, deque
from types import SimpleNamespace
from typing import Any

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
    DIARIZATION_WINDOW_RESULTS_SCHEMA,
    DIARIZATION_WINDOW_TURNS_SCHEMA,
    GLOBAL_SPEAKERS_SCHEMA,
    P_TAGS_SCHEMA,
    PEOPLE_TAGS_SCHEMA,
    PROCESSING_RUNS_SCHEMA,
    RECORDING_AUDIO_WINDOW_MANIFESTS_SCHEMA,
    RECORDING_SPEAKER_ALIASES_SCHEMA,
    RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDINGS_SCHEMA,
    SPEAKER_CORRECTION_EVENTS_SCHEMA,
    SPEAKER_NAME_EVENTS_SCHEMA,
    TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
    TRANSCRIPTS_SCHEMA,
    USERS_SCHEMA,
)

NOW = "2026-10-22 00:00:00"
REC = "scope-rec"
DANA = [1.0, 0.0]
# The extracted voice: 0.8 from Dana, and Robin's voiceprint exactly.
VOICE = [0.8, math.sqrt(1.0 - 0.8 * 0.8)]
APPLY = f"/api/v1/speakers/recordings/{REC}/speakers/SPEAKER_00/voiceprint/apply"
CHAT_MESSAGES_SCHEMA = """
CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY, created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL, recording_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL, role VARCHAR NOT NULL, content TEXT NOT NULL
)
"""


@pytest.fixture
async def session_maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        for schema in (
            USERS_SCHEMA,
            RECORDINGS_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            GLOBAL_SPEAKERS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA,
            PROCESSING_RUNS_SCHEMA,
            RECORDING_AUDIO_WINDOW_MANIFESTS_SCHEMA,
            DIARIZATION_WINDOW_RESULTS_SCHEMA,
            DIARIZATION_WINDOW_TURNS_SCHEMA,
            TRANSCRIPT_UTTERANCES_SCHEMA,
            TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA,
            RECORDING_SPEAKER_ALIASES_SCHEMA,
            SPEAKER_CORRECTION_EVENTS_SCHEMA,
            SPEAKER_NAME_EVENTS_SCHEMA,
            PEOPLE_TAGS_SCHEMA,
            P_TAGS_SCHEMA,
            CHAT_MESSAGES_SCHEMA,
        ):
            await connection.execute(text(schema))
    try:
        yield sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def client(session_maker, stub_meeting_edge_dispatch):
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")

    async def override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1, username="alice", settings={}, force_password_change=False
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as async_client:
        yield async_client


@pytest.fixture
def extraction(monkeypatch) -> deque:
    """Queue the voiceprints the worker hands back, one per extraction call."""
    from backend.api.v1.endpoints import speakers as speakers_module

    results: deque = deque()

    def _send_task(name, args=None, kwargs=None, **options):
        result = results.popleft()

        class _Result:
            def get(self, timeout=None):
                return result

        return _Result()

    monkeypatch.setattr(speakers_module.celery_app, "send_task", _send_task)
    return results


async def _seed(session_maker, *, speaker_voiceprint: list[float] | None) -> None:
    """Alice's meeting with one speaker, her Dana, and Bob's Robin."""
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    pipeline_generation, is_archived, is_deleted, user_id
                ) VALUES (
                    1, :now, :now, 'Meeting', :public_id, 'uid-1',
                    '/tmp/meeting.wav', 'PROCESSED', 100, 100, 'unified', 0, 0, 1
                )
                """
            ),
            {"now": NOW, "public_id": REC},
        )
        segments = [
            {
                "start": 0.0,
                "end": 30.0,
                "speaker": "SPEAKER_00",
                "text": "line",
                "segment_source": "legacy",
            }
        ]
        await session.execute(
            text(
                """
                INSERT INTO transcripts (
                    id, created_at, updated_at, recording_id, text, segments,
                    meeting_edge_status, notes_status, transcript_status
                ) VALUES (1, :now, :now, 1, 'text', :segments, 'idle',
                          'pending', 'completed')
                """
            ),
            {"now": NOW, "segments": json.dumps(segments)},
        )
        for person_id, owner, name, voiceprint in (
            (7, 1, "Dana", DANA),
            (8, 2, "Robin", VOICE),
        ):
            await session.execute(
                text(
                    """
                    INSERT INTO global_speakers (
                        id, created_at, updated_at, user_id, name, embedding,
                        embedding_version, is_voiceprint_locked
                    ) VALUES (:id, :now, :now, :owner, :name, :embedding, 2, 0)
                    """
                ),
                {
                    "id": person_id,
                    "now": NOW,
                    "owner": owner,
                    "name": name,
                    "embedding": json.dumps(voiceprint),
                },
            )
        await session.execute(
            text(
                """
                INSERT INTO recording_speakers (
                    id, created_at, updated_at, public_id, recording_id,
                    diarization_label, name, embedding, embedding_version,
                    embedding_speech_seconds, speaker_status, speaker_kind,
                    identity_locked
                ) VALUES (1, :now, :now, 'rs-1', 1, 'SPEAKER_00', 'Speaker 1',
                          :embedding, 2, 25.0, 'active', 'automated', 0)
                """
            ),
            {
                "now": NOW,
                "embedding": (
                    json.dumps(speaker_voiceprint) if speaker_voiceprint else None
                ),
            },
        )
        await session.commit()


async def _people(session_maker) -> dict[int, tuple[Any, str, Any]]:
    """Every person by id: owner, name and voiceprint."""
    async with session_maker() as session:
        rows = await session.execute(
            text("SELECT id, user_id, name, embedding FROM global_speakers")
        )
        return {
            person_id: (owner, name, json.loads(embedding) if embedding else None)
            for person_id, owner, name, embedding in rows.all()
        }


async def _linked_person(session_maker) -> Any:
    async with session_maker() as session:
        return (
            await session.execute(
                text("SELECT global_speaker_id FROM recording_speakers WHERE id = 1")
            )
        ).scalar_one()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "endpoint", ["speakers/SPEAKER_00/voiceprint/extract", "voiceprints/extract-all"]
)
async def test_an_extracted_voiceprint_is_matched_against_the_users_own_people(
    client, session_maker, extraction, endpoint
) -> None:
    """Robin is Bob's, so Alice's speaker is matched to her Dana at 0.8 even
    though Robin's voiceprint is the voice itself, and the people offered for
    a link are hers alone."""
    await _seed(session_maker, speaker_voiceprint=None)
    extraction.append({"embedding": VOICE, "speech_seconds": 25.0})

    response = await client.post(f"/api/v1/speakers/recordings/{REC}/{endpoint}")

    assert response.status_code == 200
    body = response.json()
    matched = (
        body["matched_speaker"]
        if "matched_speaker" in body
        else body["results"][0]["matched_speaker"]
    )
    assert (matched["id"], matched["name"]) == (7, "Dana")
    assert matched["similarity_score"] == pytest.approx(0.8)
    assert [person["id"] for person in body["all_global_speakers"]] == [7]


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["link_existing", "force_link"])
async def test_a_speaker_links_only_to_a_person_of_the_users_own(
    client, session_maker, action
) -> None:
    """Linking Alice's speaker to Bob's Robin answers exactly as linking it to
    a person that does not exist, and changes neither the speaker nor Robin."""
    await _seed(session_maker, speaker_voiceprint=VOICE)
    before = await _people(session_maker)

    someone_elses = await client.post(
        APPLY, json={"action": action, "global_speaker_id": 8}
    )
    missing = await client.post(
        APPLY, json={"action": action, "global_speaker_id": 999}
    )

    assert someone_elses.status_code == missing.status_code == 404
    assert someone_elses.json() == missing.json()
    assert await _linked_person(session_maker) is None
    assert await _people(session_maker) == before

    own = await client.post(APPLY, json={"action": action, "global_speaker_id": 7})

    assert own.status_code == 200
    assert await _linked_person(session_maker) == 7


@pytest.mark.anyio
async def test_a_person_created_from_the_dialog_belongs_to_the_user(
    client, session_maker
) -> None:
    """Bob's Robin does not stop Alice creating her own Robin, who is hers and
    is listed in her People library; Bob's Robin is unchanged."""
    await _seed(session_maker, speaker_voiceprint=VOICE)

    response = await client.post(
        APPLY, json={"action": "create_new", "new_speaker_name": "Robin"}
    )

    assert response.status_code == 200
    created = response.json()["matched_speaker"]["id"]
    people = await _people(session_maker)
    assert people[created] == (1, "Robin", pytest.approx(VOICE))
    assert people[8] == (2, "Robin", pytest.approx(VOICE))
    assert await _linked_person(session_maker) == created
    listed = await client.get("/api/v1/speakers/")
    assert sorted(person["id"] for person in listed.json()) == sorted([7, created])


@pytest.mark.anyio
async def test_creating_a_person_refuses_a_name_already_in_the_users_library(
    client, session_maker
) -> None:
    await _seed(session_maker, speaker_voiceprint=VOICE)

    response = await client.post(
        APPLY, json={"action": "create_new", "new_speaker_name": "Dana"}
    )

    assert response.status_code == 400
    assert sorted(await _people(session_maker)) == [7, 8]
    assert await _linked_person(session_maker) is None


@pytest.mark.anyio
async def test_the_demo_meetings_people_belong_to_the_user_it_is_seeded_for(
    session_maker, monkeypatch, tmp_path
) -> None:
    """Alice already has the demo's host from an earlier seed, so the seed
    links to her rather than making a second person of the same name."""
    import backend.seed_demo as seed_demo

    monkeypatch.setattr(seed_demo, "async_session_maker", session_maker)
    monkeypatch.setattr(
        seed_demo,
        "PathManager",
        lambda: SimpleNamespace(recordings_directory=str(tmp_path)),
    )
    async with session_maker() as session:
        for user_id, username in ((1, "alice"), (2, "bob")):
            await session.execute(
                text(
                    """
                    INSERT INTO users (
                        id, created_at, updated_at, username, hashed_password,
                        is_active, is_superuser, force_password_change, role,
                        token_version, settings, has_seen_demo_recording
                    ) VALUES (:id, :now, :now, :username, 'x', 1, 0, 0, 'user',
                              0, '{}', 0)
                    """
                ),
                {"id": user_id, "now": NOW, "username": username},
            )
        await session.execute(
            text(
                """
                INSERT INTO global_speakers (
                    id, created_at, updated_at, user_id, name, is_voiceprint_locked
                ) VALUES (40, :now, :now, 1, 'Alice (Host)', 0)
                """
            ),
            {"now": NOW},
        )
        await session.commit()

    await seed_demo.seed_demo_data(user_id=1)
    await seed_demo.seed_demo_data(user_id=2)

    async with session_maker() as session:
        people = (
            await session.execute(
                text("SELECT user_id, name FROM global_speakers ORDER BY user_id, id")
            )
        ).all()
        links = (
            await session.execute(
                text(
                    "SELECT r.user_id, gs.user_id FROM recording_speakers rs"
                    " JOIN recordings r ON r.id = rs.recording_id"
                    " JOIN global_speakers gs ON gs.id = rs.global_speaker_id"
                )
            )
        ).all()
        alice_host = (
            await session.execute(
                text(
                    "SELECT count(*) FROM recording_speakers rs"
                    " JOIN recordings r ON r.id = rs.recording_id"
                    " WHERE r.user_id = 1 AND rs.global_speaker_id = 40"
                )
            )
        ).scalar_one()

    names = ["Alice (Host)", "Bob (Product)", "Charlie (Eng)", "Dana (Design)"]
    assert Counter(map(tuple, people)) == Counter(
        (user_id, name) for user_id in (1, 2) for name in names
    )
    assert len(links) == 8
    assert all(recording_owner == owner for recording_owner, owner in links)
    assert alice_host == 1
