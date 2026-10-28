"""Live word settling boundaries taken by the capture lifecycle routes."""

from fastapi import HTTPException
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession

import backend.api.v1.endpoints.recordings as recordings_module
from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.utils.canonical_pipeline.constants import _load_transcript
from backend.utils.transcript_settling import update_capture_settling_state


async def lock_capture_publication(
    db: AsyncSession, recording: Recording
) -> Transcript | None:
    """Take the audio lock, then Transcript: the order Stop takes them in."""
    try:
        await recordings_module._lock_capture(db, recording)
    except InvalidRequestError as exc:
        raise HTTPException(status_code=404, detail="Recording not found") from exc
    return await db.run_sync(
        lambda session: _load_transcript(session, recording.id, lock=True)
    )


async def close_live_words(
    db: AsyncSession, transcript: Transcript | None, recording_id: int
) -> None:
    """Lock the last accepted hypothesis inside the lifecycle transaction."""
    state = getattr(transcript, "settling_state", None) or {}
    if not state.get("last_decode_id") or state.get("phase") != "live":
        return
    from backend.utils.canonical_pipeline.live_settling import close_live_settling

    await db.run_sync(
        lambda session: close_live_settling(session, recording_id=recording_id)
    )


async def seal_live_settling(db: AsyncSession, recording: Recording) -> None:
    """End the live episode under the Transcript fence, so nothing publishes after.

    Stop calls this under the audio lock it already holds. Publishers hold
    Transcript only for their short publication, so the wait is brief.
    """
    transcript = await db.run_sync(
        lambda session: _load_transcript(session, recording.id, lock=True)
    )
    await close_live_words(db, transcript, recording.id)
    update_capture_settling_state(
        transcript,
        capture_epoch=recording.capture_epoch,
        sealed=True,
        phase="sealed",
    )
