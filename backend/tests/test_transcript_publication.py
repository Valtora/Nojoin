"""Publication counters follow real transactions, independently of audit cursors."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event, func, text
from sqlmodel import Session, select

from backend.core.db import driver_urls
from backend.models.pipeline import ProcessingRunKind, TranscriptUtteranceEvent
from backend.models.recording import Recording, RecordingStatus
from backend.models.recording_public import serialize_transcript
from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
from backend.models.transcript import Transcript
from backend.tests import test_canonical_transcript_phase1 as schemas
from backend.tests.postgres_setup import fresh_schema
from backend.utils.canonical_pipeline import (
    append_utterances_from_segments,
    get_canonical_transcript_revision,
    refresh_transcript_projection_from_canonical,
    serialize_canonical_utterances,
    update_recording_speaker_identity,
    update_utterance_text,
)
from backend.utils.canonical_pipeline.projection import serialize_canonical_delta

SCHEMA = "transcript_publication_test"


@pytest.fixture(params=["sqlite", "postgres"])
def publication_engine(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[Engine]:
    """Run identical transaction semantics on SQLite and the worker PG driver."""
    if request.param == "postgres":
        url = request.getfixturevalue("postgres_test_url")
        engine = create_engine(
            driver_urls(url)[1],
            connect_args={"options": f"-csearch_path={SCHEMA},public"},
        )
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        fresh_schema(engine, SCHEMA)
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'publication.db'}")
        with engine.begin() as connection:
            for name, schema in vars(schemas).items():
                if name.endswith("_SCHEMA"):
                    connection.execute(text(schema))
    try:
        yield engine
    finally:
        engine.dispose()


def _recording(session: Session) -> int:
    recording = Recording(
        name="Capture",
        audio_path="/tmp/publication.wav",
        status=RecordingStatus.UPLOADING,
    )
    session.add(recording)
    session.flush()
    assert recording.id is not None
    session.add(Transcript(recording_id=recording.id, text="", segments=[]))
    session.commit()
    return recording.id


def _transcript(session: Session, recording_id: int) -> Transcript:
    return session.exec(
        select(Transcript).where(Transcript.recording_id == recording_id)
    ).one()


def _append(
    session: Session, recording_id: int, *, prefix: str = "line", count: int = 3
) -> list[Any]:
    return append_utterances_from_segments(
        session,
        recording_id=recording_id,
        segments=[
            {
                "id": f"{prefix}-{index}",
                "start": float(index),
                "end": float(index + 1),
                "text": f"{prefix} {index}",
                "speaker": "UNKNOWN",
                "segment_source": "live",
            }
            for index in range(count)
        ],
        run_kind=ProcessingRunKind.LIVE,
        source="live",
        idempotency_key=prefix,
    )


def test_many_rows_and_flushes_publish_once_and_reads_do_not(
    publication_engine: Engine,
) -> None:
    with Session(publication_engine) as session:
        recording_id = _recording(session)
        baseline = _transcript(session, recording_id).change_sequence
        rows = _append(session, recording_id)
        update_utterance_text(
            session,
            recording_id=recording_id,
            utterance_public_id=rows[0].public_id,
            text="Edited",
        )
        session.commit()
        assert _transcript(session, recording_id).change_sequence == baseline + 1
        revision = get_canonical_transcript_revision(session, recording_id)
        assert revision > baseline + 1
        assert len(serialize_canonical_utterances(session, recording_id)) == 3
        serialize_canonical_delta(session, recording_id, after_revision=revision)
        refresh_transcript_projection_from_canonical(session, recording_id)
        session.commit()
        assert _transcript(session, recording_id).change_sequence == baseline + 1

        _append(session, recording_id, prefix="next")
        session.commit()
        assert _transcript(session, recording_id).change_sequence == baseline + 2


def test_name_only_publish_leaves_events_and_analytics_watermark_alone(
    publication_engine: Engine,
) -> None:
    with Session(publication_engine) as session:
        recording_id = _recording(session)
        _append(session, recording_id)
        session.add(
            RecordingSpeaker(
                recording_id=recording_id,
                diarization_label="SPEAKER_00",
                name="Speaker 1",
            )
        )
        session.commit()
        baseline = _transcript(session, recording_id).change_sequence
        revision = get_canonical_transcript_revision(session, recording_id)
        count = session.exec(
            select(func.count()).select_from(TranscriptUtteranceEvent)
        ).one()
        update_recording_speaker_identity(
            session,
            recording_id=recording_id,
            diarization_label="SPEAKER_00",
            new_speaker_name="Mike",
        )
        session.commit()
        assert _transcript(session, recording_id).change_sequence == baseline + 1
        assert get_canonical_transcript_revision(session, recording_id) == revision
        assert (
            session.exec(
                select(func.count()).select_from(TranscriptUtteranceEvent)
            ).one()
            == count
        )


@pytest.mark.parametrize("commit_inner", [False, True])
def test_savepoint_then_outer_write_counts_only_surviving_publication(
    publication_engine: Engine, commit_inner: bool
) -> None:
    with Session(publication_engine) as session:
        recording_id = _recording(session)
        transcript = _transcript(session, recording_id)
        baseline = transcript.change_sequence
        nested = session.begin_nested()
        transcript.text = "Inner result"
        session.flush()
        assert transcript.change_sequence == baseline + 1
        if commit_inner:
            nested.commit()
        else:
            nested.rollback()
        transcript.text = "Outer result"
        session.flush()
        session.commit()
        assert _transcript(session, recording_id).change_sequence == baseline + 1
        assert _transcript(session, recording_id).text == "Outer result"


def test_rows_events_projection_and_sequence_rollback_together(
    publication_engine: Engine,
) -> None:
    with Session(publication_engine) as session:
        recording_id = _recording(session)
        baseline = _transcript(session, recording_id).change_sequence
        with pytest.raises(RuntimeError, match="abort publication"):
            with session.begin_nested():
                _append(session, recording_id)
                transcript = _transcript(session, recording_id)
                transcript.settling_state = {"generation": "discarded", "sealed": False}
                session.flush()
                assert transcript.change_sequence == baseline + 1
                raise RuntimeError("abort publication")
        session.commit()
        transcript = _transcript(session, recording_id)
        assert (
            transcript.text,
            transcript.segments,
            transcript.settling_state,
            transcript.change_sequence,
        ) == ("", [], None, baseline)
        assert get_canonical_transcript_revision(session, recording_id) == 0
        assert serialize_canonical_utterances(session, recording_id) == []
        # Retry in the same Session after rollback must still publish.
        _append(session, recording_id)
        session.commit()
        assert _transcript(session, recording_id).change_sequence == baseline + 1


def test_status_and_inherited_person_name_share_the_publication_boundary(
    publication_engine: Engine,
) -> None:
    with Session(publication_engine) as session:
        recording_id = _recording(session)
        person = GlobalSpeaker(name="Old name")
        session.add(person)
        session.flush()
        session.add(
            RecordingSpeaker(
                recording_id=recording_id,
                diarization_label="SPEAKER_00",
                global_speaker_id=person.id,
            )
        )
        session.commit()
        baseline = _transcript(session, recording_id).change_sequence
        person.name = "New name"
        session.flush()
        transcript = _transcript(session, recording_id)
        transcript.fail_transcription("ASR failed")
        session.commit()
        assert _transcript(session, recording_id).change_sequence == baseline + 1
        assert get_canonical_transcript_revision(session, recording_id) == 0


def test_private_runtime_updates_and_noop_status_do_not_publish(
    publication_engine: Engine,
) -> None:
    with Session(publication_engine) as session:
        recording_id = _recording(session)
        rows = _append(session, recording_id)
        session.commit()
        transcript = _transcript(session, recording_id)
        baseline = transcript.change_sequence
        transcript.settling_state = {"generation": "runtime", "sealed": False}
        transcript.transcript_status = transcript.transcript_status
        rows[0].confidence_payload = {"private_comparison_count": 2}
        refresh_transcript_projection_from_canonical(session, recording_id)
        session.commit()
        assert _transcript(session, recording_id).change_sequence == baseline


def test_failed_root_transaction_can_retry_and_new_failure_reason_publishes(
    publication_engine: Engine,
) -> None:
    with Session(publication_engine) as session:
        recording_id = _recording(session)
        transcript = _transcript(session, recording_id)
        baseline = transcript.change_sequence
        transcript.fail_transcription("First failure")
        session.flush()
        assert transcript.change_sequence == baseline + 1
        session.rollback()
        assert _transcript(session, recording_id).change_sequence == baseline
        transcript.fail_transcription("Retried failure")
        session.commit()
        assert _transcript(session, recording_id).change_sequence == baseline + 1
        transcript.fail_transcription("Different failure")
        session.commit()
        assert _transcript(session, recording_id).change_sequence == baseline + 2
        assert get_canonical_transcript_revision(session, recording_id) == 0


def test_name_only_savepoint_rollback_restores_cached_counter(
    publication_engine: Engine,
) -> None:
    with Session(publication_engine, expire_on_commit=False) as session:
        recording_id = _recording(session)
        speaker = RecordingSpeaker(
            recording_id=recording_id, diarization_label="SPEAKER_00", name="Original"
        )
        session.add(speaker)
        session.commit()
        transcript = _transcript(session, recording_id)
        baseline = transcript.change_sequence
        savepoint = session.begin_nested()
        speaker.local_name = "Discarded"
        session.flush()
        assert transcript.change_sequence == baseline + 1
        savepoint.rollback()
        assert transcript.change_sequence == baseline
        session.commit()
        assert transcript.change_sequence == baseline
        speaker.local_name = "Accepted"
        session.commit()
        assert transcript.change_sequence == baseline + 1


@pytest.mark.parametrize("count", [3, 20])
def test_repeated_row_flushes_do_not_repeat_the_publication_fence(
    publication_engine: Engine,
    count: int,
) -> None:
    if publication_engine.dialect.name != "postgresql":
        pytest.skip("SQLite has no FOR UPDATE")
    statements: list[str] = []

    def capture(**kwargs: Any) -> None:
        statement = kwargs["statement"]
        if "FROM transcripts" in statement and "FOR UPDATE" in statement:
            statements.append(statement)

    with Session(publication_engine) as session:
        recording_id = _recording(session)
        event.listen(publication_engine, "before_cursor_execute", capture, named=True)
        try:
            _append(session, recording_id, count=count)
            session.commit()
        finally:
            event.remove(publication_engine, "before_cursor_execute", capture)
    # One entry fence, plus the sorted publication set and counter read.
    assert len(statements) == 3


def test_public_read_keeps_durable_evidence_private_without_mutating_projection() -> (
    None
):
    evidence = {
        "asr_segments": [{"words": [{"word": "Hello", "start_ms": 0, "end_ms": 500}]}]
    }
    segment = {
        "id": "line",
        "text": "Hello",
        "start": 0.0,
        "end": 0.5,
        "confidence_payload": evidence,
    }
    transcript = Transcript(id=1, recording_id=1, text="Hello", segments=[segment])
    public = serialize_transcript(transcript, recording_public_id="recording")
    assert "confidence_payload" not in public.segments[0]
    assert public.segments[0]["text"] == "Hello"
    assert transcript.segments[0]["confidence_payload"] == evidence
