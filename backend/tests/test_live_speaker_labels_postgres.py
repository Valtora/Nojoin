"""Live label writes against finalize and against a person's edits.

The label write holds the recording row, so finalize cannot end a capture under
it, and the transcript, so the projection it writes is current. Row locks need
a real server: SQLite has none. Skips without NOJOIN_TEST_POSTGRES_URL, like
the other Postgres tests.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from backend.models.pipeline import (
    ProcessingRunKind,
    RecordingAudioChunk,
    SpeakerCorrectionScope,
    TranscriptUtterance,
    TranscriptUtteranceState,
)
from backend.models.recording import RecordingStatus
from backend.models.transcript import Transcript
from backend.processing.live_diarization import step
from backend.processing.live_diarization.step import _recording_in_flight
from backend.tests.storage_hygiene_harness import add_recording, postgres_engine
from backend.utils.canonical_pipeline import (
    append_utterances_from_segments,
    apply_compatibility_segment_replace,
    list_active_utterances,
    update_utterance_speaker,
    update_utterance_text,
)
from backend.utils.canonical_pipeline.live_labels import (
    LiveLabelDecision,
    apply_live_speaker_labels,
)
from backend.utils.canonical_pipeline.segmentation import (
    _speaker_state_for_utterance,
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


def _locked_everyone(start_ms: int, end_ms: int) -> LiveLabelDecision:
    return LiveLabelDecision("LIVE_PROVISIONAL_1", locked=True, share=1.0)


def _label_run(engine, recording_id: int, settled_ms: int):
    with Session(engine) as session:
        write = apply_live_speaker_labels(
            session,
            recording_id=recording_id,
            settled_ms=settled_ms,
            decide=_locked_everyone,
        )
        session.commit()
        return write


def _rows(engine, recording_id: int) -> dict[str, str]:
    with Session(engine) as session:
        return {
            row.public_id: row.speaker_label
            for row in session.execute(
                select(TranscriptUtterance).where(
                    TranscriptUtterance.recording_id == recording_id
                )
            ).scalars()
        }


def _projection_fields(engine, recording_id: int) -> dict[str, tuple]:
    with Session(engine) as session:
        transcript = session.execute(
            select(Transcript).where(Transcript.recording_id == recording_id)
        ).scalar_one()
        return {
            segment["id"]: (
                segment["speaker"],
                segment["speaker_state"],
                segment["revision"],
            )
            for segment in transcript.segments
        }


def _row_fields(engine, recording_id: int) -> dict[str, tuple]:
    with Session(engine) as session:
        return {
            row.public_id: (
                row.speaker_label,
                _speaker_state_for_utterance(row),
                row.revision,
            )
            for row in session.execute(
                select(TranscriptUtterance).where(
                    TranscriptUtterance.recording_id == recording_id
                )
            ).scalars()
        }


def test_a_skipped_line_stays_open_and_is_labelled_on_a_later_run(
    postgres_test_url: str,
) -> None:
    with postgres_engine(postgres_test_url) as engine:
        recording_id = _live_lines(engine)
        with Session(engine) as person:
            person.execute(
                text(
                    "SELECT id FROM transcript_utterances "
                    "WHERE public_id = 'pg-b' FOR UPDATE"
                )
            )
            first = _label_run(engine, recording_id, 0)
            person.rollback()
        # The step saves settled_ms into the engine for the next run.
        _label_run(engine, recording_id, first.settled_ms)
        rows = _rows(engine, recording_id)

    # The settled prefix stops at the skipped line (pg-b, 10-14 s).
    assert first.settled_ms == 9000
    assert first.open_from_s == 10.0
    assert set(rows.values()) == {"LIVE_PROVISIONAL_1"}


def _writer(engine, recording_id: int, kind: str):
    with Session(engine) as session:
        transcript = session.execute(
            select(Transcript).where(Transcript.recording_id == recording_id)
        ).scalar_one()
        client_copy = [dict(segment) for segment in transcript.segments]

    def write(session) -> None:
        if kind == "rename":
            update_utterance_speaker(
                session,
                recording_id=recording_id,
                utterance_public_id="pg-c",
                new_speaker_name="Cy",
            )
        elif kind == "text":
            update_utterance_text(
                session,
                recording_id=recording_id,
                utterance_public_id="pg-c",
                text="edited",
            )
        else:
            segments = [
                {**segment, "text": "replaced"} if segment["id"] == "pg-c" else segment
                for segment in client_copy
            ]
            apply_compatibility_segment_replace(
                session, recording_id=recording_id, segments=segments
            )

    return write


@pytest.mark.parametrize("kind", ["rename", "text", "segments"])
def test_a_projection_an_edit_wrote_over_settled_lines_is_rebuilt(
    postgres_test_url: str, kind: str
) -> None:
    # The edit holds pg-c and read the projection before the labels landed;
    # the label run commits just before the edit writes that old copy back.
    with postgres_engine(postgres_test_url) as engine:
        recording_id = _live_lines(engine)
        write = _writer(engine, recording_id, kind)
        runs: list = []

        def label_first(conn, cursor, statement, *rest):
            if runs or not statement.startswith("UPDATE transcripts "):
                return
            thread, outcome = _in_thread(lambda: _label_run(engine, recording_id, 0))
            runs.append(outcome)
            thread.join(10.0)

        event.listen(engine, "before_cursor_execute", label_first)
        try:
            with Session(engine) as person:
                write(person)
                person.commit()
        finally:
            event.remove(engine, "before_cursor_execute", label_first)
        first = runs[0]["value"]
        assert _projection(engine, recording_id)["pg-a"] == "UNKNOWN"

        _label_run(engine, recording_id, first.settled_ms)
        projection = _projection_fields(engine, recording_id)
        rows = _row_fields(engine, recording_id)

    # pg-a and pg-b settled on the first run, so the second reads only pg-c.
    assert first.settled_ms == 14000
    assert projection == rows
    assert projection["pg-a"][:2] == ("LIVE_PROVISIONAL_1", "stable")
    assert projection["pg-b"][:2] == ("LIVE_PROVISIONAL_1", "stable")


@pytest.mark.parametrize("stale_revision", [True, False])
def test_a_text_edit_waits_for_a_label_and_checks_its_revision_after(
    postgres_test_url: str, stale_revision: bool
) -> None:
    with postgres_engine(postgres_test_url) as engine:
        recording_id = _live_lines(engine)

        def edit():
            with Session(engine) as session:
                update_utterance_text(
                    session,
                    recording_id=recording_id,
                    utterance_public_id="pg-a",
                    text="edited",
                    expected_revision=1 if stale_revision else None,
                )
                session.commit()

        with Session(engine) as labels:
            apply_live_speaker_labels(
                labels,
                recording_id=recording_id,
                settled_ms=0,
                decide=_everyone("LIVE_PROVISIONAL_1"),
            )
            thread, outcome = _in_thread(edit)
            thread.join(1.0)
            assert thread.is_alive()
            labels.commit()
        thread.join(10.0)
        with Session(engine) as session:
            line = session.execute(
                select(TranscriptUtterance).where(
                    TranscriptUtterance.recording_id == recording_id,
                    TranscriptUtterance.public_id == "pg-a",
                )
            ).scalar_one()

    assert line.speaker_label == "LIVE_PROVISIONAL_1"
    if stale_revision:
        assert "revision conflict" in str(outcome["error"])
        assert (line.text, line.revision) == ("opening remarks", 2)
    else:
        assert "error" not in outcome
        # The label's revision is counted, not written over.
        assert (line.text, line.revision) == ("edited", 3)


def test_a_stale_revision_conflicts_when_the_session_already_read_the_line(
    postgres_test_url: str,
) -> None:
    # The route lists the lines before the edit and holds them, so the line
    # is in the session already; the locked read must refresh it.
    with postgres_engine(postgres_test_url) as engine:
        recording_id = _live_lines(engine)
        with Session(engine) as person:
            held = list_active_utterances(person, recording_id)
            _label_and_commit(engine, recording_id)
            with pytest.raises(RuntimeError, match="revision conflict"):
                update_utterance_speaker(
                    person,
                    recording_id=recording_id,
                    utterance_public_id="pg-a",
                    new_speaker_name="Ada",
                    expected_revision=1,
                )
            # The locked read refreshed the held copy of the line.
            assert {line.public_id: line.revision for line in held}["pg-a"] == 2
            person.rollback()


class _BlockedTimeline:
    """A line decision that waits, so the label write stays open."""

    entered = threading.Event()
    release = threading.Event()

    @classmethod
    def from_engine(cls, engine):
        return cls()

    def label_line(self, start_s: float, end_s: float) -> LiveLabelDecision:
        self.entered.set()
        self.release.wait(10.0)
        return LiveLabelDecision("LIVE_PROVISIONAL_1", locked=False, share=1.0)


def test_finalize_waits_for_the_label_steps_write(
    postgres_test_url: str, monkeypatch
) -> None:
    import backend.core.db as core_db

    with postgres_engine(postgres_test_url) as engine:
        recording_id = _live_lines(engine)
        monkeypatch.setattr(core_db, "get_sync_session", lambda: Session(engine))
        monkeypatch.setattr(step, "LabelTimeline", _BlockedTimeline)
        _BlockedTimeline.entered.clear()
        _BlockedTimeline.release.clear()
        thread, outcome = _in_thread(
            lambda: step._write_labels(recording_id, SimpleNamespace(settled_ms=0))
        )
        try:
            assert _BlockedTimeline.entered.wait(5.0)
            with Session(engine) as finalize:
                finalize.execute(text("SET lock_timeout = '200ms'"))
                with pytest.raises(OperationalError, match="lock timeout"):
                    finalize.execute(
                        text("UPDATE recordings SET status = 'QUEUED' WHERE id = :id"),
                        {"id": recording_id},
                    )
                finalize.rollback()
        finally:
            _BlockedTimeline.release.set()
            thread.join(10.0)

    assert outcome["value"].changed == 3


def test_the_label_writes_lock_lets_audio_chunks_in(postgres_test_url: str) -> None:
    # FOR NO KEY UPDATE: a row that references the recording is not held up
    # behind a label write, while a status change still is.
    with postgres_engine(postgres_test_url) as engine:
        recording_id = add_recording(
            engine, "capture.wav", status=RecordingStatus.UPLOADING
        )
        with Session(engine) as labels, Session(engine) as upload:
            assert _recording_in_flight(labels, recording_id, lock=True)
            upload.execute(text("SET lock_timeout = '200ms'"))
            upload.add(
                RecordingAudioChunk(
                    recording_id=recording_id,
                    sequence_no=0,
                    absolute_start_ms=0,
                    absolute_end_ms=1000,
                    duration_ms=1000,
                    sample_rate_hz=48000,
                    channel_count=1,
                    byte_size=4,
                    sha256="0" * 64,
                    storage_path="chunk-0",
                )
            )
            upload.commit()
            upload.execute(text("SET lock_timeout = '200ms'"))
            with pytest.raises(OperationalError, match="lock timeout"):
                upload.execute(
                    text("UPDATE recordings SET status = 'QUEUED' WHERE id = :id"),
                    {"id": recording_id},
                )
            upload.rollback()
            labels.rollback()


def test_search_index_chunks_show_live_speakers_as_speaker_n(
    postgres_test_url: str, monkeypatch
) -> None:
    import backend.processing.text_embedding as text_embedding
    from backend.models.context_chunk import TEXT_EMBEDDING_DIMENSIONS, ContextChunk
    from backend.worker.tasks import index_transcript_task

    class Embeddings:
        def embed(self, texts):
            return [[0.0] * TEXT_EMBEDDING_DIMENSIONS for _ in texts]

    monkeypatch.setattr(text_embedding, "get_text_embedding_service", Embeddings)
    with postgres_engine(postgres_test_url) as engine:
        recording_id = _live_lines(engine)
        _label_and_commit(engine, recording_id)
        with Session(engine) as session:
            monkeypatch.setattr(index_transcript_task, "_session", session)
            index_transcript_task.run(recording_id)
            chunks = session.execute(select(ContextChunk)).scalars().all()

    assert [chunk.meta["speakers"] for chunk in chunks] == [["Speaker 1"]]
    assert chunks[0].content.startswith("Speaker 1: opening remarks\n")


def test_the_transcript_is_locked_before_the_lines_are_read(
    postgres_test_url: str,
) -> None:
    # The lines read and the projection written must be as of the lock, or an
    # edit committing in between leaves the projection behind its rows.
    with postgres_engine(postgres_test_url) as engine:
        recording_id = _live_lines(engine)
        statements: list[str] = []

        def record(conn, cursor, statement, *rest):
            if "FOR UPDATE" in statement or "transcript_utterances" in statement:
                statements.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            _label_and_commit(engine, recording_id)
        finally:
            event.remove(engine, "before_cursor_execute", record)

    reads = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert "FROM transcripts" in reads[0] and "FOR UPDATE" in reads[0]
    assert "SKIP LOCKED" in reads[1]
    assert "transcript_utterances" in reads[2] and "FOR UPDATE" not in reads[2]


def test_two_label_writes_for_one_recording_take_turns(postgres_test_url: str) -> None:
    # The flock serialises label steps on one host only; the row lock holds
    # wherever the second one runs.
    with postgres_engine(postgres_test_url) as engine:
        recording_id = add_recording(
            engine, "capture.wav", status=RecordingStatus.UPLOADING
        )
        with Session(engine) as first, Session(engine) as second:
            assert _recording_in_flight(first, recording_id, lock=True)
            second.execute(text("SET lock_timeout = '200ms'"))
            with pytest.raises(OperationalError, match="lock timeout"):
                _recording_in_flight(second, recording_id, lock=True)
            second.rollback()
            first.rollback()


def _decide(locked_until_ms: int):
    def decide(start_ms: int, end_ms: int) -> LiveLabelDecision:
        return LiveLabelDecision(
            "LIVE_PROVISIONAL_1", locked=end_ms <= locked_until_ms, share=1.0
        )

    return decide


def test_a_scoped_rename_racing_a_label_write_keeps_revisions_increasing(
    postgres_test_url: str,
) -> None:
    with postgres_engine(postgres_test_url) as engine:
        recording_id = _live_lines(engine)
        # Run 1 labels every line and settles pg-a (ends 9 s).
        with Session(engine) as session:
            first = apply_live_speaker_labels(
                session, recording_id=recording_id, settled_ms=0, decide=_decide(9000)
            )
            session.commit()
        before = _row_fields(engine, recording_id)
        with Session(engine) as labels:
            # Run 2 locks pg-b and pg-c, a revision bump each, not committed yet.
            apply_live_speaker_labels(
                labels,
                recording_id=recording_id,
                settled_ms=first.settled_ms,
                decide=_decide(10**9),
            )

            def rename() -> None:
                with Session(engine) as person:
                    # As the route does: the lines are in the session already.
                    listed = list_active_utterances(person, recording_id)
                    update_utterance_speaker(
                        person,
                        recording_id=recording_id,
                        utterance_public_id="pg-a",
                        new_speaker_name="Ada",
                        scope=SpeakerCorrectionScope.SPEAKER_EVERYWHERE_IN_RECORDING,
                    )
                    person.commit()
                    return len(listed)

            thread, outcome = _in_thread(rename)
            thread.join(1.0)
            waited = thread.is_alive()
            labels.commit()
        thread.join(10.0)
        after = _row_fields(engine, recording_id)

    assert waited and outcome["value"] == 3
    for line in ("pg-b", "pg-c"):
        # The label's bump, then the rename's: neither is written over.
        assert after[line][2] == before[line][2] + 2, (line, before[line], after[line])
    assert len({after[line][0] for line in after}) == 1


def test_two_corrections_to_every_line_of_a_speaker_take_turns(
    postgres_test_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Each locks the speaker's lines in id order. Had each locked its own line
    # first, two on different lines would each hold a line the other waits for.
    from backend.utils.canonical_pipeline import core

    with postgres_engine(postgres_test_url) as engine:
        recording_id = _live_lines(engine)
        _label_and_commit(engine, recording_id)
        both_there = threading.Barrier(2, timeout=2.0)
        select_lines = core._select_utterances_for_scope

        def after_both_anchors(*args, **kwargs):
            try:
                both_there.wait()
            except threading.BrokenBarrierError:
                pass  # the other is waiting for this one's locks
            return select_lines(*args, **kwargs)

        monkeypatch.setattr(core, "_select_utterances_for_scope", after_both_anchors)

        def everywhere(line: str, name: str):
            outcome: dict = {}

            def work() -> None:
                try:
                    with Session(engine) as person:
                        update_utterance_speaker(
                            person,
                            recording_id=recording_id,
                            utterance_public_id=line,
                            new_speaker_name=name,
                            scope=SpeakerCorrectionScope.SPEAKER_EVERYWHERE_IN_RECORDING,
                        )
                        person.commit()
                        outcome["done"] = True
                except OperationalError as exc:  # a deadlock, reported below
                    outcome["error"] = exc

            thread = threading.Thread(target=work)
            thread.start()
            return thread, outcome

        runs = [everywhere("pg-c", "Ada"), everywhere("pg-a", "Bo")]
        for thread, _ in runs:
            thread.join(15.0)
        projection = _projection(engine, recording_id)

    assert [outcome for _, outcome in runs] == [{"done": True}, {"done": True}]
    assert len(set(projection.values())) == 1
