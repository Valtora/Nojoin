"""Live speaker labels written to canonical utterances, read back, and finalised."""

from __future__ import annotations

import fcntl
import io
import json
import threading
import time
import zipfile

import numpy as np
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, select

from backend.models.pipeline import (
    ProcessingRunKind,
    TranscriptUtterance,
    TranscriptUtteranceState,
)
from backend.models.recording import Recording, RecordingStatus
from backend.processing.live_diarization import step
from backend.processing.live_diarization.files import (
    BANK_FILENAME,
    LOCK_FILENAME,
    STATE_FILENAME,
    remove_live_label_state,
)
from backend.tests.live_label_fakes import SAMPLE_RATE, SpeakerTrackModel, speaker_audio
from backend.tests.sqlite_schemas import (
    GLOBAL_SPEAKERS_SCHEMA,
    PROCESSING_RUNS_SCHEMA,
    RECORDING_SPEAKER_ALIASES_SCHEMA,
    RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA,
    RECORDING_SPEAKERS_SCHEMA,
    RECORDINGS_SCHEMA,
    SPEAKER_CORRECTION_EVENTS_SCHEMA,
    TIMELINE_NOTES_SCHEMA,
    TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA,
    TRANSCRIPT_UTTERANCES_SCHEMA,
    TRANSCRIPTS_SCHEMA,
)
from backend.utils.canonical_pipeline import (
    append_utterances_from_segments,
    apply_compatibility_segment_replace,
    finalize_utterances_from_segments,
    get_canonical_transcript_revision,
    serialize_canonical_delta,
    serialize_canonical_utterances,
    update_utterance_speaker,
)
from backend.utils.canonical_pipeline.live_labels import (
    LiveLabelDecision,
    apply_live_speaker_labels,
)

RECORDING_ID = 1
LINES = [
    {"id": "line-a", "start": 1.0, "end": 9.0, "text": "opening remarks"},
    {"id": "line-b", "start": 10.0, "end": 14.0, "text": "a short reply"},
    {"id": "line-c", "start": 21.0, "end": 29.0, "text": "a second voice"},
]


def _database():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    with engine.begin() as conn:
        for ddl in (
            RECORDINGS_SCHEMA,
            TRANSCRIPTS_SCHEMA,
            GLOBAL_SPEAKERS_SCHEMA,
            RECORDING_SPEAKERS_SCHEMA,
            RECORDING_SPEAKER_DRIFT_REFUSALS_SCHEMA,
            PROCESSING_RUNS_SCHEMA,
            TRANSCRIPT_UTTERANCES_SCHEMA,
            TRANSCRIPT_UTTERANCE_EVENTS_SCHEMA,
            RECORDING_SPEAKER_ALIASES_SCHEMA,
            SPEAKER_CORRECTION_EVENTS_SCHEMA,
            TIMELINE_NOTES_SCHEMA,
        ):
            conn.execute(text(ddl))
        conn.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, status, upload_progress, processing_progress,
                    pipeline_generation, is_archived, is_deleted, user_id
                ) VALUES (
                    1, :now, :now, 'Live meeting', 'live-rec', 'meeting-uid-live',
                    '/tmp/live.wav', 'UPLOADING', 50, 10, 'unified', 0, 0, 1
                )
                """
            ),
            {"now": "2026-10-21 00:00:00"},
        )
        conn.execute(
            text(
                """
                INSERT INTO transcripts (
                    id, created_at, updated_at, recording_id, text, segments,
                    notes, meeting_edge_status, notes_status, transcript_status
                ) VALUES (1, :now, :now, 1, '', '[]', NULL, 'idle', 'pending', 'processing')
                """
            ),
            {"now": "2026-10-21 00:00:00"},
        )
    with Session(engine) as session:
        append_utterances_from_segments(
            session,
            recording_id=RECORDING_ID,
            segments=[
                {
                    **line,
                    "speaker": "UNKNOWN",
                    "provisional": True,
                    "segment_source": "live",
                    "speaker_state": "provisional",
                }
                for line in LINES
            ],
            run_kind=ProcessingRunKind.LIVE,
            source="live",
            state_override=TranscriptUtteranceState.PROVISIONAL,
            trigger_source="worker",
        )
        session.commit()
    return engine


def _decisions(table: dict[int, LiveLabelDecision], calls: list[int] | None = None):
    def decide(start_ms: int, end_ms: int) -> LiveLabelDecision:
        if calls is not None:
            calls.append(start_ms)
        return table.get(
            start_ms, LiveLabelDecision("UNKNOWN", locked=False, share=None)
        )

    return decide


def _apply(engine, table, settled_ms=0, calls=None):
    with Session(engine) as session:
        write = apply_live_speaker_labels(
            session,
            recording_id=RECORDING_ID,
            settled_ms=settled_ms,
            decide=_decisions(table, calls),
        )
        session.commit()
    return write


def _by_id(payloads):
    return {payload["id"]: payload for payload in payloads}


def test_a_label_is_an_utterance_event_that_the_live_read_returns():
    engine = _database()
    with Session(engine) as session:
        before = get_canonical_transcript_revision(session, RECORDING_ID)

    _apply(
        engine, {1000: LiveLabelDecision("LIVE_PROVISIONAL_2", locked=False, share=0.9)}
    )

    with Session(engine) as session:
        _, changed, tombstones = serialize_canonical_delta(
            session, RECORDING_ID, after_revision=before
        )
        event_types = (
            session.execute(
                text(
                    "SELECT event_type FROM transcript_utterance_events WHERE id > :rev"
                ),
                {"rev": before},
            )
            .scalars()
            .all()
        )
    assert [u["id"] for u in changed] == ["line-a"]
    assert changed[0]["speaker"] == "LIVE_PROVISIONAL_2"
    assert changed[0]["speaker_state"] == "provisional"
    assert changed[0]["recording_speaker_id"] is None
    assert tombstones == []
    assert event_types == ["live_speaker_label"]

    _apply(
        engine, {1000: LiveLabelDecision("LIVE_PROVISIONAL_2", locked=True, share=0.9)}
    )
    with Session(engine) as session:
        lines = _by_id(serialize_canonical_utterances(session, RECORDING_ID))
        projection = json.loads(
            session.execute(text("SELECT segments FROM transcripts")).scalar_one()
        )
    assert lines["line-a"]["speaker_state"] == "stable"
    assert lines["line-a"]["revision"] == 3
    assert lines["line-b"]["speaker"] == "UNKNOWN"
    assert {s["id"]: s["speaker"] for s in projection}["line-a"] == "LIVE_PROVISIONAL_2"


def test_an_unchanged_label_writes_nothing():
    engine = _database()
    table = {1000: LiveLabelDecision("LIVE_PROVISIONAL_1", locked=False, share=1.0)}
    first = _apply(engine, table)
    second = _apply(engine, table)

    assert (first.changed, second.changed) == (1, 0)


def test_a_line_a_person_named_is_never_relabelled():
    engine = _database()
    with Session(engine) as session:
        update_utterance_speaker(
            session,
            recording_id=RECORDING_ID,
            utterance_public_id="line-a",
            new_speaker_name="Ada",
        )
        session.commit()

    write = _apply(
        engine, {1000: LiveLabelDecision("LIVE_PROVISIONAL_1", locked=False, share=1.0)}
    )

    with Session(engine) as session:
        line = _by_id(serialize_canonical_utterances(session, RECORDING_ID))["line-a"]
    assert write.changed == 0
    assert line["speaker"].startswith("MANUAL_")
    assert line["speaker_state"] == "manual_override"


def test_a_live_line_the_final_pass_replaced_is_never_relabelled():
    engine = _database()
    with Session(engine) as session:
        line = session.exec(
            select(TranscriptUtterance).where(TranscriptUtterance.public_id == "line-b")
        ).one()
        line.state = TranscriptUtteranceState.SUPERSEDED
        session.add(line)
        session.commit()

    write = _apply(
        engine,
        {10000: LiveLabelDecision("LIVE_PROVISIONAL_3", locked=False, share=1.0)},
    )

    with Session(engine) as session:
        line = session.exec(
            select(TranscriptUtterance).where(TranscriptUtterance.public_id == "line-b")
        ).one()
    assert write.changed == 0
    assert line.speaker_label == "UNKNOWN"


def test_locked_lines_are_settled_and_not_decided_again():
    engine = _database()
    write = _apply(
        engine,
        {
            1000: LiveLabelDecision("LIVE_PROVISIONAL_1", locked=True, share=1.0),
            10000: LiveLabelDecision("UNKNOWN", locked=False, share=0.4),
            21000: LiveLabelDecision("LIVE_PROVISIONAL_2", locked=True, share=1.0),
        },
    )
    assert write.settled_ms == 9000
    assert write.open_from_s == 10.0

    calls: list[int] = []
    again = _apply(engine, {}, settled_ms=write.settled_ms, calls=calls)

    # line-a is behind the settled prefix and line-c carries its lock.
    assert calls == [10000]
    assert again.settled_ms == 9000
    with Session(engine) as session:
        lines = _by_id(serialize_canonical_utterances(session, RECORDING_ID))
    assert lines["line-c"]["speaker"] == "LIVE_PROVISIONAL_2"


FINAL_SEGMENTS = [
    # Same boundaries as line-a, so finalize matches that live row in place.
    {"start": 1.0, "end": 9.0, "speaker": "SPEAKER_00", "text": "Opening remarks."},
    {"start": 9.5, "end": 14.5, "speaker": "SPEAKER_01", "text": "A short reply."},
    {"start": 21.0, "end": 28.0, "speaker": "SPEAKER_00", "text": "A second voice."},
]
VOLATILE = {"id", "revision", "updated_at"}


def _finalised(engine):
    with Session(engine) as session:
        recording = session.get(Recording, RECORDING_ID)
        recording.status = RecordingStatus.PROCESSING
        session.add(recording)
        finalize_utterances_from_segments(
            session,
            recording_id=RECORDING_ID,
            segments=[dict(segment) for segment in FINAL_SEGMENTS],
            trigger_source="worker",
        )
        session.commit()
        lines = [
            {k: v for k, v in line.items() if k not in VOLATILE}
            for line in serialize_canonical_utterances(session, RECORDING_ID)
        ]
        speakers = (
            session.execute(
                text("SELECT diarization_label FROM recording_speakers ORDER BY id")
            )
            .scalars()
            .all()
        )
        active = session.execute(
            text(
                "SELECT COUNT(*) FROM transcript_utterances WHERE UPPER(state) != 'SUPERSEDED'"
            )
        ).scalar_one()
    return lines, speakers, active


def test_the_final_transcript_is_identical_with_labels_on_and_off():
    labels_off = _database()
    labels_on = _database()
    _apply(
        labels_on,
        {
            1000: LiveLabelDecision("LIVE_PROVISIONAL_1", locked=False, share=0.8),
            10000: LiveLabelDecision("LIVE_PROVISIONAL_2", locked=True, share=1.0),
            21000: LiveLabelDecision("LIVE_PROVISIONAL_1", locked=True, share=0.7),
        },
    )

    off_lines, off_speakers, off_active = _finalised(labels_off)
    on_lines, on_speakers, on_active = _finalised(labels_on)

    assert on_lines == off_lines
    assert on_speakers == off_speakers == ["SPEAKER_00", "SPEAKER_01"]
    assert on_active == off_active == 3
    assert [line["speaker_state"] for line in on_lines] == ["stable"] * 3
    assert not any(line["speaker"].startswith("LIVE_") for line in on_lines)


def _patch_step(monkeypatch, engine, model):
    import backend.core.db as core_db

    monkeypatch.setattr(core_db, "get_sync_session", lambda: Session(engine))
    monkeypatch.setattr(step, "live_speaker_labels_device", lambda: "cpu")
    monkeypatch.setattr(step, "_load_model", lambda device, config: model)


def _run(live_dir, audio, start_s, next_text_start_s):
    step.run_live_speaker_labels(
        step.LiveLabelRun(
            recording_id=RECORDING_ID,
            sequence=0,
            live_dir=live_dir,
            audio=audio,
            audio_start_s=start_s,
            next_text_start_s=next_text_start_s,
        )
    )


def test_the_label_step_labels_each_run_and_keeps_numbers_across_runs(
    monkeypatch, tmp_path
):
    engine = _database()
    _patch_step(monkeypatch, engine, SpeakerTrackModel())
    audio = speaker_audio([(0, 10, 1), (10, 14, 1), (14, 20, 1), (20, 32, 2)], 32)

    for start in range(0, 32, 2):
        chunk = audio[start * SAMPLE_RATE : (start + 2) * SAMPLE_RATE]
        _run(tmp_path, chunk, float(start), float(start + 2))

    with Session(engine) as session:
        lines = _by_id(serialize_canonical_utterances(session, RECORDING_ID))
    assert lines["line-a"]["speaker"] == "LIVE_PROVISIONAL_1"
    assert lines["line-b"]["speaker"] == "LIVE_PROVISIONAL_1"
    assert lines["line-c"]["speaker"] == "LIVE_PROVISIONAL_2"
    assert lines["line-a"]["speaker_state"] == "stable"
    assert lines["line-c"]["speaker_state"] == "provisional"
    assert (tmp_path / STATE_FILENAME).exists()


def test_the_label_step_writes_nothing_and_removes_its_state_once_capture_ends(
    monkeypatch, tmp_path
):
    engine = _database()
    model = SpeakerTrackModel()
    _patch_step(monkeypatch, engine, model)
    audio = speaker_audio([(0, 30, 1)], 30)
    _run(tmp_path, audio[: 4 * SAMPLE_RATE], 0.0, 4.0)
    assert (tmp_path / STATE_FILENAME).exists()

    def labels():
        with Session(engine) as session:
            return session.execute(
                text(
                    "SELECT public_id, speaker_label, revision FROM transcript_utterances"
                )
            ).all()

    before = labels()
    calls = model.calls

    def finalize_during_the_model_pass(windows):
        with engine.begin() as conn:
            conn.execute(text("UPDATE recordings SET status = 'QUEUED'"))
        return SpeakerTrackModel.__call__(model, windows)

    monkeypatch.setattr(
        step, "_load_model", lambda device, config: finalize_during_the_model_pass
    )
    _run(tmp_path, audio[4 * SAMPLE_RATE :], 4.0, 30.0)

    assert model.calls > calls
    assert labels() == before
    assert not (tmp_path / STATE_FILENAME).exists()
    assert not (tmp_path / BANK_FILENAME).exists()
    # The step held its lock file while it removed it; nothing is left.
    assert not (tmp_path / LOCK_FILENAME).exists()


def _hold_label_lock(live_dir):
    """The lock as another lane's label step for the recording holds it."""
    handle = open(live_dir / LOCK_FILENAME, "a")
    fcntl.flock(handle, fcntl.LOCK_EX)
    return handle


def test_a_label_step_waits_for_the_recordings_previous_one(monkeypatch, tmp_path):
    # Two GPU lanes can run consecutive live tasks at once, and the label step
    # runs after the lane state is saved, so two steps could load, feed and save
    # the same state together.
    engine = _database()
    model = SpeakerTrackModel()
    _patch_step(monkeypatch, engine, model)
    held = _hold_label_lock(tmp_path)
    threading.Timer(0.3, held.close).start()

    started = time.monotonic()
    _run(tmp_path, speaker_audio([(0, 4, 1)], 4), 0.0, 4.0)

    assert time.monotonic() - started >= 0.3
    assert model.calls == 1
    assert (tmp_path / STATE_FILENAME).exists()


def test_a_label_step_gives_up_on_a_lock_that_stays_taken(monkeypatch, tmp_path):
    engine = _database()
    model = SpeakerTrackModel()
    _patch_step(monkeypatch, engine, model)
    monkeypatch.setattr(step, "LOCK_WAIT_S", 0.2)
    held = _hold_label_lock(tmp_path)
    try:
        _run(tmp_path, speaker_audio([(0, 4, 1)], 4), 0.0, 4.0)
    finally:
        held.close()

    assert model.calls == 0
    assert not (tmp_path / STATE_FILENAME).exists()


@pytest.mark.parametrize("finalized", [False, True])
def test_a_write_that_fails_keeps_the_state_only_while_capture_goes_on(
    monkeypatch, tmp_path, finalized
):
    engine = _database()
    _patch_step(monkeypatch, engine, SpeakerTrackModel())
    audio = speaker_audio([(0, 30, 1)], 30)
    _run(tmp_path, audio[: 4 * SAMPLE_RATE], 0.0, 4.0)

    def fail(session, **kwargs):
        if finalized:
            # Finalize commits its status and removes the state while this
            # run waits on its write, which then fails (a lock timeout).
            with engine.begin() as conn:
                conn.execute(text("UPDATE recordings SET status = 'QUEUED'"))
            remove_live_label_state(tmp_path)
        raise RuntimeError("lock timeout")

    monkeypatch.setattr(step, "apply_live_speaker_labels", fail)
    with pytest.raises(RuntimeError, match="lock timeout"):
        _run(tmp_path, audio[4 * SAMPLE_RATE : 8 * SAMPLE_RATE], 4.0, 8.0)

    assert (tmp_path / STATE_FILENAME).exists() is not finalized
    assert (tmp_path / BANK_FILENAME).exists() is not finalized
    assert (tmp_path / LOCK_FILENAME).exists() is not finalized


SLOTS = step.CONFIG.clustering.max_speakers


def _without_member(state: bytes, member: str) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(state))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as target:
        for item in source.infolist():
            if item.filename != f"{member}.npy":
                target.writestr(item, source.read(item.filename))
    return out.getvalue()


def _with_member(state: bytes, member: str, change) -> bytes:
    with np.load(io.BytesIO(state)) as saved:
        arrays = {key: saved[key] for key in saved.files}
    assert arrays[member].size
    arrays[member] = change(arrays[member])
    out = io.BytesIO()
    np.savez(out, **arrays)
    return out.getvalue()


def _cut_bank(live_dir):
    bank = live_dir / BANK_FILENAME
    bank.write_bytes(bank.read_bytes()[:-1])


STATE_DAMAGE = {
    "not a state": lambda state, live_dir: b"truncated",
    # A power loss before the data reached the disk.
    "empty": lambda state, live_dir: b"",
    "truncated zip": lambda state, live_dir: state[: len(state) // 2],
    "a member missing": lambda state, live_dir: _without_member(state, "centers"),
    # One column would broadcast into every centroid without complaint.
    "centroids of the wrong shape": lambda state, live_dir: _with_member(
        state, "centers", lambda centers: centers[:, :1]
    ),
    "a unit pointing past the bank": lambda state, live_dir: _with_member(
        state, "unit_row", lambda rows: rows + 10_000
    ),
    # Sums restored into the wrong slots would extend another speaker's unit.
    "open slots that are not the open units": lambda state, live_dir: _with_member(
        state, "open_slots", lambda slots: (slots + 1) % SLOTS
    ),
    "a piece pointing past the units": lambda state, live_dir: _with_member(
        state, "piece_unit", lambda units: units + 10_000
    ),
    "a unit label past the slots": lambda state, live_dir: _with_member(
        state, "unit_label", lambda labels: labels + SLOTS
    ),
    "lock flags that are not flags": lambda state, live_dir: _with_member(
        state, "unit_locked", lambda locked: locked.astype(np.float64) + 0.5
    ),
    "a negative pass count": lambda state, live_dir: _with_member(
        state, "unit_stable", lambda stable: stable - 1
    ),
}


@pytest.mark.parametrize("damage", [*STATE_DAMAGE, "bank shorter than the state"])
def test_an_unreadable_state_stops_labels_for_the_recording(
    monkeypatch, tmp_path, damage
):
    engine = _database()
    model = SpeakerTrackModel()
    _patch_step(monkeypatch, engine, model)
    audio = speaker_audio([(0, 12, 1)], 12)
    _run(tmp_path, audio, 0.0, 12.0)
    calls = model.calls
    state_path = tmp_path / STATE_FILENAME
    if damage in STATE_DAMAGE:
        state_path.write_bytes(STATE_DAMAGE[damage](state_path.read_bytes(), tmp_path))
    else:
        _cut_bank(tmp_path)

    # Neither run raises: labels stop and the text lane never sees an error.
    _run(tmp_path, audio, 12.0, 24.0)
    _run(tmp_path, audio, 24.0, 36.0)

    assert model.calls == calls
    assert (tmp_path / "speaker_labels.off").exists()


def test_removing_the_state_tolerates_files_that_are_already_gone(tmp_path):
    (tmp_path / STATE_FILENAME).write_bytes(b"state")
    remove_live_label_state(tmp_path)
    remove_live_label_state(tmp_path)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("mode", "device", "cuda", "expected"),
    [
        (None, "auto", True, None),
        ("off", "auto", True, None),
        ("on", "auto", True, None),
        ("auto", "auto", True, "cuda"),
        ("AUTO ", "cuda", True, "cuda"),
        ("auto", "cuda:1", True, "cuda:1"),
        ("auto", "cpu", True, None),
        ("auto", "auto", False, None),
    ],
)
def test_live_labels_run_only_when_auto_and_the_lane_is_on_cuda(
    monkeypatch, mode, device, cuda, expected
):
    import torch

    from backend.utils.config_manager import config_manager

    monkeypatch.setitem(config_manager.config, "live_speaker_labels", mode)
    monkeypatch.setitem(config_manager.config, "processing_device", device)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: cuda)

    assert step.live_speaker_labels_device() == expected


def test_finalised_lines_ignore_a_live_label_left_in_their_payload():
    engine = _database()
    _apply(
        engine, {1000: LiveLabelDecision("LIVE_PROVISIONAL_1", locked=False, share=0.8)}
    )
    _finalised(engine)

    with Session(engine) as session:
        row = session.execute(
            text(
                "SELECT confidence_payload FROM transcript_utterances WHERE public_id='line-a'"
            )
        ).scalar_one()
        line = _by_id(serialize_canonical_utterances(session, RECORDING_ID))["line-a"]
    assert "live_speaker_label" in json.loads(row)
    assert line["speaker_state"] == "stable"


@pytest.mark.parametrize("status", ["UPLOADING", "ERROR"])
def test_editing_a_live_labelled_line_makes_no_speaker_of_its_label(status):
    # A failed final pass leaves the live lines as the transcript, and the
    # recording in ERROR accepts find-and-replace and segment PUTs, which
    # rebuild every line from the projection; so do undo, restore and backfill.
    engine = _database()
    _apply(
        engine,
        {
            1000: LiveLabelDecision("LIVE_PROVISIONAL_2", locked=False, share=0.9),
            10000: LiveLabelDecision("LIVE_PROVISIONAL_1", locked=True, share=1.0),
        },
    )
    with engine.begin() as conn:
        conn.execute(text("UPDATE recordings SET status = :s"), {"s": status})
    with Session(engine) as session:
        segments = json.loads(
            session.execute(text("SELECT segments FROM transcripts")).scalar_one()
        )
        for segment in segments:
            if segment["id"] == "line-a":
                segment["text"] = "opening remarks, corrected"
        apply_compatibility_segment_replace(
            session, recording_id=RECORDING_ID, segments=segments
        )
        session.commit()
        speakers = session.execute(
            text("SELECT COUNT(*) FROM recording_speakers")
        ).scalar_one()
        lines = _by_id(serialize_canonical_utterances(session, RECORDING_ID))

    assert speakers == 0
    assert lines["line-a"]["text"] == "opening remarks, corrected"
    assert [lines[key]["speaker"] for key in ("line-a", "line-b")] == [
        "LIVE_PROVISIONAL_2",
        "LIVE_PROVISIONAL_1",
    ]
    assert all(line["recording_speaker_id"] is None for line in lines.values())


def test_auto_on_a_host_without_cuda_runs_no_label_step(monkeypatch, tmp_path):
    # The lane imports the step whenever the setting is auto; the step itself
    # must still stay off where it has no GPU.
    import torch

    import backend.core.db as core_db
    from backend.utils.config_manager import config_manager

    engine = _database()
    model = SpeakerTrackModel()
    monkeypatch.setattr(core_db, "get_sync_session", lambda: Session(engine))
    monkeypatch.setattr(step, "_load_model", lambda device, config: model)
    monkeypatch.setitem(config_manager.config, "live_speaker_labels", "auto")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)

    _run(tmp_path, speaker_audio([(0, 4, 1)], 4), 0.0, 4.0)

    assert model.calls == 0
    assert list(tmp_path.iterdir()) == []


def test_a_run_queued_behind_finalize_does_no_model_pass(monkeypatch, tmp_path):
    engine = _database()
    model = SpeakerTrackModel()
    _patch_step(monkeypatch, engine, model)
    entered = []
    real_lock = step._recording_lock

    def recording_lock(live_dir):
        entered.append(live_dir)
        return real_lock(live_dir)

    audio = speaker_audio([(0, 8, 1)], 8)
    _run(tmp_path, audio[: 4 * SAMPLE_RATE], 0.0, 4.0)
    calls = model.calls
    monkeypatch.setattr(step, "_recording_lock", recording_lock)
    # Finalize ended the capture; the state it left (a crash before its
    # cleanup) goes with the queued run.
    with engine.begin() as conn:
        conn.execute(text("UPDATE recordings SET status = 'QUEUED'"))

    _run(tmp_path, audio[4 * SAMPLE_RATE :], 4.0, 8.0)

    assert model.calls == calls
    assert entered == []
    assert list(tmp_path.iterdir()) == []


def test_live_labels_are_never_left_for_the_model_to_name():
    # The prompt asks the model to name only the recording's unresolved
    # speakers; a live label is none, and reads as "Speaker N".
    from backend.models.speaker import RecordingSpeaker
    from backend.services.timeline_note_service import RecordingTimelineNotes
    from backend.utils.meeting_intelligence import (
        get_speakers_eligible_for_llm_renaming,
    )
    from backend.utils.meeting_notes import build_recording_speaker_map
    from backend.worker.tasks.meeting_intelligence_stage import (
        _build_automatic_meeting_intelligence_transcript_impl,
    )

    engine = _database()
    _apply(
        engine,
        {
            1000: LiveLabelDecision("LIVE_PROVISIONAL_2", locked=True, share=1.0),
            10000: LiveLabelDecision("LIVE_PROVISIONAL_1", locked=True, share=1.0),
        },
    )
    with Session(engine) as session:
        update_utterance_speaker(
            session,
            recording_id=RECORDING_ID,
            utterance_public_id="line-c",
            new_speaker_name="Cy",
        )
        session.add(
            RecordingSpeaker(recording_id=RECORDING_ID, diarization_label="SPEAKER_00")
        )
        session.commit()
        speakers = session.execute(select(RecordingSpeaker)).scalars().all()
        unresolved = get_speakers_eligible_for_llm_renaming(speakers)
        segments = serialize_canonical_utterances(session, RECORDING_ID)
        rendered = _build_automatic_meeting_intelligence_transcript_impl(
            segments,
            build_recording_speaker_map(speakers),
            unresolved,
            RecordingTimelineNotes(),
        )

    assert unresolved == ("SPEAKER_00",)
    assert "Speaker 2: opening remarks" in rendered
    assert "Speaker 1: a short reply" in rendered
    assert "Cy: a second voice" in rendered
    assert "LIVE_" not in rendered
