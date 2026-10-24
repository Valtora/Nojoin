"""The final pass carries a user's name to whichever speaker holds its span.

Each case runs ``assign_and_identify_speakers`` on a real ORM session, with the
voiceprint extraction stubbed to known vectors: ``_at_cosine(c)`` sits at
cosine ``c`` to ``PERSON``. The floor (0.60) is written as a literal.
"""

from __future__ import annotations

import copy
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
from backend.tests.sqlite_schemas import (
    GLOBAL_SPEAKERS_SCHEMA,
    P_TAGS_SCHEMA,
    PEOPLE_TAGS_SCHEMA,
    RECORDING_SPEAKER_ALIASES_SCHEMA,
    RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDINGS_SCHEMA,
    SPEAKER_NAME_EVENTS_SCHEMA,
)
from backend.worker.tasks import speaker_assignment

PERSON = [1.0, 0.0]


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
            RECORDINGS_SCHEMA,
            GLOBAL_SPEAKERS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA,
            RECORDING_SPEAKER_ALIASES_SCHEMA,
            PEOPLE_TAGS_SCHEMA,
            P_TAGS_SCHEMA,
            SPEAKER_NAME_EVENTS_SCHEMA,
        ):
            connection.execute(text(schema))
        connection.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    is_archived, is_deleted, user_id
                ) VALUES (1, :now, :now, 'Meeting', 'rec-1', 'uid-1',
                          '/tmp/m.wav', 'PROCESSING', 100, 100, 0, 0, 1)
                """
            ),
            {"now": _now()},
        )
    with Session(engine) as db:
        yield db
    engine.dispose()


@pytest.fixture
def metrics(monkeypatch) -> list[dict[str, Any]]:
    from backend.worker.tasks import speaker_name_carryover

    recorded: list[dict[str, Any]] = []
    monkeypatch.setattr(
        speaker_name_carryover,
        "record_pipeline_metric",
        lambda **kwargs: recorded.append(kwargs),
    )
    return recorded


def _add_person(db, person_id: int, name: str, embedding, *, version: int = 2) -> None:
    db.execute(
        text(
            """
            INSERT INTO global_speakers (
                id, created_at, updated_at, user_id, name, embedding,
                embedding_version, is_voiceprint_locked
            ) VALUES (:id, :now, :now, 1, :name, :embedding, :version, 0)
            """
        ),
        {
            "id": person_id,
            "now": _now(),
            "name": name,
            "embedding": json.dumps(embedding) if embedding else None,
            "version": version,
        },
    )


def _add_event(db, event_id: int, name: str, span: tuple[int, int], **fields) -> None:
    """A name event; ``fields`` override source, authority and person."""
    db.execute(
        text(
            """
            INSERT INTO speaker_name_events (
                id, created_at, updated_at, public_id, recording_id, kind, name,
                global_speaker_id, source, authority, anchor_start_ms, anchor_end_ms
            ) VALUES (:id, :now, :now, :public_id, 1, 'name', :name, :person,
                      :source, :authority, :start, :end)
            """
        ),
        {
            "id": event_id,
            "now": _now(),
            "public_id": f"ev-{event_id}",
            "name": name,
            "person": fields.get("person"),
            "source": fields.get("source", "user"),
            "authority": fields.get("authority", 40),
            "start": span[0],
            "end": span[1],
        },
    )


def _add_speaker(db, speaker_id: int, label: str, **fields: Any) -> None:
    values = {
        "id": speaker_id,
        "now": _now(),
        "public_id": f"rs-{speaker_id}",
        "label": label,
        "name": fields.get("name"),
        "local_name": fields.get("local_name"),
        "merged_into_id": fields.get("merged_into_id"),
        "name_source": fields.get("name_source"),
    }
    db.execute(
        text(
            """
            INSERT INTO recording_speakers (
                id, created_at, updated_at, public_id, recording_id,
                diarization_label, name, local_name, merged_into_id, name_source,
                speaker_status, speaker_kind, identity_locked
            ) VALUES (:id, :now, :now, :public_id, 1, :label, :name, :local_name,
                      :merged_into_id, :name_source, 'active', 'automated', 0)
            """
        ),
        values,
    )


def _segments(*spans: tuple[str, float, float]) -> list[dict[str, Any]]:
    return [
        {"start": start, "end": end, "speaker": label, "text": "words"}
        for label, start, end in spans
    ]


def _run(db, monkeypatch, segments, voiceprints: dict[str, list[float]] | None = None):
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
    db.commit()
    ctx = SimpleNamespace(session=db, merged_config={}, recording_id=1)
    speaker_assignment.assign_and_identify_speakers(
        ctx, db.get(Recording, 1), segments, None
    )
    db.commit()


def _speakers(db) -> dict[str, dict[str, Any]]:
    rows = db.execute(
        text(
            "SELECT diarization_label, name, local_name, global_speaker_id, "
            "name_source, identity_locked, merged_into_id "
            "FROM recording_speakers ORDER BY diarization_label"
        )
    ).all()
    return {
        row[0]: {
            "name": row[1],
            "local_name": row[2],
            "person": row[3],
            "source": row[4],
            "locked": bool(row[5]),
            "merged_into": row[6],
        }
        for row in rows
    }


def _person_embedding(db, person_id: int):
    value = db.execute(
        text("SELECT embedding FROM global_speakers WHERE id = :id"), {"id": person_id}
    ).scalar_one()
    return json.loads(value) if value else None


def _two_speakers() -> list[dict[str, Any]]:
    # A fresh list each time: a merge rewrites the run's segments in place.
    return _segments(("SPEAKER_00", 0.0, 10.0), ("SPEAKER_01", 10.0, 20.0))


def test_a_user_name_follows_its_span_to_the_speaker_that_holds_it(
    session, monkeypatch, metrics
) -> None:
    # Named on the speech at 10-20 s, whatever label it had then.
    _add_event(session, 1, "Alex", (10000, 20000))

    _run(session, monkeypatch, _two_speakers())

    speakers = _speakers(session)
    assert speakers["SPEAKER_01"] == {
        "name": "Alex",
        "local_name": "Alex",
        "person": None,
        "source": "user",
        "locked": True,
        "merged_into": None,
    }
    assert speakers["SPEAKER_00"]["name"] == "Speaker 1"
    assert speakers["SPEAKER_00"]["local_name"] is None
    assert speakers["SPEAKER_00"]["source"] is None
    assert [m["payload"]["applied"] for m in metrics] == ["held"]
    assert metrics[0]["payload"]["holder"] == "SPEAKER_01"


@pytest.mark.parametrize("named", [False, True], ids=["unnamed", "named"])
def test_a_user_name_stops_identification_for_its_speaker(
    session, monkeypatch, named
) -> None:
    _add_person(session, 5, "Bob", PERSON)
    if named:
        _add_event(session, 1, "Alex", (0, 10000))

    _run(session, monkeypatch, _two_speakers(), {"SPEAKER_00": PERSON})

    speaker = _speakers(session)["SPEAKER_00"]
    if named:
        assert (speaker["local_name"], speaker["person"]) == ("Alex", None)
        # Identification never ran, so it never folded into Bob either.
        assert _person_embedding(session, 5) == PERSON
    else:
        assert (speaker["person"], speaker["source"], speaker["locked"]) == (
            5,
            "voiceprint",
            False,
        )


@pytest.mark.parametrize(
    # The person's voiceprint and its version, the cluster's, and whether the
    # user's link holds.
    "case",
    [
        pytest.param(
            (PERSON, 2, _at_cosine(0.65), True), id="cluster-sounds-like-them"
        ),
        pytest.param((PERSON, 2, _at_cosine(0.6), True), id="at-the-floor"),
        pytest.param((PERSON, 2, _at_cosine(0.55), False), id="floor-refuses"),
        pytest.param((None, 2, _at_cosine(0.1), True), id="person-has-no-voiceprint"),
        pytest.param((PERSON, 1, _at_cosine(0.1), True), id="person-voiceprint-stale"),
        pytest.param((PERSON, 2, None, True), id="cluster-has-no-voiceprint"),
    ],
)
def test_a_user_link_to_a_person_is_checked_against_their_voiceprint(
    session, monkeypatch, case
) -> None:
    person_embedding, person_version, cluster, linked = case
    _add_person(session, 7, "Dana", person_embedding, version=person_version)
    _add_event(session, 1, "Dana", (0, 10000), person=7)

    _run(
        session,
        monkeypatch,
        _two_speakers(),
        {"SPEAKER_00": cluster} if cluster is not None else {},
    )

    speaker = _speakers(session)["SPEAKER_00"]
    if linked:
        assert (speaker["person"], speaker["local_name"], speaker["locked"]) == (
            7,
            None,
            True,
        )
    else:
        # Offered as "Dana?": the name, no link, not locked.
        assert (speaker["person"], speaker["local_name"], speaker["locked"]) == (
            None,
            "Dana",
            False,
        )
        assert speaker["source"] == "user"
    # Carrying a name teaches the library nothing.
    assert _person_embedding(session, 7) == person_embedding


def test_a_near_tie_is_offered_only_on_a_speaker_nothing_else_named(
    session, monkeypatch, metrics
) -> None:
    segments = _segments(
        ("SPEAKER_00", 0.0, 5.5),
        ("SPEAKER_01", 5.5, 10.0),
        ("SPEAKER_02", 20.0, 25.5),
        ("SPEAKER_03", 25.5, 30.0),
    )
    _add_person(session, 5, "Bob", PERSON)
    _add_event(session, 1, "Alex", (0, 10000))
    _add_event(session, 2, "Sam", (20000, 30000))

    # SPEAKER_02 sounds like Bob: the voiceprint match stands.
    _run(session, monkeypatch, segments, {"SPEAKER_02": PERSON})

    speakers = _speakers(session)
    assert speakers["SPEAKER_00"]["local_name"] == "Alex"
    assert speakers["SPEAKER_00"]["locked"] is False
    assert speakers["SPEAKER_00"]["name"] == "Speaker 1"
    assert speakers["SPEAKER_01"]["local_name"] is None
    assert (speakers["SPEAKER_02"]["person"], speakers["SPEAKER_02"]["local_name"]) == (
        5,
        None,
    )
    assert {m["payload"]["outcome"] for m in metrics} == {"near_tie"}


def test_a_user_name_outranks_an_earlier_runs_machine_name(
    session, monkeypatch
) -> None:
    # A requeued run: the row still carries the LLM's name from the last one.
    _add_speaker(
        session, 1, "SPEAKER_00", name=None, local_name="Alexa", name_source="llm"
    )
    _add_event(session, 1, "Alex", (0, 10000))

    _run(session, monkeypatch, _two_speakers())

    speaker = _speakers(session)["SPEAKER_00"]
    assert (speaker["local_name"], speaker["source"], speaker["locked"]) == (
        "Alex",
        "user",
        True,
    )


def test_a_user_name_for_no_one_drops_an_earlier_runs_voiceprint_link(
    session, monkeypatch
) -> None:
    _add_person(session, 5, "Bob", None)
    _add_speaker(session, 1, "SPEAKER_00", name="Bob", name_source="voiceprint")
    session.execute(text("UPDATE recording_speakers SET global_speaker_id = 5"))
    _add_event(session, 1, "Alex", (0, 10000))

    _run(session, monkeypatch, _two_speakers())

    speaker = _speakers(session)["SPEAKER_00"]
    assert (speaker["person"], speaker["local_name"], speaker["source"]) == (
        None,
        "Alex",
        "user",
    )


def test_a_merge_made_by_hand_still_wins(session, monkeypatch) -> None:
    _add_speaker(session, 1, "SPEAKER_00", local_name="Sam", name_source="user")
    _add_speaker(session, 2, "SPEAKER_01", name="Sam", merged_into_id=1)
    _add_event(session, 1, "Alex", (10000, 20000))

    _run(session, monkeypatch, _two_speakers())

    merged = _speakers(session)["SPEAKER_01"]
    assert (merged["merged_into"], merged["name"], merged["local_name"]) == (
        1,
        "Sam",
        None,
    )


def test_a_requeued_run_derives_machine_rows_again_and_keeps_the_users(
    session, monkeypatch
) -> None:
    _add_event(session, 1, "Alexa", (0, 10000), source="llm", authority=20)
    _add_event(session, 2, "Alex", (10000, 20000))

    _run(session, monkeypatch, _two_speakers())
    _run(session, monkeypatch, _two_speakers())

    rows = session.execute(
        text("SELECT id, source FROM speaker_name_events ORDER BY id")
    ).all()
    assert [tuple(row) for row in rows] == [(2, "user")]
    assert _speakers(session)["SPEAKER_00"]["local_name"] is None


def test_an_event_that_lands_nowhere_changes_nothing(session, monkeypatch) -> None:
    segments = _segments(
        ("SPEAKER_00", 0.0, 10.0),
        ("SPEAKER_01", 10.0, 20.0),
        ("SPEAKER_00", 20.0, 30.0),
    )
    _add_person(session, 5, "Bob", PERSON)
    voiceprints = {"SPEAKER_01": PERSON}

    without = copy.deepcopy(segments)
    _run(session, monkeypatch, without, voiceprints)
    before = _speakers(session)
    session.execute(text("DELETE FROM recording_speakers"))
    session.execute(
        text("UPDATE global_speakers SET embedding = :e"), {"e": json.dumps(PERSON)}
    )

    # Named on speech the final pass has nowhere.
    _add_event(session, 1, "Alex", (100000, 110000))
    with_event = copy.deepcopy(segments)
    _run(session, monkeypatch, with_event, voiceprints)

    assert _speakers(session) == before
    assert with_event == without
