"""The screen study's endpoints (#653).

Every route answers 404 unless ``speaker_frame_sampling`` is ``owner`` and the
caller is the primary Owner, and the per-recording routes only for a recording
of the caller's own. Frames have their own limits, apart from the audio's: 1 MB
each, two uploads at a time per user and 180 a minute per client, so a study
can never hold back a capture's segments. Nothing here logs per frame.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from fastapi import Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

import backend.api.v1.endpoints.recordings as recordings_module
from backend.api.deps import get_current_recording_client_user, get_db
from backend.models.recording import Recording, RecordingStatus
from backend.models.user import User
from backend.utils import speaker_frames
from backend.utils.primary_owner import get_primary_owner_async
from backend.utils.rate_limit import enforce_rate_limit, enforce_upload_concurrency
from backend.utils.speaker_frames.storage import MAX_OFFSET_MS, scan_summary, utc_iso

from .router import router

logger = logging.getLogger(__name__)

SPEAKER_FRAME_UPLOAD_CONCURRENCY = 2
SPEAKER_FRAME_RATE_LIMIT_PER_MINUTE = 180
SPEAKER_FRAME_CONTENT_TYPE = "image/jpeg"
_READ_CHUNK = 64 * 1024

_CAPTURING = {RecordingStatus.UPLOADING, RecordingStatus.PAUSED}

UPLOAD_CLOSED_DETAIL = "This recording no longer takes screen study frames."
CAP_REACHED_DETAIL = (
    "The screen study has reached its 20 GB storage limit, so no more frames are kept."
)


async def _require_study_owner(db: AsyncSession, user: User) -> int:
    """The owner's id; 404, as for a missing route, unless the study is on and it is theirs."""
    if not speaker_frames.sampling_enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    owner = await get_primary_owner_async(db)
    if owner is None or owner.id is None or owner.id != user.id:
        raise HTTPException(status_code=404, detail="Not Found")
    return owner.id


def _summary_payload(
    recording: Recording, summary: speaker_frames.FrameSummary
) -> dict:
    stopped = speaker_frames.recording_stopped_at(recording.public_id)
    return {
        "frames": summary.frames,
        "bytes": summary.bytes,
        "delete_after": utc_iso(speaker_frames.delete_after(recording.public_id)),
        "recording_stopped": stopped is not None,
    }


async def _read_bounded(file: UploadFile, limit: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await file.read(_READ_CHUNK)
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise HTTPException(
                status_code=413, detail="A screen study frame may be at most 1 MB."
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _now() -> datetime:
    return datetime.now(UTC)


def _accepts_frames(recording: Recording, now: datetime) -> bool:
    if recording.status in _CAPTURING:
        return True
    return speaker_frames.accepts_frames_after_stop(recording.public_id, now=now)


@router.get("/speaker-frames/availability")
async def get_speaker_frame_availability(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user),
):
    """Whether this user may tick Screen study at Start Meeting, and its rate."""
    await _require_study_owner(db, current_user)
    return {
        "rate_hz": speaker_frames.frame_rate_hz(),
        "retention_days": speaker_frames.RETENTION.days,
        "max_frame_bytes": speaker_frames.MAX_FRAME_BYTES,
    }


@router.post("/{recording_id}/speaker-frames")
async def upload_speaker_frame(  # noqa: PLR0913 - FastAPI dependencies are parameters
    request: Request,
    recording_id: str,
    sequence: int = Query(
        ..., ge=0, description="The audio chunk the frame was taken in"
    ),
    offset_ms: int = Query(
        ...,
        ge=0,
        le=MAX_OFFSET_MS,
        description="Milliseconds into that chunk",
    ),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user),
):
    """Keep one JPEG of the shared screen for the screen study."""
    owner_id = await _require_study_owner(db, current_user)
    await enforce_rate_limit(
        request,
        "speaker_frames",
        SPEAKER_FRAME_RATE_LIMIT_PER_MINUTE,
        60,
        detail="Too many screen study frames. The browser stops sampling.",
    )
    async with enforce_upload_concurrency(
        request,
        "upload_speaker_frame",
        str(owner_id),
        SPEAKER_FRAME_UPLOAD_CONCURRENCY,
    ):
        recording = await recordings_module._get_owned_recording(
            db, recording_id, owner_id
        )
        now = _now()
        if not _accepts_frames(recording, now):
            raise HTTPException(status_code=409, detail=UPLOAD_CLOSED_DETAIL)
        content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
        if content_type != SPEAKER_FRAME_CONTENT_TYPE:
            raise HTTPException(
                status_code=415, detail="A screen study frame must be image/jpeg."
            )
        data = await _read_bounded(file, speaker_frames.MAX_FRAME_BYTES)
        try:
            stored = await asyncio.to_thread(
                speaker_frames.store_frame,
                recording.public_id,
                sequence=sequence,
                offset_ms=offset_ms,
                data=data,
                received_at=now,
            )
        except speaker_frames.InvalidFrame as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except speaker_frames.StorageCapReached as exc:
            raise HTTPException(status_code=507, detail=CAP_REACHED_DETAIL) from exc

    return {
        "status": "duplicate" if stored.duplicate else "stored",
        **_summary_payload(recording, stored.summary),
    }


@router.get("/{recording_id}/speaker-frames")
async def get_speaker_frame_status(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user),
):
    """How many frames this recording has, their size and when they go."""
    owner_id = await _require_study_owner(db, current_user)
    recording = await recordings_module._get_owned_recording(db, recording_id, owner_id)
    summary = await asyncio.to_thread(
        scan_summary,
        speaker_frames.recording_frames_dir(recording.public_id),
    )
    return _summary_payload(recording, summary)


@router.delete("/{recording_id}/speaker-frames")
async def delete_speaker_frames(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_recording_client_user),
):
    """Delete this recording's frames now. The recording itself is untouched."""
    owner_id = await _require_study_owner(db, current_user)
    recording = await recordings_module._get_owned_recording(db, recording_id, owner_id)
    deleted = await asyncio.to_thread(
        speaker_frames.delete_recording_frames,
        recording.public_id,
        reason="deleted by its owner",
    )
    return {"deleted": deleted}
