"""Workers recover what a lost side run left behind (issues #828 and #836).

Notes no longer move the recording's status, so a lost notes run strands only
its claim. Retry Speaker Inference still holds a finished meeting in
PROCESSING; a hold whose run was lost is handed back when the io worker
starts, on a beat tick once it is older than the claim expiry, and by the gpu
worker's startup sweep, which no longer reprocesses the whole meeting to do
it. A failed pipeline run stays in ERROR.
"""

from __future__ import annotations

import threading
import time
import types
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlmodel import Session

import backend.worker.tasks as tasks_module
from backend.models.recording import SPEAKER_INFERENCE_STEP
from backend.tests.sqlite_schemas import RECORDINGS_SCHEMA, TRANSCRIPTS_SCHEMA
from backend.tests.test_generate_notes_task import (
    _create_notes_task_database,
    _run_generate_notes_task,
)
from backend.utils.generation_claim import GENERATION_CLAIM_EXPIRY
from backend.utils.status_manager import update_recording_status
from backend.utils.time import utc_now
from backend.worker.tasks import pipeline, side_runs

IO_QUEUE = "io"
GPU_QUEUE = "gpu"
LONG_AGO = GENERATION_CLAIM_EXPIRY + timedelta(minutes=1)


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    engine = create_engine(f"sqlite:///{tmp_path / 'side-runs.sqlite'}", future=True)
    with engine.begin() as connection:
        connection.execute(text(RECORDINGS_SCHEMA))
        connection.execute(text(TRANSCRIPTS_SCHEMA))
    try:
        yield engine
    finally:
        engine.dispose()


DEFAULTS: dict[str, Any] = {
    "status": "PROCESSING",
    "step": SPEAKER_INFERENCE_STEP,
    "completed_at": None,
    "task_id": None,
    "touched": None,
    "transcript_status": "completed",
    "notes_status": "completed",
    "error_message": None,
}


def _insert(engine: Engine, recording_id: int, **fields: Any) -> None:
    """A recording and its transcript; ``fields`` override ``DEFAULTS``."""
    row = {**DEFAULTS, **fields}
    touched = row["touched"] or utc_now()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO recordings (
                    id, created_at, updated_at, name, public_id, meeting_uid,
                    audio_path, celery_task_id, status, client_status,
                    upload_progress, processing_progress, processing_step,
                    processing_completed_at, is_archived, is_deleted
                ) VALUES (
                    :id, :touched, :touched, 'Weekly sync', :public_id, :uid,
                    :audio_path, :task_id, :status, 'IDLE', 100, 100, :step,
                    :completed_at, 0, 0
                )
                """
            ),
            {
                "id": recording_id,
                "touched": touched,
                "public_id": f"rec-{recording_id}",
                "uid": f"uid-{recording_id}",
                "audio_path": f"/tmp/missing-{recording_id}.wav",
                "task_id": row["task_id"],
                "status": row["status"],
                "step": row["step"],
                "completed_at": row["completed_at"],
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO transcripts (
                    id, created_at, updated_at, recording_id, segments,
                    notes_status, transcript_status, error_message
                ) VALUES (
                    :id, :touched, :touched, :id, '[]', :notes, :status, :error
                )
                """
            ),
            {
                "id": recording_id,
                "touched": touched,
                "notes": row["notes_status"],
                "status": row["transcript_status"],
                "error": row["error_message"],
            },
        )


def _recording(engine: Engine, recording_id: int) -> tuple[Any, ...]:
    with engine.connect() as connection:
        return tuple(
            connection.execute(
                text("SELECT status, processing_step FROM recordings WHERE id = :id"),
                {"id": recording_id},
            ).one()
        )


def _sender(queues: set[str]) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        app=types.SimpleNamespace(
            amqp=types.SimpleNamespace(
                queues=types.SimpleNamespace(
                    consume_from={queue: object() for queue in queues}
                )
            )
        )
    )


# --- holds ---


def test_a_hold_whose_task_is_gone_is_handed_back(engine: Engine) -> None:
    _insert(engine, 1, task_id="lost-task")
    _insert(engine, 2, task_id="running-task")

    with Session(engine) as session:
        released = side_runs.release_stranded_holds(
            session,
            side_runs.side_run_holds(session),
            live_task_ids={"running-task"},
        )

    assert released == [1]
    assert _recording(engine, 1) == ("PROCESSED", "Completed")
    assert _recording(engine, 2) == ("PROCESSING", SPEAKER_INFERENCE_STEP)


def test_a_hold_over_a_failed_transcription_goes_back_to_error(engine: Engine) -> None:
    _insert(
        engine,
        1,
        task_id="lost-task",
        transcript_status="error",
        error_message="Transcription failed: the model ran out of memory",
    )

    with Session(engine) as session:
        side_runs.release_stranded_holds(
            session, side_runs.side_run_holds(session), live_task_ids=set()
        )

    assert _recording(engine, 1) == (
        "ERROR",
        "Transcription failed: the model ran out of memory",
    )


def test_without_liveness_only_an_expired_hold_is_handed_back(engine: Engine) -> None:
    _insert(engine, 1, task_id="unknown", touched=utc_now() - LONG_AGO)
    _insert(engine, 2, task_id="unknown")

    with Session(engine) as session:
        released = side_runs.release_stranded_holds(
            session, side_runs.side_run_holds(session), live_task_ids=None
        )

    assert released == [1]
    assert _recording(engine, 2)[0] == "PROCESSING"


def test_a_running_pipeline_is_never_taken_for_a_hold(engine: Engine) -> None:
    _insert(engine, 1, step="Transcribing... (GPU)", task_id="lost-task")

    with Session(engine) as session:
        assert side_runs.side_run_holds(session) == []


def test_an_upgrade_finds_a_notes_hold_from_an_earlier_release(
    engine: Engine,
) -> None:
    """Notes held a finished meeting in PROCESSING before #828; an upgrade
    can find one, which still carries the pipeline's completion time."""
    _insert(
        engine,
        1,
        step="Generating meeting notes...",
        completed_at=utc_now() - timedelta(days=1),
        task_id="pipeline-task",
        notes_status="generating",
    )

    with Session(engine) as session:
        assert [r.id for r in side_runs.side_run_holds(session)] == [1]


def test_the_gpu_sweep_hands_a_hold_back_instead_of_reprocessing_it(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _insert(engine, 1, task_id="lost-inference")
    _insert(engine, 2, step="Transcribing... (GPU)", task_id="lost-pipeline")
    monkeypatch.setattr(pipeline, "_live_task_ids", lambda: set())

    with Session(engine) as session:
        reclaimed = pipeline._reclaim_orphaned_processing(session)
        reclaimed_ids = [recording.id for recording in reclaimed]

    assert reclaimed_ids == [2]
    assert _recording(engine, 1) == ("PROCESSED", "Completed")
    assert _recording(engine, 2) == ("QUEUED", "Queued after an interrupted run...")


def test_the_gpu_sweep_leaves_a_hold_whose_run_is_alive(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _insert(engine, 1, task_id="live-inference")
    monkeypatch.setattr(pipeline, "_live_task_ids", lambda: {"live-inference"})

    with Session(engine) as session:
        assert pipeline._reclaim_orphaned_processing(session) == []

    assert _recording(engine, 1)[0] == "PROCESSING"


def test_an_io_worker_start_hands_back_holds_lost_with_its_predecessor(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _insert(engine, 1, task_id="lost-inference")
    _insert(engine, 2, step="Transcribing... (GPU)", task_id="pipeline-task")
    monkeypatch.setattr(side_runs, "sync_engine", engine)
    monkeypatch.setattr(pipeline, "_live_task_ids", lambda: set())

    side_runs.release_side_run_holds_on_io_start(_sender({IO_QUEUE}))

    assert _recording(engine, 1) == ("PROCESSED", "Completed")
    # The gpu lane's own sweep decides about a pipeline.
    assert _recording(engine, 2)[0] == "PROCESSING"


def test_an_io_worker_start_leaves_holds_when_liveness_is_unknown(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _insert(engine, 1, task_id="maybe-running")
    monkeypatch.setattr(side_runs, "sync_engine", engine)
    monkeypatch.setattr(pipeline, "_live_task_ids", lambda: None)

    side_runs.release_side_run_holds_on_io_start(_sender({IO_QUEUE}))

    assert _recording(engine, 1)[0] == "PROCESSING"


def test_only_the_io_lane_releases_holds_on_start(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _insert(engine, 1, task_id="lost-inference")
    monkeypatch.setattr(side_runs, "sync_engine", engine)
    monkeypatch.setattr(pipeline, "_live_task_ids", lambda: set())

    side_runs.release_side_run_holds_on_io_start(_sender({GPU_QUEUE}))

    assert _recording(engine, 1)[0] == "PROCESSING"


def test_the_beat_tick_hands_back_a_hold_older_than_the_expiry(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _insert(engine, 1, task_id="killed-child", touched=utc_now() - LONG_AGO)
    _insert(engine, 2, task_id="running")
    monkeypatch.setattr(side_runs, "get_sync_session", lambda: Session(engine))

    assert side_runs.release_stale_side_run_holds_task.run() == {"released": 1}

    assert _recording(engine, 1) == ("PROCESSED", "Completed")
    assert _recording(engine, 2)[0] == "PROCESSING"


def test_a_late_inference_leaves_a_reprocess_that_followed_its_release(
    engine: Engine,
) -> None:
    """Once a lost run's hold is handed back the meeting can be reprocessed;
    that run finishing late must not end the reprocess."""
    _insert(engine, 1, step="Transcribing... (GPU)", task_id="pipeline-task")

    with Session(engine) as session:
        recording = session.get(tasks_module.Recording, 1)
        tasks_module._complete_speaker_inference_task(session, recording)

    assert _recording(engine, 1) == ("PROCESSING", "Transcribing... (GPU)")


def test_speaker_inference_still_hands_back_its_own_hold(engine: Engine) -> None:
    _insert(engine, 1, task_id="inference")

    with Session(engine) as session:
        recording = session.get(tasks_module.Recording, 1)
        tasks_module._complete_speaker_inference_task(session, recording)

    assert _recording(engine, 1) == ("PROCESSED", "Completed")


# --- claim renewal ---


def test_a_heartbeat_renews_the_claim_until_its_block_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renewals: list[tuple[int, str]] = []
    renewed = threading.Event()

    def renew(recording_id: int, field: str) -> None:
        renewals.append((recording_id, field))
        renewed.set()

    monkeypatch.setattr(side_runs, "renew_transcript_claim", renew)

    with side_runs.claim_heartbeat(7, "notes_status", interval=0.01):
        assert renewed.wait(5)
    count = len(renewals)
    time.sleep(0.05)

    assert renewals[0] == (7, "notes_status")
    assert len(renewals) == count


def test_a_failed_renewal_does_not_end_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = threading.Event()

    def renew(recording_id: int, field: str) -> None:
        attempts.set()
        raise RuntimeError("database restarting")

    monkeypatch.setattr(side_runs, "renew_transcript_claim", renew)

    with side_runs.claim_heartbeat(7, "notes_status", interval=0.01):
        assert attempts.wait(5)


def test_a_renewal_stamps_only_a_claim_still_generating(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    then = utc_now() - timedelta(minutes=10)
    _insert(engine, 1, status="PROCESSED", notes_status="generating", touched=then)
    _insert(engine, 2, status="PROCESSED", notes_status="completed", touched=then)
    monkeypatch.setattr(side_runs, "get_sync_session", lambda: Session(engine))

    side_runs.renew_transcript_claim(1, "notes_status")
    side_runs.renew_transcript_claim(2, "notes_status")

    with engine.connect() as connection:
        stamps = dict(
            connection.execute(
                text("SELECT id, updated_at FROM transcripts ORDER BY id")
            ).all()
        )
    assert datetime.fromisoformat(str(stamps[1])) > then + timedelta(minutes=9)
    assert datetime.fromisoformat(str(stamps[2])) == then


# --- the status manager (#836) ---


@pytest.mark.parametrize("transcript_status", ["pending", "processing"])
def test_a_failed_run_stays_in_error_over_an_unpublished_transcript(
    engine: Engine, transcript_status: str
) -> None:
    _insert(engine, 1, status="ERROR", step="System Error: Audio file not found")
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE transcripts SET transcript_status = :s"),
            {"s": transcript_status},
        )

    with Session(engine) as session:
        update_recording_status(session, 1)

    assert _recording(engine, 1) == ("ERROR", "System Error: Audio file not found")


def test_a_published_transcript_still_finishes_a_failed_run(engine: Engine) -> None:
    """A failure after the transcript published keeps the meeting viewable."""
    _insert(engine, 1, status="ERROR", step="System Error: speaker stage")

    with Session(engine) as session:
        update_recording_status(session, 1)

    assert _recording(engine, 1)[0] == "PROCESSED"


def test_generating_notes_no_longer_hold_a_meeting_in_processing(
    engine: Engine,
) -> None:
    _insert(engine, 1, status="PROCESSED", step="Completed", notes_status="generating")

    with Session(engine) as session:
        update_recording_status(session, 1)

    assert _recording(engine, 1)[0] == "PROCESSED"


def test_a_paused_capture_is_left_to_its_capture(engine: Engine) -> None:
    _insert(engine, 1, status="PAUSED", step="Paused", transcript_status="pending")

    with Session(engine) as session:
        update_recording_status(session, 1)

    assert _recording(engine, 1)[0] == "PAUSED"


def test_retry_processing_without_audio_ends_in_error(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #836: the run failed and set ERROR, then recomputed QUEUED from the
    transcript the retry had reset to pending, so the page waited for good."""
    _insert(
        engine,
        1,
        status="QUEUED",
        step="Queued for processing...",
        transcript_status="pending",
        notes_status="pending",
    )
    session = Session(engine)
    task = tasks_module.process_recording_task

    def prepare(self, session, recording, temp_files):
        ctx = pipeline._PipelineRunContext(
            task=self,
            session=session,
            recording_id=recording.id,
            device_suffix="",
            temp_files=temp_files,
            merged_config={},
        )
        return ctx, types.SimpleNamespace(merged_config={})

    monkeypatch.setattr(pipeline, "_prepare_pipeline_run", prepare)
    monkeypatch.setattr(pipeline, "_release_pipeline_vram", lambda: None)
    monkeypatch.setattr(task, "_session", session, raising=False)
    monkeypatch.setattr(task, "update_state", lambda *a, **k: None, raising=False)
    try:
        assert task.run(1) is None
    finally:
        session.close()

    status, step = _recording(engine, 1)
    assert status == "ERROR"
    assert "Audio file not found" in step


# --- the notes run ---


def test_a_notes_run_leaves_the_meeting_finished_while_the_model_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_text_embedding
) -> None:
    engine = _create_notes_task_database(tmp_path, owner_settings={})
    seen: dict[str, Any] = {}
    heartbeats: list[tuple[int, str]] = []

    class FakeLLM:
        def generate_meeting_notes(self, transcript, speaker_mapping, **kwargs):
            with engine.connect() as connection:
                seen["during"] = tuple(
                    connection.execute(
                        text(
                            "SELECT r.status, t.notes_status FROM recordings r "
                            "JOIN transcripts t ON t.recording_id = r.id"
                        )
                    ).one()
                )
            seen["heartbeat"] = list(heartbeats)
            return "# Meeting Notes"

    class Heartbeat:
        def __init__(self, recording_id: int, field: str) -> None:
            self.claim = (recording_id, field)

        def __enter__(self) -> None:
            heartbeats.append(self.claim)

        def __exit__(self, *exc_info: object) -> None:
            return None

    monkeypatch.setattr(tasks_module, "get_sync_session", lambda: Session(engine))
    monkeypatch.setattr(
        tasks_module, "resolve_llm_config", lambda *a, **k: _configured_llm()
    )
    monkeypatch.setattr(tasks_module, "_llm_backend_from_config", lambda c: FakeLLM())
    monkeypatch.setattr("backend.worker.tasks.intelligence.claim_heartbeat", Heartbeat)
    verification = create_engine(str(engine.url), future=True)
    try:
        _run_generate_notes_task(engine)
        with verification.connect() as connection:
            after = tuple(
                connection.execute(
                    text(
                        "SELECT r.status, r.processing_step, t.notes_status "
                        "FROM recordings r JOIN transcripts t "
                        "ON t.recording_id = r.id"
                    )
                ).one()
            )
    finally:
        verification.dispose()

    assert seen["during"] == ("PROCESSED", "generating")
    # The model call runs inside the claim's renewal.
    assert seen["heartbeat"] == [(1, "notes_status")]
    assert after == ("PROCESSED", "Completed", "completed")


def test_a_failed_notes_run_fails_only_the_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_text_embedding
) -> None:
    engine = _create_notes_task_database(tmp_path, owner_settings={})

    class FakeLLM:
        def generate_meeting_notes(self, transcript, speaker_mapping, **kwargs):
            raise RuntimeError("The provider refused the request")

    monkeypatch.setattr(tasks_module, "get_sync_session", lambda: Session(engine))
    monkeypatch.setattr(
        tasks_module, "resolve_llm_config", lambda *a, **k: _configured_llm()
    )
    monkeypatch.setattr(tasks_module, "_llm_backend_from_config", lambda c: FakeLLM())
    verification = create_engine(str(engine.url), future=True)
    try:
        _run_generate_notes_task(engine)
        with verification.connect() as connection:
            after = tuple(
                connection.execute(
                    text(
                        "SELECT r.status, r.processing_step, t.notes_status, "
                        "t.error_message FROM recordings r JOIN transcripts t "
                        "ON t.recording_id = r.id"
                    )
                ).one()
            )
    finally:
        verification.dispose()

    assert after == (
        "PROCESSED",
        "Completed",
        "error",
        "The provider refused the request",
    )


def _configured_llm() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        merged_config={},
        provider="ollama",
        missing_configuration_message=lambda: None,
    )
