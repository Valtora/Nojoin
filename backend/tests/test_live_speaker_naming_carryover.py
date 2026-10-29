"""A name given to a live speaker during capture reaches the final pass's speaker.

The live lines are ``live_naming_fixtures.LIVE_LINES``, named the way the
naming endpoint names them (``record_live_speaker_name``). The final pass then
runs ``assign_and_identify_speakers`` on its own labels, which differ from the
live ones, with the voiceprint extraction stubbed to known vectors:
``_at_cosine(c)`` sits at cosine ``c`` to ``PERSON``.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session

from backend.models.recording import Recording
from backend.tests.live_naming_fixtures import ONE, TWO, seed_live_lines
from backend.tests.sqlite_schemas import (
    GLOBAL_SPEAKERS_SCHEMA,
    P_TAGS_SCHEMA,
    PEOPLE_TAGS_SCHEMA,
    PROCESSING_RUNS_SCHEMA,
    RECORDING_SPEAKER_ALIASES_SCHEMA,
    RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDINGS_SCHEMA,
    SPEAKER_CORRECTION_EVENTS_SCHEMA,
    SPEAKER_NAME_EVENTS_SCHEMA,
    TIMELINE_NOTES_SCHEMA,
    TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
    TRANSCRIPTS_SCHEMA,
    USERS_SCHEMA,
)
from backend.utils.speaker_names.events import UserNameAct
from backend.utils.speaker_names.live import record_live_speaker_name
from backend.worker.tasks import speaker_assignment

PERSON = [1.0, 0.0]
# The final pass's own labels for the live speech: Alex's locked turn (0-6 s)
# is SPEAKER_01's, Blair's turn (6-9 s) SPEAKER_00's.
FINAL = [
    ("SPEAKER_01", 0.0, 6.0),
    ("SPEAKER_00", 6.0, 9.0),
    ("SPEAKER_01", 9.5, 20.0),
    ("SPEAKER_00", 21.0, 22.0),
    ("SPEAKER_02", 30.0, 36.0),
]


def _at_cosine(cosine: float) -> list[float]:
    return [cosine, math.sqrt(1.0 - cosine * cosine)]


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    with engine.begin() as connection:
        for schema in (
            USERS_SCHEMA,
            RECORDINGS_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            GLOBAL_SPEAKERS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA,
            RECORDING_SPEAKER_ALIASES_SCHEMA,
            PEOPLE_TAGS_SCHEMA,
            P_TAGS_SCHEMA,
            PROCESSING_RUNS_SCHEMA,
            TRANSCRIPT_UTTERANCES_SCHEMA,
            TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA,
            SPEAKER_CORRECTION_EVENTS_SCHEMA,
            SPEAKER_NAME_EVENTS_SCHEMA,
            TIMELINE_NOTES_SCHEMA,
        ):
            connection.execute(text(schema))
        connection.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    pipeline_generation, is_archived, is_deleted, user_id
                ) VALUES (1, :now, :now, 'Live meeting', 'live-rec', 'uid-live',
                          '/tmp/live.wav', 'UPLOADING', 50, 10, 'unified', 0, 0, 1)
                """
            ),
            {"now": _now()},
        )
        connection.execute(
            text(
                """
                INSERT INTO transcripts (
                    id, created_at, updated_at, recording_id, text, segments,
                    meeting_edge_status, notes_status, transcript_status
                ) VALUES (1, :now, :now, 1, '', '[]', 'idle', 'pending',
                          'processing')
                """
            ),
            {"now": _now()},
        )
    with Session(engine) as db:
        seed_live_lines(db)
        yield db
    engine.dispose()


def _add_person(db, person_id: int, name: str, embedding) -> None:
    db.execute(
        text(
            """
            INSERT INTO global_speakers (
                id, created_at, updated_at, user_id, name, embedding,
                embedding_version, is_voiceprint_locked
            ) VALUES (:id, :now, :now, 1, :name, :embedding, 2, 0)
            """
        ),
        {
            "id": person_id,
            "now": _now(),
            "name": name,
            "embedding": json.dumps(embedding),
        },
    )


def _name_live(db, label: str, name: str, person: int | None = None) -> None:
    assert record_live_speaker_name(
        db, 1, label, UserNameAct(name, person, actor_user_id=1, surface="live")
    )
    db.commit()


def _finalise(
    db,
    monkeypatch,
    voiceprints: dict[str, list[float]] | None = None,
    final: list[tuple[str, float, float]] = FINAL,
):
    monkeypatch.setattr(
        speaker_assignment,
        "_extract_speaker_voiceprints",
        lambda ctx, recording, result: {
            label: SimpleNamespace(embedding=vector, speech_seconds=30.0)
            for label, vector in (voiceprints or {}).items()
        },
    )
    monkeypatch.setattr(
        speaker_assignment, "_run_duplicate_merge_pass", lambda *args, **kwargs: None
    )
    db.execute(text("UPDATE recordings SET status = 'PROCESSING'"))
    db.commit()
    ctx = SimpleNamespace(session=db, merged_config={}, recording_id=1)
    speaker_assignment.assign_and_identify_speakers(
        ctx,
        db.get(Recording, 1),
        [
            {"start": start, "end": end, "speaker": label, "text": "words"}
            for label, start, end in final
        ],
        None,
    )
    db.commit()


def _speakers(db) -> dict[str, dict[str, Any]]:
    rows = db.execute(
        text(
            "SELECT diarization_label, name, local_name, global_speaker_id, "
            "name_source, identity_locked FROM recording_speakers"
        )
    ).all()
    return {
        row[0]: {
            "name": row[1],
            "local_name": row[2],
            "person": row[3],
            "source": row[4],
            "locked": bool(row[5]),
        }
        for row in rows
    }


def _person_embedding(db, person_id: int):
    value = db.execute(
        text("SELECT embedding FROM global_speakers WHERE id = :id"), {"id": person_id}
    ).scalar_one()
    return json.loads(value)


def test_live_names_follow_their_speech_onto_the_final_speakers(
    session, monkeypatch
) -> None:
    # SPEAKER_01 sounds exactly like Bob, but the user said it is Alex.
    _add_person(session, 5, "Bob", PERSON)
    _name_live(session, ONE, "Alex")
    _name_live(session, TWO, "Blair")

    _finalise(session, monkeypatch, {"SPEAKER_01": PERSON})

    speakers = _speakers(session)
    assert speakers["SPEAKER_01"] == {
        "name": "Alex",
        "local_name": "Alex",
        "person": None,
        "source": "user",
        "locked": True,
    }
    assert (speakers["SPEAKER_00"]["local_name"], speakers["SPEAKER_00"]["locked"]) == (
        "Blair",
        True,
    )
    # Nobody named the third voice live.
    assert speakers["SPEAKER_02"]["local_name"] is None
    assert speakers["SPEAKER_02"]["source"] is None
    # The user's name stopped identification, so Bob learned nothing.
    assert _person_embedding(session, 5) == PERSON


def test_a_live_name_follows_its_label_when_the_final_pass_misses_its_turn(
    session, monkeypatch
) -> None:
    # Named on Alex's locked turn (0-6 s); the final transcript starts at 9.5 s.
    _name_live(session, ONE, "Alex")

    _finalise(session, monkeypatch, final=FINAL[2:])

    assert _speakers(session)["SPEAKER_01"] == {
        "name": "Alex",
        "local_name": "Alex",
        "person": None,
        "source": "user",
        "locked": True,
    }


def test_only_the_newest_live_name_reaches_the_final_pass(session, monkeypatch) -> None:
    _name_live(session, ONE, "Alex")
    _name_live(session, ONE, "Alexis")

    _finalise(session, monkeypatch)

    # Not "Alex?" offered as a conflict between the two: the rename replaced it.
    assert _speakers(session)["SPEAKER_01"] == {
        "name": "Alexis",
        "local_name": "Alexis",
        "person": None,
        "source": "user",
        "locked": True,
    }


@pytest.mark.parametrize(
    ("cluster", "linked"),
    [
        pytest.param(_at_cosine(0.65), True, id="sounds-like-them"),
        pytest.param(_at_cosine(0.55), False, id="below-the-floor"),
    ],
)
def test_a_live_link_to_a_person_must_pass_the_voiceprint_floor(
    session, monkeypatch, cluster, linked
) -> None:
    _add_person(session, 7, "Dana", PERSON)
    _name_live(session, ONE, "Dana", person=7)

    _finalise(session, monkeypatch, {"SPEAKER_01": cluster})

    speaker = _speakers(session)["SPEAKER_01"]
    if linked:
        assert (speaker["person"], speaker["local_name"], speaker["locked"]) == (
            7,
            None,
            True,
        )
    else:
        # Offered as "Dana?": the name, no link, nothing locked.
        assert (speaker["person"], speaker["local_name"], speaker["locked"]) == (
            None,
            "Dana",
            False,
        )
    # A live name teaches the library nothing, at capture or at the final pass.
    assert _person_embedding(session, 7) == PERSON
