"""Worker-side paths into a person's library voiceprint, and the speech figure
every recording voiceprint now carries.

As in ``test_library_voiceprint_api``, ``_at_cosine(c)`` is a unit vector at
cosine ``c`` to ``PERSON``, and the floor (20 s) and the guard (0.70) are
literals.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from pyannote.core import Annotation, Segment
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session

import backend.processing.embedding_core as embedding_core
from backend.models.recording import Recording
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.tests.sqlite_schemas import (
    GLOBAL_SPEAKERS_SCHEMA,
    P_TAGS_SCHEMA,
    PEOPLE_TAGS_SCHEMA,
    RECORDING_SPEAKER_ALIASES_SCHEMA,
    RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDINGS_SCHEMA,
)

PERSON = [1.0, 0.0]
ELI = [0.0, 1.0]


def _at_cosine(cosine: float) -> list[float]:
    return [cosine, math.sqrt(1.0 - cosine * cosine)]


def _blend(current: list[float], new: list[float], alpha: float) -> list[float]:
    return [(1 - alpha) * c + alpha * n for c, n in zip(current, new)]


class _UnitModel:
    """Stands in for the embedding model: every crop embeds to the same voice."""

    def crop(self, audio_path, segment):
        return np.array([1.0, 0.0])


@pytest.fixture
def unit_model(monkeypatch):
    monkeypatch.setitem(
        embedding_core._embedding_model_cache,
        (embedding_core.DEFAULT_EMBEDDING_MODEL, "cpu"),
        _UnitModel(),
    )


@pytest.mark.parametrize(
    ("segments", "seconds"),
    [
        # The 0.4 s piece is below the 1 s crop floor and does not count.
        ([(0.0, 10.0), (12.0, 13.5), (20.0, 20.4)], 11.5),
        # A long turn is cut into 30 s crops; all of it still counts.
        ([(0.0, 70.0)], 70.0),
        # Overlapping selections count the speech they share once.
        ([(0.0, 10.0), (5.0, 15.0), (5.0, 15.0)], 15.0),
    ],
)
def test_a_segment_voiceprint_is_measured_in_selected_crop_seconds(
    unit_model, segments, seconds
) -> None:
    voiceprint = embedding_core.extract_embedding_for_segments(
        "meeting.wav", segments, device_str="cpu", hf_token="token"
    )

    assert voiceprint.embedding == pytest.approx(PERSON)
    assert voiceprint.speech_seconds == pytest.approx(seconds)


def test_a_pipeline_voiceprint_leaves_overlapped_speech_out_of_its_seconds(
    unit_model,
) -> None:
    annotation = Annotation()
    annotation[Segment(0.0, 10.0)] = "A"
    annotation[Segment(8.0, 12.0)] = "B"

    voiceprints = embedding_core.extract_embeddings(
        "meeting.wav", annotation, device_str="cpu", config={"hf_token": "token"}
    )

    assert {label: v.speech_seconds for label, v in voiceprints.items()} == {
        "A": pytest.approx(8.0),
        "B": pytest.approx(2.0),
    }


def test_each_extraction_task_keeps_its_own_result_shape(unit_model) -> None:
    """The measured task carries the speech; the old name still returns a vector.

    An API one version behind its worker calls the old name and stores what it
    gets as a voiceprint, so that name must never start returning a dict. The
    current API refuses the bare vector: a new extraction is never unmeasured,
    since only voiceprints from before the figure existed are grandfathered.
    """
    from backend.processing.embedding import RecordingVoiceprint
    from backend.worker.tasks.embeddings import (
        extract_embedding_task,
        extract_measured_embedding_task,
    )

    segments = [(0.0, 10.0), (12.0, 13.5)]
    measured = extract_measured_embedding_task.run("meeting.wav", segments, "cpu", "t")
    bare = extract_embedding_task.run("meeting.wav", segments, "cpu", "t")

    assert measured["embedding"] == pytest.approx(PERSON)
    assert measured["speech_seconds"] == pytest.approx(11.5)
    assert bare == pytest.approx(PERSON)
    assert RecordingVoiceprint.from_task_result(measured).speech_seconds == 11.5
    with pytest.raises(TypeError, match="no speech figure"):
        RecordingVoiceprint.from_task_result(bare)
    with pytest.raises(TypeError, match="no speech figure"):
        RecordingVoiceprint.from_task_result(
            {"embedding": bare, "speech_seconds": None}
        )


@pytest.mark.parametrize("empty", [None, [], {}])
def test_an_empty_extraction_result_reads_as_no_voiceprint(empty) -> None:
    from backend.processing.embedding import RecordingVoiceprint

    assert RecordingVoiceprint.from_task_result(empty) is None


def test_an_unrecognised_extraction_result_is_an_error_not_a_voiceprint() -> None:
    from backend.processing.embedding import RecordingVoiceprint

    with pytest.raises(TypeError, match="returned a str"):
        RecordingVoiceprint.from_task_result("[1.0, 0.0]")


class _FakeSession:
    def __init__(self, recording, speaker, person):
        self._rows = {
            "Recording": recording,
            "RecordingSpeaker": speaker,
            "GlobalSpeaker": person,
        }

    def get(self, model, identity):
        return self._rows.get(getattr(model, "__name__", ""))

    def add(self, value):
        pass

    def commit(self):
        pass

    def rollback(self):
        raise AssertionError("the update must not fail")


def _learn_segment(monkeypatch, tmp_path, person, incoming, seconds):
    """Move a segment onto a recording speaker linked to ``person``; returns the speaker."""
    from backend.processing.embedding import RecordingVoiceprint
    from backend.worker.tasks.embeddings import update_speaker_embedding_task

    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"wav")
    recording = SimpleNamespace(id=1, audio_path=str(audio), proxy_path=None)
    speaker = SimpleNamespace(
        id=9,
        diarization_label="SPEAKER_00",
        embedding=list(PERSON),
        embedding_version=2,
        embedding_speech_seconds=10.0,
        global_speaker_id=7,
        drift_refusals=[],
    )
    monkeypatch.setattr(
        embedding_core,
        "extract_embedding_for_segments",
        lambda *a, **k: RecordingVoiceprint(embedding=incoming, speech_seconds=seconds),
    )

    update_speaker_embedding_task._session = _FakeSession(recording, speaker, person)
    try:
        update_speaker_embedding_task.run(1, 0.0, 30.0, 9)
    finally:
        update_speaker_embedding_task._session = None
    return speaker


@pytest.mark.parametrize(
    "case",
    [
        # No voiceprint yet: only enough speech founds one.
        (None, 0.9, 12.0, None, 10.0),
        (None, 0.9, 25.0, _at_cosine(0.9), 10.0),
        # A voiceprint already: the guard decides, whatever the speech.
        (PERSON, 0.65, 25.0, PERSON, 10.0),
        (PERSON, 0.75, 5.0, _blend(PERSON, _at_cosine(0.75), 0.5), 10.0),
    ],
)
def test_segment_learning_applies_the_floor_and_the_drift_guard(
    monkeypatch, tmp_path, case
) -> None:
    person_voiceprint, cosine, seconds, person_after, speaker_after = case
    person = SimpleNamespace(
        id=7,
        embedding=list(person_voiceprint) if person_voiceprint else None,
        embedding_version=2 if person_voiceprint else None,
        is_voiceprint_locked=False,
    )

    speaker = _learn_segment(monkeypatch, tmp_path, person, _at_cosine(cosine), seconds)

    if person_after is None:
        assert person.embedding is None
    else:
        assert person.embedding == pytest.approx(person_after)
    assert speaker.embedding_speech_seconds == speaker_after


@pytest.mark.parametrize(
    ("person_voiceprint", "person_version"),
    [
        # A current voiceprint the segment would fold into.
        (PERSON, 2),
        # A stale one the segment's would replace.
        (PERSON, 1),
        # None at all, which enough speech would found.
        (None, None),
    ],
)
def test_segment_learning_leaves_a_locked_voiceprint_as_it_is(
    monkeypatch, tmp_path, person_voiceprint, person_version
) -> None:
    """Moving a segment corrects a transcript line, not a locked voiceprint.

    The recording speaker still learns the segment; only the person's library
    voiceprint is left alone.
    """
    person = SimpleNamespace(
        id=7,
        embedding=list(person_voiceprint) if person_voiceprint else None,
        embedding_version=person_version,
        is_voiceprint_locked=True,
    )
    incoming = _at_cosine(0.9)

    speaker = _learn_segment(monkeypatch, tmp_path, person, incoming, 25.0)

    assert person.embedding == person_voiceprint
    assert person.embedding_version == person_version
    assert speaker.embedding == pytest.approx(_blend(PERSON, incoming, 0.5))
    assert speaker.embedding_speech_seconds == 10.0


def test_segment_learning_reports_a_locked_voiceprint_as_skipped(
    monkeypatch, tmp_path, caplog
) -> None:
    """The task has no response to carry the outcome, so its log line says it.

    And the log line is true: the person's voiceprint, version and lock are as
    they were, while the recording speaker still learned the segment.
    """
    person = SimpleNamespace(
        id=7, embedding=list(PERSON), embedding_version=2, is_voiceprint_locked=True
    )
    incoming = _at_cosine(0.9)

    with caplog.at_level(logging.INFO, logger="backend.worker.tasks.constants"):
        speaker = _learn_segment(monkeypatch, tmp_path, person, incoming, 25.0)

    assert "Segment voiceprint for person 7: skipped_locked" in caplog.text
    assert (person.embedding, person.embedding_version) == (PERSON, 2)
    assert person.is_voiceprint_locked is True
    assert speaker.embedding == pytest.approx(_blend(PERSON, incoming, 0.5))


@pytest.mark.parametrize(
    # The person's voiceprint and version (None: the speaker is linked to no
    # one), the refused speaker's voiceprint, the segment's, and the people
    # the speaker is refused for afterwards.
    "case",
    [
        pytest.param(
            ((PERSON, 2), _at_cosine(0.5), _at_cosine(0.45), [7]), id="refused-again"
        ),
        pytest.param(
            ((PERSON, 2), _at_cosine(0.65), _at_cosine(0.986), []), id="let-back-in"
        ),
        # A segment the speaker's own voiceprint refuses changes nothing.
        pytest.param(((PERSON, 2), _at_cosine(0.5), PERSON, [7]), id="fold-refused"),
        # No voiceprint of the person's to judge the result against: none at
        # all, or one of an older extraction method that it can't be scored
        # against, though it would pass if it were.
        pytest.param(
            ((None, None), _at_cosine(0.5), _at_cosine(0.45), [7]), id="no-voiceprint"
        ),
        pytest.param(
            ((PERSON, 1), _at_cosine(0.65), _at_cosine(0.986), [7]),
            id="stale-voiceprint",
        ),
        pytest.param((None, _at_cosine(0.5), _at_cosine(0.45), [7]), id="unlinked"),
    ],
)
def test_a_segment_moved_onto_a_refused_speaker_gets_a_new_verdict(
    monkeypatch, tmp_path, case
) -> None:
    """The speaker was refused for person 7. A segment folded in changes its
    voiceprint, and the guard judges the result against the person it is
    linked to: still refused, or let back in. Where there is no one to judge
    it against, the refusal stays, since the speech the guard refused is
    still the speaker's."""
    from backend.processing.embedding import RecordingVoiceprint
    from backend.worker.tasks.embeddings import update_speaker_embedding_task

    person, speaker_voiceprint, segment, refused_after = case
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"wav")
    recording = SimpleNamespace(id=1, audio_path=str(audio), proxy_path=None)
    linked = person is not None
    if linked:
        person_voiceprint, person_version = person
        person = SimpleNamespace(
            id=7,
            embedding=list(person_voiceprint) if person_voiceprint else None,
            embedding_version=person_version,
            is_voiceprint_locked=False,
        )
    speaker = SimpleNamespace(
        id=9,
        diarization_label="SPEAKER_00",
        embedding=list(speaker_voiceprint),
        embedding_version=2,
        embedding_speech_seconds=25.0,
        global_speaker_id=7 if linked else None,
        drift_refusals=[SimpleNamespace(global_speaker_id=7)],
    )
    monkeypatch.setattr(
        embedding_core,
        "extract_embedding_for_segments",
        lambda *a, **k: RecordingVoiceprint(embedding=segment, speech_seconds=4.5),
    )

    update_speaker_embedding_task._session = _FakeSession(recording, speaker, person)
    try:
        update_speaker_embedding_task.run(1, 0.0, 4.5, 9)
    finally:
        update_speaker_embedding_task._session = None

    assert [r.global_speaker_id for r in speaker.drift_refusals] == refused_after


@pytest.mark.parametrize(
    ("speaker_voiceprint", "speaker_seconds", "speaker_after"),
    [
        # A fold keeps the speaker's own extraction figure, however often a
        # segment is moved onto it.
        (PERSON, 2.0, 2.0),
        # A speaker with no voiceprint takes the segment's, and its figure.
        (None, None, 4.5),
    ],
)
def test_moving_a_segment_onto_a_speaker_never_adds_to_its_speech(
    monkeypatch, tmp_path, speaker_voiceprint, speaker_seconds, speaker_after
) -> None:
    from backend.processing.embedding import RecordingVoiceprint
    from backend.worker.tasks.embeddings import update_speaker_embedding_task

    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"wav")
    recording = SimpleNamespace(id=1, audio_path=str(audio), proxy_path=None)
    speaker = SimpleNamespace(
        id=9,
        diarization_label="SPEAKER_00",
        embedding=list(speaker_voiceprint) if speaker_voiceprint else None,
        embedding_version=2 if speaker_voiceprint else None,
        embedding_speech_seconds=speaker_seconds,
        global_speaker_id=None,
        drift_refusals=[],
    )
    monkeypatch.setattr(
        embedding_core,
        "extract_embedding_for_segments",
        lambda *a, **k: RecordingVoiceprint(
            embedding=_at_cosine(0.95), speech_seconds=4.5
        ),
    )

    update_speaker_embedding_task._session = _FakeSession(recording, speaker, None)
    try:
        for _ in range(4):
            update_speaker_embedding_task.run(1, 0.0, 4.5, 9)
    finally:
        update_speaker_embedding_task._session = None

    assert speaker.embedding_speech_seconds == speaker_after


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
                          '/tmp/m.wav', 'PROCESSED', 100, 100, 0, 0, 1)
                """
            ),
            {"now": _now()},
        )
    with Session(engine) as db:
        yield db
    engine.dispose()


def _add_person(
    db, person_id: int, embedding: list[float] | None, *, locked: bool = False
) -> None:
    db.execute(
        text(
            """
            INSERT INTO global_speakers (
                id, created_at, updated_at, user_id, name, embedding,
                embedding_version, is_voiceprint_locked
            ) VALUES (:id, :now, :now, 1, :name, :embedding, 2, :locked)
            """
        ),
        {
            "id": person_id,
            "now": _now(),
            "name": f"Person {person_id}",
            "embedding": json.dumps(embedding) if embedding else None,
            "locked": int(locked),
        },
    )


def _add_speaker(db, speaker_id: int, embedding, seconds) -> None:
    db.execute(
        text(
            """
            INSERT INTO recording_speakers (
                id, created_at, updated_at, public_id, recording_id,
                diarization_label, name, embedding, embedding_version,
                embedding_speech_seconds, speaker_status, speaker_kind,
                identity_locked
            ) VALUES (:id, :now, :now, :public_id, 1, :label, :label,
                      :embedding, 2, :seconds, 'active', 'automated', 0)
            """
        ),
        {
            "id": speaker_id,
            "now": _now(),
            "public_id": f"rs-{speaker_id}",
            "label": f"SPEAKER_{speaker_id:02d}",
            "embedding": json.dumps(embedding),
            "seconds": seconds,
        },
    )


def test_auto_applied_name_suggestions_found_and_fold_under_the_same_rules(
    session,
) -> None:
    from backend.worker.tasks.intelligence import (
        _auto_apply_persisted_speaker_name_suggestions,
    )

    voice, far = _at_cosine(0.9), _at_cosine(0.65)
    for person_id, embedding in ((7, None), (8, None), (9, PERSON)):
        _add_person(session, person_id, embedding)
    for speaker_id, embedding, seconds in (
        (1, voice, 12.0),
        (2, voice, 25.0),
        (3, far, 40.0),
    ):
        _add_speaker(session, speaker_id, embedding, seconds)
    session.commit()
    speakers = [session.get(RecordingSpeaker, i) for i in (1, 2, 3)]

    applied = _auto_apply_persisted_speaker_name_suggestions(
        session,
        recording=session.get(Recording, 1),
        speakers=speakers,
        persisted=[
            {
                "suggested_name": f"Person {person_id}",
                "recording_speaker_id": speaker_id,
                "suggested_global_speaker_id": person_id,
                "confidence": 0.9,
            }
            for speaker_id, person_id in ((1, 7), (2, 8), (3, 9))
        ],
    )
    session.commit()

    assert len(applied) == 3
    people = {i: session.get(GlobalSpeaker, i).embedding for i in (7, 8, 9)}
    assert people[7] is None
    assert people[8] == pytest.approx(voice)
    assert people[9] == pytest.approx(PERSON)


def test_an_auto_applied_name_suggestion_never_changes_a_locked_voiceprint(
    session,
) -> None:
    """Pins upstream's lock on auto-applied suggestions; it can't fail first.

    The suggestion path keeps ``respect_voiceprint_lock=True``, so a locked
    person is neither folded into nor given a first voiceprint, however much
    speech and however close the voice. The floor's helper took over that
    decision, so this keeps it from being lost.
    """
    from backend.worker.tasks.intelligence import (
        _auto_apply_persisted_speaker_name_suggestions,
    )

    _add_person(session, 7, PERSON, locked=True)
    _add_person(session, 8, None, locked=True)
    _add_speaker(session, 1, _at_cosine(0.95), 40.0)
    _add_speaker(session, 2, _at_cosine(0.95), 40.0)
    session.commit()

    _auto_apply_persisted_speaker_name_suggestions(
        session,
        recording=session.get(Recording, 1),
        speakers=[session.get(RecordingSpeaker, i) for i in (1, 2)],
        persisted=[
            {
                "suggested_name": f"Person {person_id}",
                "recording_speaker_id": speaker_id,
                "suggested_global_speaker_id": person_id,
                "confidence": 0.9,
            }
            for speaker_id, person_id in ((1, 7), (2, 8))
        ],
    )
    session.commit()

    assert session.get(GlobalSpeaker, 7).embedding == PERSON
    assert session.get(GlobalSpeaker, 8).embedding is None
    assert session.get(RecordingSpeaker, 1).global_speaker_id == 7


def test_the_pipeline_records_the_speech_behind_each_voiceprint(session) -> None:
    from backend.worker.tasks.speaker_assignment import (
        _absorb_into_existing_name,
        _LabelWork,
        _persist_speaker,
        _ResolvedIdentity,
    )

    _add_speaker(session, 1, PERSON, None)
    session.commit()
    recording = session.get(Recording, 1)

    def work(label: str, existing: Any, seconds: float) -> Any:
        return _LabelWork(
            recording=recording,
            label=label,
            identity=_ResolvedIdentity(name="Dana"),
            embedding=_at_cosine(0.9),
            speech_seconds=seconds,
            existing_speaker=existing,
        )

    updated_id = _persist_speaker(
        session, work("SPEAKER_01", session.get(RecordingSpeaker, 1), 31.0)
    )
    created_id = _persist_speaker(session, work("SPEAKER_02", None, 24.0))
    _absorb_into_existing_name(
        session,
        work("SPEAKER_03", None, 4.0),
        target_id=created_id,
        target_label="SPEAKER_02",
        final_segments=[],
    )
    session.commit()

    seconds = dict(
        session.execute(
            text(
                "SELECT diarization_label, embedding_speech_seconds FROM recording_speakers"
            )
        ).all()
    )
    assert updated_id == 1
    assert seconds == {"SPEAKER_01": 31.0, "SPEAKER_02": 24.0, "SPEAKER_03": 4.0}


def test_a_run_lifts_the_refusal_of_a_speaker_that_now_matches(session) -> None:
    """A run that re-extracts a speaker gives it this run's voiceprint.

    The guard refused the old one for person 7; the new one matches them, so
    judged against them it passes and the refusal goes. Folding a confident
    match into the person changes no other speaker's voiceprint, so another
    speaker's refusal for them stays.
    """
    from backend.worker.tasks.speaker_assignment import (
        _LabelWork,
        _learn_from_confident_match,
        _persist_speaker,
        _ResolvedIdentity,
    )

    _add_person(session, 7, PERSON)
    _add_speaker(session, 1, PERSON, 30.0)
    _add_speaker(session, 2, _at_cosine(0.1), 30.0)
    session.execute(text("UPDATE recording_speakers SET global_speaker_id = 7"))
    session.execute(
        text(
            "INSERT INTO recording_speaker_drift_refusals "
            "(recording_speaker_id, global_speaker_id) VALUES (1, 7), (2, 7)"
        )
    )
    session.commit()
    person = session.get(GlobalSpeaker, 7)
    new_voiceprint = _at_cosine(0.95)

    _learn_from_confident_match(session, person, new_voiceprint, 0.95)
    _persist_speaker(
        session,
        _LabelWork(
            recording=session.get(Recording, 1),
            label="SPEAKER_01",
            identity=_ResolvedIdentity(name="Person 7", global_speaker_id=7),
            embedding=new_voiceprint,
            speech_seconds=30.0,
            existing_speaker=session.get(RecordingSpeaker, 1),
        ),
    )
    session.commit()

    refused = session.execute(
        text(
            "SELECT recording_speaker_id, global_speaker_id "
            "FROM recording_speaker_drift_refusals"
        )
    ).all()
    assert [tuple(row) for row in refused] == [(2, 7)]
    assert session.get(GlobalSpeaker, 7).embedding == pytest.approx(
        _blend(PERSON, new_voiceprint, 0.1)
    )


@pytest.mark.parametrize(
    # Speaker 2 linked to, refused for before, the run's person, its voiceprint,
    # refused for after, linked to after.
    "case",
    [
        pytest.param(
            (7, (7,), None, _at_cosine(0.15), [7], 7), id="stranger-stays-refused"
        ),
        pytest.param(
            (7, (), None, _at_cosine(0.1), [7], 7), id="stranger-gains-refusal"
        ),
        pytest.param(
            (7, (7, 8), None, _at_cosine(0.95), [8], 7), id="eli-refusal-stays"
        ),
        pytest.param(
            (None, (7,), None, _at_cosine(0.1), [7], None), id="unlinked-keeps-refusal"
        ),
        pytest.param((7, (8,), 8, ELI, [], 8), id="moved-to-eli-judged-for-eli"),
        pytest.param(
            (7, (8,), None, _at_cosine(0.1), [7, 8], 7), id="refusal-keeps-elis"
        ),
        pytest.param((7, (7,), None, None, [7], 7), id="no-new-voiceprint-no-verdict"),
    ],
)
def test_a_run_judges_the_new_voiceprint_of_a_speaker_already_there(
    session, case
) -> None:
    """Speaker 2 already exists when the run's assign stage stores it, as one
    named during capture does, and gets this run's voiceprint.

    The guard judges it against the person the speaker ends up linked to: a
    stranger's is refused, so person 7's rebuild from links leaves it out. A
    refusal for anyone else stays, an unlinked speaker gets no verdict, and a
    speaker the run moves to person 8 is judged for them, not person 7. Its
    old voiceprint now sounds like person 7, but a run that extracts no new
    one keeps it unjudged, so its refusal stays.
    """
    from backend.worker.tasks.speaker_assignment import (
        _LabelWork,
        _persist_speaker,
        _ResolvedIdentity,
    )
    from backend.worker.tasks.voiceprint_rebuild import _rebuild_global_speaker

    linked, refused_before, run_person, voiceprint, refused_after, linked_after = case
    _add_person(session, 7, PERSON)
    _add_person(session, 8, ELI)
    _add_speaker(session, 1, PERSON, 30.0)
    _add_speaker(session, 2, _at_cosine(0.95), 30.0)
    session.execute(text("UPDATE recording_speakers SET global_speaker_id = 7"))
    session.execute(
        text("UPDATE recording_speakers SET global_speaker_id = :p WHERE id = 2"),
        {"p": linked},
    )
    for person_id in refused_before:
        session.execute(
            text(
                "INSERT INTO recording_speaker_drift_refusals "
                "(recording_speaker_id, global_speaker_id) VALUES (2, :p)"
            ),
            {"p": person_id},
        )
    session.commit()

    _persist_speaker(
        session,
        _LabelWork(
            recording=session.get(Recording, 1),
            label="SPEAKER_02",
            identity=_ResolvedIdentity(name="Speaker 2", global_speaker_id=run_person),
            embedding=voiceprint,
            speech_seconds=30.0,
            existing_speaker=session.get(RecordingSpeaker, 2),
        ),
    )
    session.commit()

    refused = session.execute(
        text(
            "SELECT global_speaker_id FROM recording_speaker_drift_refusals "
            "WHERE recording_speaker_id = 2 ORDER BY global_speaker_id"
        )
    ).scalars()
    assert list(refused) == refused_after
    assert session.get(RecordingSpeaker, 2).global_speaker_id == linked_after
    if linked_after == 7 and 7 in refused_after:
        _rebuild_global_speaker(session, session.get(GlobalSpeaker, 7), 2)
        session.commit()
        assert session.get(GlobalSpeaker, 7).embedding == pytest.approx(PERSON)
