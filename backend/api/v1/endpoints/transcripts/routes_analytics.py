import logging
from uuid import uuid4

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from backend.api.deps import get_current_user, get_db
from backend.core.task_dispatch import dispatch_task_best_effort
from backend.models.ai_analysis import RUN_SOURCE_USER
from backend.models.analytics_public import LiveRecordingAnalyticsRead
from backend.models.recording import IN_FLIGHT_TRANSCRIPT_STATUSES, Recording
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.services.ai_analysis_budget import (
    AiAnalysisBudgetExhausted,
    claim_run,
    get_budget,
)
from backend.services.meeting_analytics import compute_recording_analytics
from backend.services.meeting_analytics.live import compute_live_recording_analytics
from backend.utils.analytics_payload import merge_analytics_payload
from backend.utils.analytics_preferences import is_auto_remeasure_delivery_enabled
from backend.utils.canonical_pipeline import (
    ensure_canonical_backfill,
    get_canonical_transcript_revision,
)
from backend.utils.rate_limit import RATE_LIMIT_KEY_PREFIX, claim_once

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
    # figures describe a transcript that no longer exists. Re-measured on this
    # read when the user's auto_remeasure_delivery setting is on (the default),
    # in which case delivery_status reports "generating"; otherwise the figures
    # stay until the user asks.
    delivery_stale: bool = False
    # The AI tier: topics and who led them, sentiment read from the words,
    # question/answer mapping, and decision ownership. Absent is normal; so is
    # a status of "unavailable", which means the install has no AI provider
    # configured rather than that anything failed.
    ai: dict | None = None
    ai_status: str = "pending"
    ai_error_message: str | None = None
    ai_stale: bool = False
    # The caller's daily budget for the AI tier: limit (null for no cap),
    # used, remaining and the UTC reset time, so the tab can say why the
    # button is unavailable before a request is refused.
    ai_budget: dict = {}
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
    ai_generation: dict = {}


class RecordingAiAnalyticsRead(BaseModel):
    recording_id: str
    transcript_revision: int
    ai: dict | None = None
    ai_status: str = "pending"
    ai_error_message: str | None = None
    ai_stale: bool = False
    ai_budget: dict = {}
    ai_generation: dict = {}


# How long one read's re-measure claim holds off the next. Long enough to
# cover the queue wait and the measurement itself on a busy CPU lane, short
# enough that a dispatch the worker never picked up is retried on a later open.
AUTO_REMEASURE_CLAIM_WINDOW_S = 300


def _require_final_analytics(recording: Recording) -> None:
    if recording.status in IN_FLIGHT_TRANSCRIPT_STATUSES:
        raise HTTPException(
            status_code=409, detail="Final analytics are available after processing"
        )


@router.get("/{recording_id}/analytics/live", response_model=LiveRecordingAnalyticsRead)
async def get_live_recording_analytics(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> LiveRecordingAnalyticsRead:
    """Transcript estimates with no backfill, audio work or analysis budget."""
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    transcript = await _get_recording_transcript(db, recording.id)
    payload = await db.run_sync(
        lambda session: compute_live_recording_analytics(session, recording, transcript)
    )
    return LiveRecordingAnalyticsRead(**payload)


def _is_stale(block: dict | None, watermark: object, revision: int) -> bool:
    """Whether a stored tier was produced against an older transcript.

    Staleness is disclosed on both tiers. The AI tier is never acted on, since
    a run spends the user's own provider quota. The delivery tier is
    re-measured on read when the user has left that on (see
    _auto_remeasure_delivery): it costs audio analysis on the CPU lane, which
    is the user's own hardware, and the moment they open the tab is the moment
    stale figures would otherwise be read as current.
    """
    if block is None or watermark is None:
        return False
    try:
        return revision > int(watermark)
    except (TypeError, ValueError):
        return False


async def _auto_remeasure_delivery(recording, transcript, user, stale: bool) -> bool:
    """Re-measure stale delivery for this read; True when a run was queued.

    Nothing is queued for figures that are current, for a run already in
    progress, for a measurement that ended in error (the audio is gone or
    unreadable, and the button remains for a retry), or when the user has
    turned the behaviour off. A once-per-window claim stops the tab's polling
    from queueing the same run on every read before the worker has marked the
    transcript as generating.
    """
    if not stale or transcript.analytics_status in ("generating", "error"):
        return False
    if not is_auto_remeasure_delivery_enabled(user.settings):
        return False
    key = f"{RATE_LIMIT_KEY_PREFIX}:analytics:remeasure:{recording.id}"
    if not await claim_once(key, AUTO_REMEASURE_CLAIM_WINDOW_S):
        return False
    dispatched = await dispatch_task_best_effort(
        "backend.worker.tasks.compute_delivery_analytics_task",
        args=[recording.id],
        context="delivery re-measure after a transcript edit",
    )
    return dispatched is not None


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


async def read_recording_analytics(db: AsyncSession, recording, transcript) -> dict:
    """Everything the Analytics tab shows for a recording, as one dict.

    Shared by the tab's read and by exports, so an exported file cannot report
    different figures from the tab. Nothing here dispatches work or spends a
    budget; the tab's read layers those on top.
    """
    revision, analytics, baselines = await db.run_sync(
        lambda sync_session: _backfill_and_compute(sync_session, recording)
    )
    await db.commit()

    payload = transcript.analytics_payload or {}
    delivery = payload.get("delivery")
    ai = payload.get("ai")
    audio_overlap = payload.get("audio_overlap")
    return {
        "recording_id": recording.public_id,
        "transcript_revision": revision,
        "speakers": analytics["speakers"],
        "metrics": analytics["metrics"],
        "attribution_warning": analytics["attribution_warning"],
        "delivery": delivery,
        "delivery_status": transcript.analytics_status,
        "delivery_error_message": transcript.analytics_error_message,
        "delivery_stale": _is_stale(delivery, payload.get("event_watermark"), revision),
        "ai": ai,
        "ai_status": transcript.analytics_ai_status,
        "ai_error_message": transcript.analytics_ai_error_message,
        # Each tier carries its own watermark, so editing the transcript after
        # measuring delivery but before analysing does not mark the analysis
        # stale as well.
        "ai_stale": _is_stale(ai, (ai or {}).get("event_watermark"), revision),
        "audio_overlap": (
            audio_overlap
            if (audio_overlap or {}).get("status") == "completed"
            else None
        ),
        "audio_overlap_status": (audio_overlap or {}).get("status", "pending"),
        "audio_overlap_error_message": (audio_overlap or {}).get("error_message"),
        "delivery_baselines": baselines,
    }


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
    _require_final_analytics(recording)
    transcript = await _get_recording_transcript(db, recording.id)

    if transcript is None:
        raise HTTPException(status_code=404, detail="Transcript not found")

    read = await read_recording_analytics(db, recording, transcript)
    # The read commits its backfill. A new processing pass may have started
    # across that boundary, so check fresh state before dispatching final work.
    await db.refresh(recording)
    _require_final_analytics(recording)
    if await _auto_remeasure_delivery(
        recording, transcript, current_user, read["delivery_stale"]
    ):
        # Reported, not written: the worker owns the stored status, and the
        # tab polls while a tier reports generating, so it picks up the new
        # figures when the run lands.
        read["delivery_status"] = "generating"

    return RecordingAnalyticsRead(
        **read,
        ai_budget=(await get_budget(db, current_user)).as_dict(),
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
    _require_final_analytics(recording)
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
    result = await db.execute(
        select(Transcript)
        .where(Transcript.recording_id == recording.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    transcript = result.scalar_one_or_none()

    if transcript is None:
        raise HTTPException(status_code=404, detail="Transcript not found")

    if transcript.analytics_ai_status == "generating":
        raise HTTPException(
            status_code=409, detail="This meeting is already being analysed"
        )

    # The budget is spent here, where the run is queued: a refused request
    # logs nothing, and the log row commits with the dispatch below so a
    # dispatch that never reaches the broker spends nothing either.
    try:
        await claim_run(db, current_user, recording.id, RUN_SOURCE_USER)
    except AiAnalysisBudgetExhausted as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    # Keep the row locked until dispatch commits. The worker locks it before
    # checking ownership, so even a fast worker sees the accepted request.
    request_id = str(uuid4())
    generation = {"request_id": request_id}
    transcript.analytics_payload = merge_analytics_payload(
        transcript.analytics_payload, {"ai_generation": generation}
    )
    transcript.analytics_ai_status = "generating"
    transcript.analytics_ai_error_message = None
    db.add(transcript)
    await db.flush()
    dispatched = await dispatch_task_best_effort(
        "backend.worker.tasks.compute_meeting_analysis_task",
        args=[recording.id],
        kwargs={"request_id": request_id},
        context="meeting analysis",
    )
    if dispatched is None:
        await db.rollback()
        raise HTTPException(
            status_code=503,
            detail="Analysis could not be queued because the task broker is unreachable",
        )
    await db.commit()

    return AnalyticsAiGenerateResponse(
        recording_id=recording.public_id,
        ai_status="generating",
        ai_generation=generation,
    )


@router.get("/{recording_id}/analytics/ai", response_model=RecordingAiAnalyticsRead)
async def get_recording_ai_analytics(
    recording_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RecordingAiAnalyticsRead:
    """Read discussion analysis without final audio work or canonical backfill."""
    recording = await _get_owned_recording(db, recording_id, current_user.id)
    transcript = await _get_recording_transcript(db, recording.id)
    if transcript is None:
        raise HTTPException(status_code=404, detail="Transcript not found")
    revision = await db.run_sync(
        lambda session: get_canonical_transcript_revision(session, recording.id)
    )
    payload = transcript.analytics_payload or {}
    ai = payload.get("ai")
    return RecordingAiAnalyticsRead(
        recording_id=recording.public_id,
        transcript_revision=revision,
        ai=ai,
        ai_status=transcript.analytics_ai_status,
        ai_error_message=transcript.analytics_ai_error_message,
        ai_stale=_is_stale(ai, (ai or {}).get("event_watermark"), revision),
        ai_budget=(await get_budget(db, current_user)).as_dict(),
        ai_generation=payload.get("ai_generation") or {},
    )
