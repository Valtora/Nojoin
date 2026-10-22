"""Live label writes against finalize and against a person's edits.

The label write holds the recording row, so finalize cannot end a capture under
it, and the transcript, so the projection it writes is current. Row locks need
a real server: SQLite has none. Skips without NOJOIN_TEST_POSTGRES_URL, like
the other Postgres tests.
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from backend.models.pipeline import ProcessingRunKind, TranscriptUtteranceState
from backend.models.recording import RecordingStatus
from backend.models.transcript import Transcript
from backend.processing.live_diarization.step import _recording_in_flight
from backend.tests.storage_hygiene_harness import add_recording, postgres_engine
from backend.utils.canonical_pipeline import (
    append_utterances_from_segments,
    update_utterance_speaker,
)
from backend.utils.canonical_pipeline.live_labels import (
    LiveLabelDecision,
    apply_live_speaker_labels,
)


@pytest.mark.parametrize("lock", [True, False])
def test_finalize_waits_for_a_label_write_in_progress(
    postgres_test_url: str, lock: bool
) -> None:
    with postgres_engine(postgres_test_url) as engine:
        recording_id = add_recording(
            engine, "capture.wav", status=RecordingStatus.UPLOADING
        )
        with Session(engine) as labels, Session(engine) as finalize:
            assert _recording_in_flight(labels, recording_id, lock=lock)
            finalize.execute(text("SET lock_timeout = '200ms'"))
            statement = text("UPDATE recordings SET status = 'QUEUED' WHERE id = :id")
            if lock:
                with pytest.raises(OperationalError, match="lock timeout"):
                    finalize.execute(statement, {"id": recording_id})
            else:
                finalize.execute(statement, {"id": recording_id})
            finalize.rollback()
            labels.rollback()


LINES = [
    {"id": "pg-a", "start": 1.0, "end": 9.0, "text": "opening remarks"},
    {"id": "pg-b", "start": 10.0, "end": 14.0, "text": "a short reply"},
    {"id": "pg-c", "start": 21.0, "end": 29.0, "text": "a second voice"},
]


def _live_lines(engine) -> int:
    recording_id = add_recording(
        engine, "capture.wav", status=RecordingStatus.UPLOADING
    )
    with Session(engine) as session:
        session.add(Transcript(recording_id=recording_id, segments=[]))
        append_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[
                {**line, "speaker": "UNKNOWN", "segment_source": "live"}
                for line in LINES
            ],
            run_kind=ProcessingRunKind.LIVE,
            source="live",
            state_override=TranscriptUtteranceState.PROVISIONAL,
            trigger_source="worker",
        )
        session.commit()
    return recording_id


def _everyone(speaker: str):
    return lambda start_ms, end_ms: LiveLabelDecision(speaker, locked=False, share=1.0)


def _in_thread(work):
    """Run ``work`` in a thread; returns (thread, outcome dict)."""
    outcome: dict = {}

    def run():
        try:
            outcome["value"] = work()
        except RuntimeError as exc:  # a revision conflict
            outcome["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    return thread, outcome


def _projection(engine, recording_id: int) -> dict[str, str]:
    with Session(engine) as session:
        transcript = session.execute(
            select(Transcript).where(Transcript.recording_id == recording_id)
        ).scalar_one()
        return {segment["id"]: segment["speaker"] for segment in transcript.segments}


def _rename(engine, recording_id: int, line: str, name: str, revision=None):
    def work():
        with Session(engine) as session:
            update_utterance_speaker(
                session,
                recording_id=recording_id,
                utterance_public_id=line,
                new_speaker_name=name,
                expected_revision=revision,
            )
            session.commit()

    return work


@pytest.mark.parametrize("stale_revision", [True, False])
def test_a_person_editing_a_line_being_labelled_waits_and_sees_the_label(
    postgres_test_url: str, stale_revision: bool
) -> None:
    with postgres_engine(postgres_test_url) as engine:
        recording_id = _live_lines(engine)
        with Session(engine) as labels:
            apply_live_speaker_labels(
                labels,
                recording_id=recording_id,
                settled_ms=0,
                decide=_everyone("LIVE_PROVISIONAL_1"),
            )
            # The client last saw revision 1; the label is revision 2.
            thread, outcome = _in_thread(
                _rename(
                    engine,
                    recording_id,
                    "pg-a",
                    "Ada",
                    revision=1 if stale_revision else None,
                )
            )
            thread.join(1.0)
            assert thread.is_alive()
            labels.commit()
        thread.join(10.0)

        projection = _projection(engine, recording_id)

    if stale_revision:
        assert "revision conflict" in str(outcome["error"])
        assert projection["pg-a"] == "LIVE_PROVISIONAL_1"
    else:
        assert "error" not in outcome
        assert projection["pg-a"].startswith("MANUAL_")
    # The person's edit wrote the projection from a copy that has the labels.
    assert projection["pg-b"] == projection["pg-c"] == "LIVE_PROVISIONAL_1"


def test_a_label_run_neither_waits_on_nor_overwrites_a_persons_edit(
    postgres_test_url: str,
) -> None:
    with postgres_engine(postgres_test_url) as engine:
        recording_id = _live_lines(engine)
        with Session(engine) as person:
            update_utterance_speaker(
                person,
                recording_id=recording_id,
                utterance_public_id="pg-c",
                new_speaker_name="Cy",
            )
            person.flush()
            # The person holds pg-c and the projection; the label run waits
            # for the projection only, then skips nothing it should label.
            thread, outcome = _in_thread(
                lambda: _label_and_commit(engine, recording_id)
            )
            thread.join(1.0)
            assert thread.is_alive()
            person.commit()
        thread.join(10.0)

        projection = _projection(engine, recording_id)

    assert outcome["value"] == 2
    assert projection["pg-a"] == projection["pg-b"] == "LIVE_PROVISIONAL_1"
    assert projection["pg-c"].startswith("MANUAL_")


def _label_and_commit(engine, recording_id: int) -> int:
    with Session(engine) as session:
        write = apply_live_speaker_labels(
            session,
            recording_id=recording_id,
            settled_ms=0,
            decide=_everyone("LIVE_PROVISIONAL_1"),
        )
        session.commit()
        return write.changed


def test_a_projection_written_from_an_old_copy_is_put_right_on_the_next_run(
    postgres_test_url: str,
) -> None:
    with postgres_engine(postgres_test_url) as engine:
        recording_id = _live_lines(engine)
        _label_and_commit(engine, recording_id)
        with Session(engine) as session:
            # An edit elsewhere wrote the projection from a copy without labels.
            transcript = session.execute(
                select(Transcript).where(Transcript.recording_id == recording_id)
            ).scalar_one()
            transcript.segments = [
                {**segment, "speaker": "UNKNOWN"} for segment in transcript.segments
            ]
            session.commit()

        assert _label_and_commit(engine, recording_id) == 0
        projection = _projection(engine, recording_id)

    assert set(projection.values()) == {"LIVE_PROVISIONAL_1"}


def test_a_label_run_skips_a_line_a_person_holds_instead_of_waiting(
    postgres_test_url: str,
) -> None:
    # Waiting would hold the transcript while the person's edit, which locks
    # its line first and the transcript after, waits on it: a deadlock.
    with postgres_engine(postgres_test_url) as engine:
        recording_id = _live_lines(engine)
        with Session(engine) as person:
            person.execute(
                text(
                    "SELECT id FROM transcript_utterances "
                    "WHERE public_id = 'pg-b' FOR UPDATE"
                )
            )
            thread, outcome = _in_thread(
                lambda: _label_and_commit(engine, recording_id)
            )
            thread.join(5.0)
            assert not thread.is_alive()
            person.rollback()

        projection = _projection(engine, recording_id)

    assert outcome["value"] == 2
    assert projection == {
        "pg-a": "LIVE_PROVISIONAL_1",
        "pg-b": "UNKNOWN",
        "pg-c": "LIVE_PROVISIONAL_1",
    }
