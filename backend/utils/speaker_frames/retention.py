"""The hourly sweep that deletes screen-study frames once they are due.

It runs whatever ``speaker_frame_sampling`` says, so switching the study off
never keeps a frame past its date. A recording's frames go:

- at once when its row is gone or in the bin, which also catches a delete path
  that did not remove them itself (a restore that overwrote the meeting, say);
- 14 days after it stopped, by the time ``mark_recording_stopped`` saved at
  finalize, or the sweep's own time when none was saved;
- 14 days after its last activity while it is still capturing or paused, so an
  abandoned capture cannot keep frames for ever.

A directory under any other name is left alone and reported, since Nojoin
never writes one. A state file whose frames are gone is removed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlmodel import Session, select

from backend.models.recording import Recording, RecordingStatus

from .storage import (
    RETENTION,
    delete_recording_frames,
    is_public_id,
    mark_recording_stopped,
    recording_dirs,
    recording_frames_dir,
    recording_stopped_at,
    state_files,
)

logger = logging.getLogger(__name__)

_CAPTURING = (RecordingStatus.UPLOADING, RecordingStatus.PAUSED)


@dataclass
class SweepResult:
    deleted_recordings: list[str] = field(default_factory=list)
    deleted_frames: int = 0
    kept: int = 0
    unrecognised: list[str] = field(default_factory=list)
    orphaned_state_removed: int = 0


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _due(recording: Recording, public_id: str, now: datetime) -> str | None:
    """Why this recording's frames go now, or None to keep them."""
    if recording.is_deleted:
        return "in the bin"
    if recording.status in _CAPTURING:
        last = _aware(recording.last_activity_at or recording.created_at)
        if last is not None and now >= last + RETENTION:
            return "retention"
        return None
    stopped = recording_stopped_at(public_id)
    if stopped is None:
        mark_recording_stopped(public_id, now)
        return None
    return "retention" if now >= stopped + RETENTION else None


def sweep_speaker_frames(
    session: Session, *, now: datetime | None = None
) -> SweepResult:
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    result = SweepResult()

    for directory in recording_dirs():
        public_id = directory.name
        if not is_public_id(public_id):
            result.unrecognised.append(public_id)
            continue
        recording = session.exec(
            select(Recording).where(Recording.public_id == public_id)
        ).first()
        reason = "deleted" if recording is None else _due(recording, public_id, moment)
        if reason is None:
            result.kept += 1
            continue
        result.deleted_frames += delete_recording_frames(public_id, reason=reason)
        result.deleted_recordings.append(public_id)

    for state in state_files():
        public_id = state.stem
        if not is_public_id(public_id):
            continue
        if not recording_frames_dir(public_id).is_dir():
            state.unlink(missing_ok=True)
            result.orphaned_state_removed += 1

    if result.unrecognised:
        logger.warning(
            "The screen study sweep left %d directories it did not recognise",
            len(result.unrecognised),
        )
    return result
