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
    # The AI tier: topics and who led them, sentiment read from the words,
    # question/answer mapping, and decision ownership. Absent is normal; so is
    # a status of "unavailable", which means the install has no AI provider
    # configured rather than that anything failed.
    ai: dict | None = None
    ai_status: str = "pending"
    ai_error_message: str | None = None
    ai_stale: bool = False
    # Overlapping speech measured from the audio. It has no staleness story:
    # it depends only on the audio, which never changes after processing. Its
    # status lives inside the block because the block is the whole lifecycle.
    audio_overlap: dict | None = None
    audio_overlap_status: str = "pending"
    audio_overlap_error_message: str | None = None
    # Each linked person's usual delivery across this user's other measured
    # meetings, keyed by speaker_key. Only speakers linked to a person with
    # enough comparable history appear; sample counts ride along so the
    # interface can say how much stands behind "their usual".
    delivery_baselines: dict = {}


class AnalyticsGenerateResponse(BaseModel):
    recording_id: str
    delivery_status: str


class AnalyticsAiGenerateResponse(BaseModel):
    recording_id: str
    ai_status: str


def _is_stale(block: dict | None, watermark: object, revision: int) -> bool:
    """Whether a stored tier was produced against an older transcript.

    Staleness is disclosed, never acted on: regenerating either tier costs the
    user something (audio analysis, or AI quota), so the interface offers the
    button and the user decides.
    """
    if block is None or watermark is None:
        return False
    try:
        return revision > int(watermark)
    except (TypeError, ValueError):
        return False


def _backfill_and_compute(sync_session, recording) -> tuple[int, dict, dict]:
    """Backfill, derive, and read the cursor in one synchronous block."""
    from backend.services.meeting_analytics.baselines import (
        compute_delivery_baselines,
    )

    ensure_canonical_backfill(sync_session, recording.id)
    analytics = compute_recording_analytics(sync_session, recording)
    revision = get_canonical_transcript_revision(sync_session, recording.id)
    baselines = compute_delivery_baselines(
        sync_session,
        user_id=recording.user_id,
        recording_id=recording.id,
        speakers=analytics["speakers"],
    )
    return revision, analytics, baselines


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

    revision, analytics, baselines = await db.run_sync(
        lambda sync_session: _backfill_and_compute(sync_session, recording)
    )
    await db.commit()

    payload = transcript.analytics_payload or {}
    delivery = payload.get("delivery")
    ai = payload.get("ai")
    audio_overlap = payload.get("audio_overlap")

    return RecordingAnalyticsRead(
        recording_id=recording.public_id,
        transcript_revision=revision,
        speakers=analytics["speakers"],
        metrics=analytics["metrics"],
        attribution_warning=analytics["attribution_warning"],
        delivery=delivery,
        delivery_status=transcript.analytics_status,
        delivery_error_message=transcript.analytics_error_message,
        delivery_stale=_is_stale(delivery, payload.get("event_watermark"), revision),
        ai=ai,
        ai_status=transcript.analytics_ai_status,
        ai_error_message=transcript.analytics_ai_error_message,
        # Each tier carries its own watermark, so editing the transcript after
        # measuring delivery but before analysing does not mark the analysis
        # stale as well.
        ai_stale=_is_stale(ai, (ai or {}).get("event_watermark"), revision),
        audio_overlap=(
            audio_overlap
            if (audio_overlap or {}).get("status") == "completed"
            else None
        ),
        audio_overlap_status=(audio_overlap or {}).get("status", "pending"),
        audio_overlap_error_message=(audio_overlap or {}).get("error_message"),
        delivery_baselines=baselines,
    )


@router.post(
    "/{recording_id}/analytics/generate", response_model=AnalyticsGenerateResponse
)
async def generate_recording_analytics(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Measure a recording's audio: delivery descriptors and overlap.

    Manual rather than automatic for meetings that predate the features: the
    alternative is a sweep that reads every recording's audio in every library
    at upgrade time, on hardware the user owns, for tiers most meetings will
    never be asked about. One button dispatches both measured tiers because
    they answer the same request -- "measure this meeting's audio" -- even
    though they run on different lanes.
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

    # Overlap is genuinely best-effort on top: the delivery dispatch above
    # succeeded, so the broker is reachable, and a failure here should not
    # fail a request whose primary work is already queued.
    await dispatch_task_best_effort(
        "backend.worker.tasks.compute_overlap_analytics_task",
        args=[recording.id],
        context="overlap analytics",
    )

    return AnalyticsGenerateResponse(
        recording_id=recording.public_id, delivery_status="generating"
    )


@router.post(
    "/{recording_id}/analytics/ai/generate", response_model=AnalyticsAiGenerateResponse
)
async def generate_recording_ai_analytics(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run the AI analytics pass for a recording.

    Always on request. This one call spends the user's own AI quota, and the
    questions it answers are not asked of most meetings, so running it for
    every recording at finalise would charge everyone for something few would
    read. A meeting that has never been analysed simply reports so.
    """
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    transcript = await _get_recording_transcript(db, recording.id)

    if transcript is None:
        raise HTTPException(status_code=404, detail="Transcript not found")

    if transcript.analytics_ai_status == "generating":
        raise HTTPException(
            status_code=409, detail="This meeting is already being analysed"
        )

    # As with delivery, the worker owns the status from here. Setting it in the
    # handler would strand the transcript in "generating" when a dispatch never
    # reaches the broker, and could overwrite a run that finished first.
    dispatched = await dispatch_task_best_effort(
        "backend.worker.tasks.compute_meeting_analysis_task",
        args=[recording.id],
        context="meeting analysis",
    )
    if dispatched is None:
        raise HTTPException(
            status_code=503,
            detail="Analysis could not be queued because the task broker is unreachable",
        )

    return AnalyticsAiGenerateResponse(
        recording_id=recording.public_id, ai_status="generating"
    )
