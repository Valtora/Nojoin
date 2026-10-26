"""Visible speaker edits publish without moving the utterance event cursor."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import Engine, event, func, text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from backend.models.base import BaseDBModel
from backend.models.pipeline import TranscriptUtteranceEvent
from backend.models.recording import Recording
from backend.models.recording_public import serialize_recording_speaker
from backend.models.speaker import GlobalSpeaker, GlobalSpeakerRead, RecordingSpeaker
from backend.models.transcript import Transcript
from backend.tests.test_transcript_publication import (
    _append,
    _recording,
    _transcript,
)
from backend.tests.test_transcript_publication import (
    publication_engine as publication_engine,
)
from backend.utils.canonical_pipeline import get_canonical_transcript_revision

# Discover cases from the response schema, so newly public person columns must
# publish too. Do not take this list from the publication detector under test.
PUBLIC_PERSON_FIELDS = sorted(
    set(GlobalSpeakerRead.model_fields)
    - set(BaseDBModel.model_fields)
    - {"has_voiceprint", "tags"}
)


def _person_recordings(session: Session) -> list[int]:
    """Create linked and unrelated targets with distinct persisted audio paths."""
    ids: list[int] = []
    for index in range(3):
        recording = Recording(
            name="Capture", audio_path=f"/tmp/person-publication-{index}.wav"
        )
        session.add(recording)
        session.flush()
        assert recording.id is not None
        ids.append(recording.id)
        session.add(Transcript(recording_id=recording.id, text="", segments=[]))
    session.commit()
    return ids


@pytest.mark.parametrize("field", PUBLIC_PERSON_FIELDS)
@pytest.mark.parametrize("expire", [False, True])
def test_nested_public_person_field_publishes_each_linked_transcript_once(
    publication_engine: Engine, field: str, expire: bool
) -> None:
    with Session(publication_engine, expire_on_commit=expire) as session:
        first_id, second_id, unrelated_id = _person_recordings(session)
        linked_ids = [first_id, second_id]
        _append(session, linked_ids[0])
        person = GlobalSpeaker(name="Dana")
        session.add(person)
        session.flush()
        speakers = [
            RecordingSpeaker(
                recording_id=recording_id,
                diarization_label="SPEAKER_00",
                global_speaker_id=person.id,
            )
            for recording_id in linked_ids
        ]
        session.add_all(speakers)
        session.commit()
        baseline = {
            recording_id: _transcript(session, recording_id).change_sequence
            for recording_id in [*linked_ids, unrelated_id]
        }
        cursor = get_canonical_transcript_revision(session, linked_ids[0])
        event_count = session.exec(
            select(func.count()).select_from(TranscriptUtteranceEvent)
        ).one()
        before = serialize_recording_speaker(
            speakers[0], recording_public_id="capture"
        ).global_speaker
        if expire:
            session.expire(person, [field])
        value = True if field == "is_voiceprint_locked" else f"Changed {field}"
        setattr(person, field, value)
        session.flush()
        session.flush()
        session.commit()
        after = serialize_recording_speaker(
            speakers[0], recording_public_id="capture"
        ).global_speaker
        assert before is not None and after is not None
        assert getattr(before, field) != getattr(after, field)
        for recording_id in linked_ids:
            assert (
                _transcript(session, recording_id).change_sequence
                == baseline[recording_id] + 1
            )
        assert (
            _transcript(session, unrelated_id).change_sequence == baseline[unrelated_id]
        )
        assert get_canonical_transcript_revision(session, linked_ids[0]) == cursor
        assert (
            session.exec(
                select(func.count()).select_from(TranscriptUtteranceEvent)
            ).one()
            == event_count
        )
        if expire:
            session.expire(person, [field])
        setattr(person, field, value)
        session.commit()
        for recording_id in linked_ids:
            assert (
                _transcript(session, recording_id).change_sequence
                == baseline[recording_id] + 1
            )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "Mike"),
        ("local_name", "Mike"),
        ("diarization_label", "SPEAKER_01"),
        ("name_source", "user"),
        ("identity_locked", True),
        ("identity_confidence", 0.9),
        ("speaker_status", "merged"),
        ("color", "violet"),
        ("speaker_kind", "manual"),
        ("first_seen_ms", 100),
        ("last_seen_ms", 3000),
        ("name_last_edit_source", "mcp"),
    ],
)
def test_visible_speaker_field_publishes_without_utterance_events(
    publication_engine: Engine, field: str, value: Any
) -> None:
    with Session(publication_engine) as session:
        recording_id = _recording(session)
        _append(session, recording_id)
        speaker = RecordingSpeaker(
            recording_id=recording_id, diarization_label="SPEAKER_00"
        )
        session.add(speaker)
        session.commit()
        baseline = _transcript(session, recording_id).change_sequence
        cursor = get_canonical_transcript_revision(session, recording_id)
        event_count = session.exec(
            select(func.count()).select_from(TranscriptUtteranceEvent)
        ).one()
        setattr(speaker, field, value)
        session.flush()
        session.flush()
        session.commit()
        assert _transcript(session, recording_id).change_sequence == baseline + 1
        assert get_canonical_transcript_revision(session, recording_id) == cursor
        assert (
            session.exec(
                select(func.count()).select_from(TranscriptUtteranceEvent)
            ).one()
            == event_count
        )
        setattr(speaker, field, value)
        session.commit()
        assert _transcript(session, recording_id).change_sequence == baseline + 1


@pytest.mark.parametrize("person", [False, True])
@pytest.mark.parametrize("expire", [False, True])
@pytest.mark.parametrize(
    "transition",
    [(None, [1.0], 1), ([1.0], None, 1), ([1.0], [0.5], 0), (None, [], 0)],
)
def test_voiceprint_availability_publishes_but_private_vector_updates_do_not(
    publication_engine: Engine,
    person: bool,
    expire: bool,
    transition: tuple[list[float] | None, list[float] | None, int],
) -> None:
    previous, current, increment = transition
    with Session(publication_engine, expire_on_commit=expire) as session:
        recording_id = _recording(session)
        global_speaker = GlobalSpeaker(
            name="Dana", embedding=previous if person else None
        )
        session.add(global_speaker)
        session.flush()
        speaker = RecordingSpeaker(
            recording_id=recording_id,
            diarization_label="SPEAKER_00",
            global_speaker_id=global_speaker.id,
            embedding=None if person else previous,
        )
        session.add(speaker)
        session.commit()
        baseline = _transcript(session, recording_id).change_sequence
        target = global_speaker if person else speaker
        target.embedding = current
        session.commit()
        assert (
            _transcript(session, recording_id).change_sequence == baseline + increment
        )
        assert get_canonical_transcript_revision(session, recording_id) == 0


@pytest.mark.parametrize(
    ("person", "field", "initial", "replacement"),
    [
        (False, "color", "violet", "blue"),
        (True, "embedding", [1.0], None),
        *[
            (
                True,
                field,
                False if field == "is_voiceprint_locked" else f"Original {field}",
                True if field == "is_voiceprint_locked" else f"Changed {field}",
            )
            for field in PUBLIC_PERSON_FIELDS
        ],
    ],
)
def test_expired_noop_is_fenced_before_comparing_stored_speaker_fact(
    publication_engine: Engine,
    person: bool,
    field: str,
    initial: Any,
    replacement: Any,
) -> None:
    if publication_engine.dialect.name != "postgresql":
        pytest.skip("Concurrent row locks require PostgreSQL")
    model = GlobalSpeaker if person else RecordingSpeaker
    attempted: list[bool] = []
    with Session(publication_engine) as session:
        recording_id = _recording(session)
        global_speaker = GlobalSpeaker(name="Dana", embedding=[1.0])
        if person:
            setattr(global_speaker, field, initial)
        session.add(global_speaker)
        session.flush()
        speaker = RecordingSpeaker(
            recording_id=recording_id,
            diarization_label="SPEAKER_00",
            global_speaker_id=global_speaker.id,
            color="violet",
        )
        session.add(speaker)
        session.commit()
        target = session.get(model, global_speaker.id if person else speaker.id)
        target_id = target.id
        baseline = _transcript(session, recording_id).change_sequence
        session.expire(target, [field])
        setattr(target, field, initial)

        def compete(*args: Any) -> None:
            statement = args[2]
            if attempted or not statement.startswith(
                f"SELECT {model.__tablename__}.{field}"
            ):
                return
            with Session(publication_engine) as writer:
                writer.execute(text("SET LOCAL lock_timeout = '250ms'"))
                other = writer.get(model, target_id)
                setattr(other, field, replacement)
                try:
                    writer.commit()
                except OperationalError as error:
                    assert "lock timeout" in str(error)
                    attempted.append(True)
                    writer.rollback()
                else:
                    attempted.append(False)

        connection = session.connection()
        event.listen(connection, "after_cursor_execute", compete)
        try:
            session.commit()
        finally:
            event.remove(connection, "after_cursor_execute", compete)
        assert attempted == [True], (
            "Speaker comparison ran without the Transcript fence"
        )
        assert _transcript(session, recording_id).change_sequence == baseline
    with Session(publication_engine) as writer:
        other = writer.get(model, target_id)
        setattr(other, field, replacement)
        writer.commit()
        assert getattr(other, field) == replacement
        assert _transcript(writer, recording_id).change_sequence == baseline + 1


@pytest.mark.parametrize("person", [False, True])
def test_stale_loaded_voiceprint_compares_database_availability(
    publication_engine: Engine, person: bool
) -> None:
    model = GlobalSpeaker if person else RecordingSpeaker
    with Session(publication_engine) as session:
        recording_id = _recording(session)
        global_speaker = GlobalSpeaker(name="Dana", embedding=[1.0])
        session.add(global_speaker)
        session.flush()
        speaker = RecordingSpeaker(
            recording_id=recording_id,
            diarization_label="SPEAKER_00",
            global_speaker_id=global_speaker.id,
            embedding=[1.0],
        )
        session.add(speaker)
        session.commit()
        target = session.get(model, global_speaker.id if person else speaker.id)
        assert target.embedding == [1.0]
        target_id = target.id
        baseline = _transcript(session, recording_id).change_sequence
        with Session(publication_engine) as other:
            concurrent = other.get(model, target_id)
            concurrent.embedding = None
            other.commit()
        target.embedding = [0.5]
        session.commit()
        assert target.has_voiceprint
        assert _transcript(session, recording_id).change_sequence == baseline + 2
