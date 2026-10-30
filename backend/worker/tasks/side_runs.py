"""Runs over a finished meeting, and the recovery of ones a lost worker strands.

Notes, the AI analysis and delivery measurement claim their own status on the
transcript row (``backend.utils.generation_claim``), so a lost run strands only
that output until its claim expires. Retry Speaker Inference still holds the
whole recording in PROCESSING for its run, so a lost one would keep the meeting
in its processing view and refuse Reprocess. Celery acknowledges a task when a
worker takes it and never delivers a lost one again, so the hold is handed back
here: when the io worker starts, for a hold whose task is no longer running,
and on a beat tick, for one older than the claim expiry (issue #828).
"""

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from celery.signals import worker_ready
from sqlalchemy import update
from sqlmodel import Session, col, select

from backend.celery_app import IO_QUEUE, celery_app
from backend.core.db import get_sync_session, sync_engine
from backend.models.recording import (
    ClientStatus,
    Recording,
    RecordingStatus,
    is_side_run_hold,
)
from backend.models.transcript import Transcript
from backend.utils.generation_claim import GENERATING, GENERATION_CLAIM_EXPIRY
from backend.utils.time import utc_now

logger = logging.getLogger(__name__)

# How often a run waiting on a model renews its claim. Far inside the expiry,
# so a slow model or a long meeting in parts never reads as lost while it runs.
CLAIM_HEARTBEAT_INTERVAL_SECONDS = 60.0


def renew_transcript_claim(recording_id: int, field: str) -> None:
    """Stamp a claim that is still generating as alive, in a session of its own."""
    with get_sync_session() as session:
        session.execute(
            update(Transcript)
            .where(col(Transcript.recording_id) == recording_id)
            .where(getattr(Transcript, field) == GENERATING)
            .values(updated_at=utc_now())
        )
        session.commit()


@contextmanager
def claim_heartbeat(
    recording_id: int,
    field: str,
    *,
    interval: float = CLAIM_HEARTBEAT_INTERVAL_SECONDS,
) -> Iterator[None]:
    """Renew the transcript's ``field`` claim every ``interval`` while the block runs.

    The block is a model call, which a long meeting in parts or a slow provider
    can keep going past the expiry. The task's own session is waiting on that
    call, so the renewal runs on a thread with a session of its own. It ends
    with the block, or with the process when the worker is lost, which is what
    lets a lost run's claim expire.
    """
    stop = threading.Event()

    def beat() -> None:
        while not stop.wait(interval):
            try:
                renew_transcript_claim(recording_id, field)
            except Exception:  # noqa: BLE001 -- boundary: a missed renewal only brings the expiry closer; the run goes on
                logger.warning(
                    "Could not renew the %s claim of recording %s",
                    field,
                    recording_id,
                    exc_info=True,
                )

    thread = threading.Thread(
        target=beat, name=f"claim-heartbeat-{recording_id}", daemon=True
    )
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=10)


def hand_back_side_run_hold(session: Session, recording: Recording) -> None:
    """Return a held meeting to the state its transcript describes, uncommitted.

    A side run never repairs a failed transcription, so a recording whose ASR
    failed goes back to ERROR rather than reading as finished.
    """
    transcript = session.exec(
        select(Transcript).where(Transcript.recording_id == recording.id)
    ).first()
    if transcript is not None and transcript.transcription_failed():
        recording.status = RecordingStatus.ERROR
        recording.processing_step = transcript.error_message or "Transcription failed"
    else:
        recording.status = RecordingStatus.PROCESSED
        recording.processing_step = "Completed"
    recording.client_status = ClientStatus.IDLE
    session.add(recording)


def side_run_holds(session: Session) -> list[Recording]:
    """Every finished meeting a side run holds in PROCESSING."""
    processing = session.exec(
        select(Recording).where(Recording.status == RecordingStatus.PROCESSING)
    ).all()
    return [recording for recording in processing if is_side_run_hold(recording)]


def release_stranded_holds(
    session: Session,
    holds: list[Recording],
    *,
    live_task_ids: set[str] | None,
) -> list[int]:
    """Hand back each hold whose run is gone, and return their ids.

    With the running tasks known, a hold whose task is not among them is gone.
    A task still waiting in the broker is not among them either; handing its
    hold back early only lets its result land on a finished meeting. Without
    them, only a hold untouched for longer than the claim expiry is gone.
    """
    cutoff = utc_now() - GENERATION_CLAIM_EXPIRY
    released: list[int] = []
    for recording in holds:
        if live_task_ids is not None:
            if recording.celery_task_id in live_task_ids:
                continue
        elif recording.updated_at > cutoff:
            continue
        logger.warning(
            "Recording %s was held in PROCESSING by %s (task %s), which is no "
            "longer running. Handing it back.",
            recording.id,
            recording.processing_step or "a side run",
            recording.celery_task_id or "<unrecorded>",
        )
        hand_back_side_run_hold(session, recording)
        assert recording.id is not None
        released.append(recording.id)
    if released:
        session.commit()
    return released


def release_holds_among(
    session: Session,
    processing: list[Recording],
    *,
    live_task_ids: set[str],
) -> list[Recording]:
    """Hand back the stranded holds among ``processing``; return the rest.

    For the gpu lane's startup sweep, whose pipeline runs are what remain.
    """
    holds = [recording for recording in processing if is_side_run_hold(recording)]
    others = [recording for recording in processing if not is_side_run_hold(recording)]
    release_stranded_holds(session, holds, live_task_ids=live_task_ids)
    return others


@worker_ready.connect
def release_side_run_holds_on_io_start(sender, **kwargs) -> None:
    """On io worker startup, hand back holds whose runs were lost with a worker.

    Speaker inference runs on this lane, so a restart here is what loses one.
    The hold names its task, so liveness decides at once; when it cannot be
    established, the beat tick's expiry does. Runs in the prefork pool's
    parent, so the engine's connections are disposed before returning, as in
    the cpu lane's startup sweep.
    """
    from backend.worker.tasks.pipeline import _live_task_ids, _sweeps_recordings

    if not _sweeps_recordings(sender, IO_QUEUE):
        return
    session = Session(sync_engine)
    try:
        holds = side_run_holds(session)
        if not holds:
            return
        live = _live_task_ids()
        if live is None:
            logger.warning(
                "%s recording(s) are held by a side run but liveness is unknown; "
                "leaving them to the claim expiry.",
                len(holds),
            )
            return
        release_stranded_holds(session, holds, live_task_ids=live)
    except Exception as e:  # noqa: BLE001 -- boundary: a failed sweep must not stop the worker starting; the beat tick retries
        logger.error("Failed to release side-run holds: %s", e, exc_info=True)
    finally:
        session.close()
        sync_engine.dispose()


@celery_app.task(name="backend.worker.tasks.release_stale_side_run_holds_task")
def release_stale_side_run_holds_task() -> dict[str, int]:
    """Hand back holds older than the claim expiry, whatever lost their run.

    Covers what no worker restart reveals: a pool child killed mid-run, or a
    dispatch that failed after the hold was committed.
    """
    with get_sync_session() as session:
        released = release_stranded_holds(
            session, side_run_holds(session), live_task_ids=None
        )
    return {"released": len(released)}


__all__ = [
    "CLAIM_HEARTBEAT_INTERVAL_SECONDS",
    "claim_heartbeat",
    "hand_back_side_run_hold",
    "release_holds_among",
    "release_side_run_holds_on_io_start",
    "release_stale_side_run_holds_task",
    "release_stranded_holds",
    "renew_transcript_claim",
    "side_run_holds",
]
