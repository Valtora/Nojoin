"""Work dispatched once a recording has finished processing.

Collected here rather than inline at the end of ``process_recording_task`` so
there is one place that answers "what else happens when a meeting completes",
and so adding to that list does not mean editing the orchestrator.

Everything here shares one contract: it is dispatched, never run inline, and
its failure must not reach a recording that has already finished. The
orchestrator holds the single-slot GPU lane; the one follow-up that benefits
from a GPU (overlap detection) is dispatched back onto that lane as its own
task rather than run inline, so it queues behind any meeting waiting to
process instead of in front of it.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def dispatch_post_processing_followups(recording_id: int) -> None:
    """Queue the follow-on work for a recording that just completed."""
    from backend.worker.tasks import (
        compute_delivery_analytics_task,
        compute_overlap_analytics_task,
        index_transcript_task,
    )

    index_transcript_task.delay(recording_id)
    compute_delivery_analytics_task.delay(recording_id)
    compute_overlap_analytics_task.delay(recording_id)
    dispatch_meeting_analysis_if_opted_in(recording_id)


def dispatch_meeting_analysis_if_opted_in(recording_id: int) -> bool:
    """Queue the AI analytics tier for an owner who asked for it automatically.

    Off unless the owner's ``auto_analyse_meetings`` setting is on, and then
    only within their daily budget, which is spent here as it is by the
    button. Returns True when a run was queued. Nothing raised here reaches
    the recording, which has already finished.
    """
    from backend.core.db import get_sync_session
    from backend.models.ai_analysis import RUN_SOURCE_AUTO
    from backend.models.recording import Recording
    from backend.models.user import User
    from backend.services.ai_analysis_budget import claim_run_sync
    from backend.utils.analytics_preferences import is_auto_analyse_meetings_enabled
    from backend.worker.tasks import compute_meeting_analysis_task

    try:
        with get_sync_session() as session:
            recording = session.get(Recording, recording_id)
            owner = session.get(User, recording.user_id) if recording else None
            if owner is None or not is_auto_analyse_meetings_enabled(owner.settings):
                return False
            if not claim_run_sync(session, owner, recording_id, RUN_SOURCE_AUTO):
                logger.info(
                    "Recording %s not analysed automatically: the owner's daily "
                    "AI analysis budget is used up.",
                    recording_id,
                )
                return False
            session.commit()
    except Exception:  # noqa: BLE001 -- boundary: a follow-up never fails a finished recording
        logger.warning(
            "Could not decide whether to analyse recording %s automatically.",
            recording_id,
            exc_info=True,
        )
        return False

    compute_meeting_analysis_task.delay(recording_id)
    return True
