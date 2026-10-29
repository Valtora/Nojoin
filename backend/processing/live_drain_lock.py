"""Serialise live drains on the recording's shared audio filesystem.

The label lane already uses flock for per-recording worker exclusion. A separate
lock protects the text checkpoint and carried WAVs, without holding the upload
audio advisory lock (or a database connection) throughout inference. Every GPU
worker must share the recording's existing audio and checkpoint directory.
"""

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from celery import Task

DRAIN_LOCK_FILENAME = "live-drain.lock"


def _skip_live_drain(
    recording_id: int, sequence: int, reason: str, *, status: str | None = None
) -> None:
    """Preserve the task's skip metrics without creating a live directory."""
    from backend.processing import live_transcribe as lt

    payload: dict[str, Any] = {"sequence": sequence, "reason": reason}
    extra = {"status": status} if reason == "not_uploading" else None
    if extra:
        payload.update(extra)
    lt.record_pipeline_metric(
        stage="live_task_started",
        recording_id=recording_id,
        payload={"sequence": sequence},
        log=lt.logger,
    )
    lt.record_pipeline_metric(
        stage="live_task_skipped",
        recording_id=recording_id,
        payload=payload,
        status="skipped",
        log=lt.logger,
    )
    lt._record_live_sequence_outcome_metric(
        recording_id=recording_id,
        sequence=sequence,
        outcome="skipped",
        reason=reason,
        extra_payload=extra,
    )


@contextmanager
def recording_live_drain_lock(recording_dir: Path) -> Iterator[bool]:
    """Try one drain lock, releasing it on every return or exception.

    Keep the inode until the recording's entire temporary directory is removed;
    unlinking a lock file lets another worker lock a new inode concurrently.
    """
    with (recording_dir / DRAIN_LOCK_FILENAME).open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def drain_live_recording(task: Task, recording_id: int, sequence: int) -> None:
    """Protect checkpoint read, audio promotion, recovery and checkpoint write.

    A holder snapshots its run before inference, so a contending upload may not
    be in that snapshot. Retry it even if no later chunk arrives; contention is
    outside ordinary run-failure recovery and must never mark audio spent.
    """
    from backend.core.db import get_sync_session
    from backend.models.recording import Recording, RecordingStatus
    from backend.processing import live_transcribe as lt

    # Reject ended captures before making any files. The drain still rereads
    # status and captures the publication token after acquiring its lock.
    session = get_sync_session()
    try:
        recording = session.get(Recording, recording_id)
        status = getattr(recording, "status", None)
    finally:
        session.close()
    if status not in {RecordingStatus.UPLOADING, RecordingStatus.PAUSED}:
        _skip_live_drain(recording_id, sequence, "not_uploading", status=status)
        return
    temp_dir = lt.recording_upload_temp_dir(recording_id, create=False)
    if not temp_dir.exists():
        _skip_live_drain(recording_id, sequence, "upload_buffer_missing")
        return
    with recording_live_drain_lock(temp_dir) as acquired:
        if not acquired:
            raise task.retry(countdown=1)
        lt._transcribe_segment_live(recording_id, sequence)
