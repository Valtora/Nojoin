import logging

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_current_user, get_db
from backend.core.task_dispatch import dispatch_task_best_effort
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
    # The measured delivery tier, when it has been generated for this
    # recording. Absent is a normal state, not an error: it costs audio
    # analysis, so it is produced once per recording rather than per read.
    delivery: dict | None = None
    delivery_status: str = "pending"
    delivery_error_message: str | None = None
    # True when the transcript has moved since delivery was measured, so the
    # figures describe a transcript that no longer exists. Never regenerated
    # automatically: rereading the audio is work the user should ask for.
    delivery_stale: bool = False


class AnalyticsGenerateResponse(BaseModel):
    recording_id: str
    delivery_status: str


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

    payload = transcript.analytics_payload or {}
    delivery = payload.get("delivery")
    watermark = payload.get("event_watermark")

    return RecordingAnalyticsRead(
        recording_id=recording.public_id,
        transcript_revision=revision,
        speakers=analytics["speakers"],
        metrics=analytics["metrics"],
        attribution_warning=analytics["attribution_warning"],
        delivery=delivery,
        delivery_status=transcript.analytics_status,
        delivery_error_message=transcript.analytics_error_message,
        delivery_stale=bool(
            delivery is not None and watermark is not None and revision > int(watermark)
        ),
    )


@router.post(
    "/{recording_id}/analytics/generate", response_model=AnalyticsGenerateResponse
)
async def generate_recording_analytics(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Measure delivery descriptors for a recording, or refresh stale ones.

    Manual rather than automatic for meetings that predate the feature: the
    alternative is a sweep that reads every recording's audio in every library
    at upgrade time, on hardware the user owns, for a tier most meetings will
    never be asked about.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    transcript = await _get_recording_transcript(db, recording.id)

    if transcript is None:
        raise HTTPException(status_code=404, detail="Transcript not found")

    if transcript.analytics_status == "generating":
        raise HTTPException(
            status_code=409, detail="Delivery analytics are already being measured"
        )

    # The worker owns analytics_status from here, and this handler deliberately
    # writes none of it. Setting "generating" here instead would lose both ways:
    # a dispatch that never reached the broker would leave the transcript
    # generating forever, and a run that finished before this request committed
    # would have its "completed" overwritten back to "generating".
    dispatched = await dispatch_task_best_effort(
        "backend.worker.tasks.compute_delivery_analytics_task",
        args=[recording.id],
        context="delivery analytics",
    )
    if dispatched is None:
        raise HTTPException(
            status_code=503,
            detail="Analytics could not be queued because the task broker is unreachable",
        )

    return AnalyticsGenerateResponse(
        recording_id=recording.public_id, delivery_status="generating"
    )
