"""A user's rename, link or promote is recorded as a name event, anchored on
the speaker's longest turn, so a later final pass can carry it to whichever
speaker holds that speech.

The transcript gives SPEAKER_00 lines at 0-5 s and 5-8 s (one turn) and a
1 s line at 20 s; SPEAKER_01 holds 8-10 s between them.
"""

from __future__ import annotations

import json
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
)

pytestmark = pytest.mark.anyio

NOW = "2026-10-20 00:00:00"
REC = "names-rec"
USER = SimpleNamespace(id=1, username="alice", settings={}, force_password_change=False)
LINES = [
    ("SPEAKER_00", 0.0, 5.0),
    ("SPEAKER_00", 5.0, 8.0),
    ("SPEAKER_01", 8.0, 10.0),
    ("SPEAKER_00", 20.0, 21.0),
]


@pytest.fixture
async def session_maker():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        for schema in (
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
    app.dependency_overrides[get_current_user] = lambda: USER
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as async_client:
        yield async_client


@pytest.fixture
def legacy_writes(monkeypatch) -> None:
    from backend.api.v1.endpoints import speakers as speakers_module

    original_get = speakers_module.config_manager.get

    def _get(key, default=None):
        if key == "enable_canonical_transcript_writes":
            return False
        return original_get(key, default)

    monkeypatch.setattr(speakers_module.config_manager, "get", _get)


async def _seed(session_maker, *, people: list[dict[str, Any]] = ()) -> None:
    async with session_maker() as session:
        await session.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    pipeline_generation, is_archived, is_deleted, user_id
                ) VALUES (1, :now, :now, 'Meeting', :public_id, 'uid-1',
                          '/tmp/meeting.wav', 'PROCESSED', 100, 100, 'unified',
                          0, 0, 1)
                """
            ),
            {"now": NOW, "public_id": REC},
        )
        segments = [
            {
                "start": start,
                "end": end,
                "speaker": label,
                "text": f"line {index}",
                "segment_source": "legacy",
            }
            for index, (label, start, end) in enumerate(LINES)
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
        for person in people:
            await session.execute(
                text(
                    """
                    INSERT INTO global_speakers (
                        id, created_at, updated_at, user_id, name,
                        is_voiceprint_locked
                    ) VALUES (:id, :now, :now, 1, :name, 0)
                    """
                ),
                {"now": NOW, **person},
            )
        for index, label in enumerate(("SPEAKER_00", "SPEAKER_01"), start=1):
            await session.execute(
                text(
                    """
                    INSERT INTO recording_speakers (
                        id, created_at, updated_at, public_id, recording_id,
                        diarization_label, name, speaker_status, speaker_kind,
                        identity_locked
                    ) VALUES (:id, :now, :now, :public_id, 1, :label, :name,
                              'active', 'automated', 0)
                    """
                ),
                {
                    "now": NOW,
                    "id": index,
                    "public_id": f"rs-{index}",
                    "label": label,
                    "name": f"Speaker {index}",
                },
            )
        await session.commit()


async def _events(session_maker) -> list[dict[str, Any]]:
    async with session_maker() as session:
        rows = await session.execute(
            text(
                "SELECT id, kind, name, global_speaker_id, source, authority, "
                "surface, anchor_start_ms, anchor_end_ms, anchor_label, "
                "recording_speaker_id, supersedes_event_id, created_by_user_id "
                "FROM speaker_name_events ORDER BY id"
            )
        )
        return [dict(row._mapping) for row in rows.all()]


async def _name_source(session_maker, speaker_id: int) -> tuple[Any, Any]:
    async with session_maker() as session:
        row = (
            await session.execute(
                text(
                    "SELECT name_source, identity_locked FROM recording_speakers "
                    "WHERE id = :id"
                ),
                {"id": speaker_id},
            )
        ).one()
    return row[0], bool(row[1])


async def _rename(client, label: str, name: str):
    response = await client.put(
        f"/api/v1/speakers/recordings/{REC}",
        json={"diarization_label": label, "global_speaker_name": name},
    )
    assert response.status_code == 200, response.text
    return response


async def test_a_rename_is_recorded_on_the_speakers_longest_turn(
    client, session_maker
) -> None:
    await _seed(session_maker)

    response = await _rename(client, "SPEAKER_00", "Alex")

    assert await _events(session_maker) == [
        {
            "id": 1,
            "kind": "name",
            "name": "Alex",
            "global_speaker_id": None,
            "source": "user",
            "authority": 40,
            "surface": "api",
            "anchor_start_ms": 0,
            "anchor_end_ms": 8000,
            "anchor_label": "SPEAKER_00",
            "recording_speaker_id": 1,
            "supersedes_event_id": None,
            "created_by_user_id": 1,
        }
    ]
    assert await _name_source(session_maker, 1) == ("user", True)
    assert response.json()[0]["name_source"] == "user"


async def test_a_second_rename_supersedes_the_first(client, session_maker) -> None:
    await _seed(session_maker)

    await _rename(client, "SPEAKER_00", "Alx")
    await _rename(client, "SPEAKER_00", "Alex")
    await _rename(client, "SPEAKER_01", "Sam")
    await _rename(client, "SPEAKER_00", "Alexander")

    events = await _events(session_maker)
    assert [(e["id"], e["name"], e["supersedes_event_id"]) for e in events] == [
        (1, "Alx", None),
        (2, "Alex", 1),
        (3, "Sam", None),
        (4, "Alexander", 2),
    ]
    assert (events[2]["anchor_start_ms"], events[2]["anchor_end_ms"]) == (8000, 10000)


async def test_a_link_and_a_promote_record_the_person(client, session_maker) -> None:
    await _seed(session_maker, people=[{"id": 7, "name": "Dana"}])

    await _rename(client, "SPEAKER_00", "Dana")
    await _rename(client, "SPEAKER_01", "Sam")
    response = await client.post(
        f"/api/v1/speakers/recordings/{REC}/speakers/SPEAKER_01/promote"
    )
    assert response.status_code == 200, response.text

    events = await _events(session_maker)
    promoted_person = response.json()["global_speaker_id"]
    assert [(e["name"], e["global_speaker_id"]) for e in events] == [
        ("Dana", 7),
        ("Sam", None),
        ("Sam", promoted_person),
    ]
    assert events[2]["supersedes_event_id"] == events[1]["id"]
    assert await _name_source(session_maker, 2) == ("user", True)


async def test_an_assistants_rename_is_recorded_as_from_mcp(session_maker) -> None:
    from backend.api.v1.endpoints.speakers.helpers import SpeakerUpdate
    from backend.api.v1.endpoints.speakers.routes_recording import (
        apply_recording_speaker_update,
    )

    await _seed(session_maker)
    async with session_maker() as db:
        await apply_recording_speaker_update(
            REC,
            SpeakerUpdate(diarization_label="SPEAKER_01", global_speaker_name="Sam"),
            db=db,
            current_user=USER,
            source="mcp",
        )

    events = await _events(session_maker)
    assert [(e["name"], e["surface"], e["source"]) for e in events] == [
        ("Sam", "mcp", "user")
    ]


async def test_a_rename_on_the_legacy_path_is_still_the_users(
    client, session_maker, legacy_writes
) -> None:
    await _seed(session_maker)

    await _rename(client, "SPEAKER_00", "Alex")

    # No canonical lines to anchor on, so no row; the name is still the user's.
    assert await _events(session_maker) == []
    assert await _name_source(session_maker, 1) == ("user", True)
