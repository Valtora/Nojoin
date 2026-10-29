"""Live speaker naming: its own step, queued by live publication.

A publication that adds or changes lines queues a check while a live label is
still unnamed (``LivePublicationResult.queue_live_naming``), whatever Meeting
Edge is doing. At most one check waits per recording: the dispatcher claims
the slot before queueing and the check frees it as it starts, so lines
published during a check queue the next one.
"""

import logging

from backend.core.single_flight import claim, release_claim
from backend.utils.speaker_names.live_suggestions import (
    refresh_live_speaker_suggestions,
)
from backend.worker.tasks.constants import DatabaseTask, celery_app

logger = logging.getLogger(__name__)

LIVE_NAMING_TASK = "backend.worker.tasks.refresh_live_speaker_names_task"
# Frees the slot if a queued check is lost, as a worker restart can do.
LIVE_NAMING_QUEUED_TTL_SECONDS = 300


def _queued_key(recording_id: int) -> str:
    return f"live-naming-queued:{recording_id}"


def queue_live_naming_check(recording_id: int) -> None:
    """Queue a check unless one already waits; a dispatch failure is logged."""
    key = _queued_key(recording_id)
    if not claim(key, ttl_seconds=LIVE_NAMING_QUEUED_TTL_SECONDS):
        return
    try:
        celery_app.send_task(LIVE_NAMING_TASK, args=[recording_id])
    except Exception as exc:  # noqa: BLE001 -- boundary: dispatch is best-effort
        release_claim(key)
        logger.warning(
            "Failed to queue live naming for recording %s: %s", recording_id, exc
        )


@celery_app.task(name=LIVE_NAMING_TASK, base=DatabaseTask, bind=True)
def refresh_live_speaker_names_task(self, recording_id: int) -> None:
    """Propose names for unnamed live labels from the current lines."""
    release_claim(_queued_key(recording_id))
    refresh_live_speaker_suggestions(self.session, recording_id)


__all__ = ["queue_live_naming_check", "refresh_live_speaker_names_task"]
