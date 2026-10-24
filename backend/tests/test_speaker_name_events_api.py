"""A user's rename, link or promote is recorded as a name event, anchored on
the speaker's longest turn, so a later final pass can carry it to whichever
speaker holds that speech. Confirm and Not {name} answer a pending name the
same way.

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


# Confirm and Not {name} on a pending name.


async def _offer(session_maker, speaker_id: int, name: str, **fields: Any) -> None:
    """Give a speaker a pending name, as inference leaves it, and its suggestion."""
    label = f"SPEAKER_{speaker_id - 1:02d}"
    async with session_maker() as session:
        await session.execute(
            text(
                "UPDATE recording_speakers SET local_name = :local, name = NULL, "
                "name_source = :source, global_speaker_id = :person, "
                "identity_confidence = 0.8 WHERE id = :id"
            ),
            {
                "id": speaker_id,
                "local": None if fields.get("person") else name,
                "source": fields.get("source", "llm"),
                "person": fields.get("person"),
            },
        )
        suggestion = {
            "diarization_label": label,
            "suggested_name": name,
            "status": "pending",
            "resolution_reason": None,
            "resolution_actor_user_id": None,
        }
        await session.execute(
            text(
                "UPDATE transcripts SET speaker_name_suggestions = :suggestions "
                "WHERE recording_id = 1"
            ),
            {"suggestions": json.dumps([suggestion])},
        )
        await session.commit()


async def _suggestions(session_maker) -> list[dict[str, Any]]:
    async with session_maker() as session:
        raw = (
            await session.execute(
                text("SELECT speaker_name_suggestions FROM transcripts")
            )
        ).scalar_one()
    return json.loads(raw) if isinstance(raw, str) else raw


async def _review(client, label: str, answer: str):
    return await client.post(
        f"/api/v1/speakers/recordings/{REC}/speakers/{label}/suggestions/{answer}"
    )


async def test_confirm_makes_a_suggested_name_the_users(client, session_maker) -> None:
    await _seed(session_maker, people=[{"id": 7, "name": "Alex"}])
    await _offer(session_maker, 1, "Alex")

    response = await _review(client, "SPEAKER_00", "accept")

    assert response.status_code == 200, response.text
    body = response.json()[0]
    assert (body["global_speaker_id"], body["name_source"], body["name_pending"]) == (
        7,
        "user",
        False,
    )
    assert await _name_source(session_maker, 1) == ("user", True)
    events = await _events(session_maker)
    assert [
        (e["kind"], e["name"], e["global_speaker_id"], e["source"]) for e in events
    ] == [("name", "Alex", 7, "user")]
    suggestion = (await _suggestions(session_maker))[0]
    assert (suggestion["status"], suggestion["resolution_actor_user_id"]) == (
        "accepted",
        1,
    )


async def test_not_name_puts_back_speaker_n_and_drops_a_voice_match(
    client, session_maker
) -> None:
    await _seed(session_maker, people=[{"id": 8, "name": "Bob"}])
    await _offer(session_maker, 2, "Bob", source="voiceprint", person=8)

    response = await _review(client, "SPEAKER_01", "reject")

    assert response.status_code == 200, response.text
    body = response.json()[0]
    assert (body["name"], body["global_speaker_id"], body["name_pending"]) == (
        "Speaker 2",
        None,
        False,
    )
    assert await _name_source(session_maker, 2) == (None, False)
    events = await _events(session_maker)
    assert [
        (e["kind"], e["name"], e["global_speaker_id"], e["source"], e["anchor_end_ms"])
        for e in events
    ] == [("dismiss", "Bob", 8, "user", 10000)]
    assert (await _suggestions(session_maker))[0]["status"] == "rejected"


@pytest.mark.parametrize("answer", ["accept", "reject"])
async def test_only_a_pending_name_can_be_answered(
    client, session_maker, answer
) -> None:
    await _seed(session_maker)
    await _rename(client, "SPEAKER_00", "Alex")
    unnamed = await _review(client, "SPEAKER_01", answer)
    confirmed = await _review(client, "SPEAKER_00", answer)
    unknown = await _review(client, "SPEAKER_09", answer)
    # A speaker merged into another is answered through the one it joined.
    await _offer(session_maker, 2, "Sam")
    async with session_maker() as session:
        await session.execute(
            text("UPDATE recording_speakers SET merged_into_id = 1 WHERE id = 2")
        )
        await session.commit()
    merged = await _review(client, "SPEAKER_01", answer)

    assert [r.status_code for r in (unnamed, confirmed, unknown, merged)] == [
        409,
        409,
        404,
        404,
    ]
    assert len(await _events(session_maker)) == 1


async def _speaker(session_maker, speaker_id: int) -> dict[str, Any]:
    async with session_maker() as session:
        row = (
            await session.execute(
                text(
                    "SELECT global_speaker_id, local_name, name, name_source, "
                    "identity_locked, identity_confidence FROM recording_speakers "
                    "WHERE id = :id"
                ),
                {"id": speaker_id},
            )
        ).one()
    values = dict(row._mapping)
    values["identity_locked"] = bool(values["identity_locked"])
    return values


async def _set(session_maker, sql: str, **params: Any) -> None:
    async with session_maker() as session:
        await session.execute(text(sql), params)
        await session.commit()


def _named(events: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return [(e["kind"], e["name"], e["global_speaker_id"], e["source"]) for e in events]


# A link the user makes outside a rename is the user's too. Left with the
# inferred name's source and no lock, it would read as pending: Retry Speaker
# Inference would replace the name and drop the link, and Confirm would rename
# the speaker back to the inferred name.


@pytest.mark.parametrize(
    "action",
    [
        {"action": "link_existing", "global_speaker_id": 8},
        {"action": "force_link", "global_speaker_id": 8},
        {"action": "create_new", "new_speaker_name": "Bob"},
    ],
)
async def test_the_voiceprint_dialogs_link_is_the_users(
    client, session_maker, action
) -> None:
    people = [{"id": 8, "name": "Bob"}] if "global_speaker_id" in action else []
    await _seed(session_maker, people=people)
    # A rename of the other speaker gives the recording its canonical lines,
    # which a processed meeting has, for the link's row to be anchored on.
    await _rename(client, "SPEAKER_01", "Sam")
    await _offer(session_maker, 1, "Alex")
    await _set(
        session_maker,
        "UPDATE recording_speakers SET embedding = '[1.0, 0.0]' WHERE id = 1",
    )

    response = await client.post(
        f"/api/v1/speakers/recordings/{REC}/speakers/SPEAKER_00/voiceprint/apply",
        json=action,
    )

    assert response.status_code == 200, response.text
    person = response.json()["matched_speaker"]["id"]
    speaker = await _speaker(session_maker, 1)
    assert (
        speaker["global_speaker_id"],
        speaker["local_name"],
        speaker["name_source"],
        speaker["identity_locked"],
    ) == (person, None, "user", True)
    events = [e for e in await _events(session_maker) if e["recording_speaker_id"] == 1]
    assert _named(events) == [("name", "Bob", person, "user")]
    assert (events[0]["anchor_start_ms"], events[0]["anchor_end_ms"]) == (0, 8000)
    # Nothing is pending, so Confirm can't rename the speaker back to "Alex".
    assert (await _review(client, "SPEAKER_00", "accept")).status_code == 409
    assert (await _speaker(session_maker, 1))["global_speaker_id"] == person


async def test_a_line_moved_to_a_person_adopts_their_speaker_as_the_users(
    client, session_maker, legacy_writes, stub_celery_dispatch
) -> None:
    await _seed(session_maker, people=[{"id": 7, "name": "Alex"}])
    await _offer(session_maker, 1, "Alex")

    # SPEAKER_01's line, moved to the person Alex: SPEAKER_00 already reads
    # "Alex", so it becomes theirs.
    response = await client.put(
        f"/api/v1/transcripts/{REC}/segments/2",
        json={"new_speaker_name": "Alex", "global_speaker_id": 7},
    )

    assert response.status_code == 200, response.text
    speaker = await _speaker(session_maker, 1)
    assert (
        speaker["global_speaker_id"],
        speaker["local_name"],
        speaker["name_source"],
        speaker["identity_locked"],
    ) == (7, None, "user", True)


async def test_add_to_people_on_the_legacy_path_is_the_users(
    client, session_maker, legacy_writes
) -> None:
    await _seed(session_maker)
    await _offer(session_maker, 2, "Sam", source="heuristic")

    response = await client.post(
        f"/api/v1/speakers/recordings/{REC}/speakers/SPEAKER_01/promote"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["name_source"], body["name_pending"]) == ("user", False)
    speaker = await _speaker(session_maker, 2)
    assert (speaker["global_speaker_id"], speaker["identity_locked"]) == (
        body["global_speaker_id"],
        True,
    )


async def test_merging_people_moves_a_voice_match_but_leaves_it_pending(
    client, session_maker
) -> None:
    await _seed(
        session_maker, people=[{"id": 7, "name": "Bob"}, {"id": 8, "name": "Robert"}]
    )
    await _offer(session_maker, 2, "Bob", source="voiceprint", person=7)

    response = await client.post(
        "/api/v1/speakers/merge", json={"source_speaker_id": 7, "target_speaker_id": 8}
    )

    assert response.status_code == 200, response.text
    speaker = await _speaker(session_maker, 2)
    assert (
        speaker["global_speaker_id"],
        speaker["name_source"],
        speaker["identity_locked"],
        speaker["identity_confidence"],
    ) == (8, "voiceprint", False, 0.8)
    # Merging people names nobody: no user row for the final pass to carry.
    assert await _events(session_maker) == []


async def test_scan_for_matches_links_as_a_voice_match_the_user_may_still_refuse(
    client, session_maker
) -> None:
    await _seed(session_maker, people=[{"id": 8, "name": "Bob"}])
    await _set(
        session_maker,
        "UPDATE global_speakers SET embedding = '[1.0, 0.0]' WHERE id = 8",
    )
    # Speaker 1 has a pending AI name, which a voice match outranks; speaker 2
    # was dismissed as Bob before.
    await _offer(session_maker, 1, "Alex")
    await _set(
        session_maker,
        "UPDATE recording_speakers SET embedding = '[0.96, 0.28]' WHERE id IN (1, 2)",
    )
    await _set(
        session_maker,
        "INSERT INTO speaker_name_events (id, created_at, updated_at, public_id, "
        "recording_id, kind, name, source, authority, anchor_start_ms, "
        "anchor_end_ms, recording_speaker_id) VALUES (1, :now, :now, 'ev-1', 1, "
        "'dismiss', 'bob', 'user', 40, 8000, 10000, 2)",
        now=NOW,
    )

    response = await client.post("/api/v1/speakers/8/scan-matches")

    assert response.status_code == 200, response.text
    assert response.json()["matches_found"] == 1
    linked = await _speaker(session_maker, 1)
    assert (
        linked["global_speaker_id"],
        linked["local_name"],
        linked["name_source"],
        linked["identity_locked"],
    ) == (8, None, "voiceprint", False)
    assert linked["identity_confidence"] == pytest.approx(0.96)
    assert (await _speaker(session_maker, 2))["global_speaker_id"] is None


async def test_confirm_keeps_a_voice_match_and_makes_it_the_users(
    client, session_maker
) -> None:
    await _seed(session_maker, people=[{"id": 8, "name": "Bob"}])
    await _offer(session_maker, 2, "Bob", source="voiceprint", person=8)

    response = await _review(client, "SPEAKER_01", "accept")

    assert response.status_code == 200, response.text
    speaker = await _speaker(session_maker, 2)
    assert (
        speaker["global_speaker_id"],
        speaker["name_source"],
        speaker["identity_locked"],
    ) == (8, "user", True)
    assert _named(await _events(session_maker)) == [("name", "Bob", 8, "user")]


@pytest.mark.parametrize("answer", ["accept", "reject"])
async def test_another_users_recording_cannot_be_answered(
    client, session_maker, answer
) -> None:
    await _seed(session_maker)
    await _offer(session_maker, 1, "Alex")
    await _set(session_maker, "UPDATE recordings SET user_id = 2")

    response = await _review(client, "SPEAKER_00", answer)

    assert response.status_code == 404
    speaker = await _speaker(session_maker, 1)
    assert (speaker["local_name"], speaker["name_source"]) == ("Alex", "llm")
    assert await _events(session_maker) == []


async def test_not_name_gives_back_the_speakers_own_number(
    client, session_maker
) -> None:
    await _seed(session_maker)
    await _offer(session_maker, 1, "Alex")

    response = await _review(client, "SPEAKER_00", "reject")

    assert response.status_code == 200, response.text
    # Speaker 2 keeps "Speaker 2"; SPEAKER_00 is "Speaker 1" again, not the
    # next free number.
    assert response.json()[0]["name"] == "Speaker 1"
