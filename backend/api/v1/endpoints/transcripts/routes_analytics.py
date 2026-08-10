import logging

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user, get_db
from backend.models.user import User
from backend.services.meeting_analytics import compute_recording_analytics
from backend.utils.canonical_pipeline import (
    ensure_canonical_backfill,
    get_canonical_transcript_revision,
)

from .helpers import _get_owned_recording, _get_recording_transcript
from .router import router

logger = logging.getLogger(__name__)


class RecordingAnalyticsRead(BaseModel):
    recording_id: str
    # Present so a caller polling this endpoint can tell a recompute from an
    # unchanged read without diffing the payload.
    transcript_revision: int
    speakers: list[dict]
    metrics: dict
    attribution_warning: dict | None = None


def _backfill_and_compute(sync_session, recording) -> tuple[int, dict]:
    """Backfill, derive, and read the cursor in one synchronous block."""
    ensure_canonical_backfill(sync_session, recording.id)
    analytics = compute_recording_analytics(sync_session, recording)
    revision = get_canonical_transcript_revision(sync_session, recording.id)
    return revision, analytics


@router.get("/{recording_id}/analytics", response_model=RecordingAnalyticsRead)
async def get_recording_analytics(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Deterministic meeting analytics for a processed recording.

    Computed per request from canonical utterances rather than stored. That is
    affordable at this size and buys two properties worth more than a cache:
    every historical recording has analytics with no backfill, and a speaker
    merge or transcript edit is reflected on the next read with no
    invalidation path to get wrong.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    transcript = await _get_recording_transcript(db, recording.id)

    if transcript is None:
        raise HTTPException(status_code=404, detail="Transcript not found")

    revision, analytics = await db.run_sync(
        lambda sync_session: _backfill_and_compute(sync_session, recording)
    )
    await db.commit()

    return RecordingAnalyticsRead(
        recording_id=recording.public_id,
        transcript_revision=revision,
        speakers=analytics["speakers"],
        metrics=analytics["metrics"],
        attribution_warning=analytics["attribution_warning"],
    )
