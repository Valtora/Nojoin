"""Work dispatched once a recording has finished processing.

Collected here rather than inline at the end of ``process_recording_task`` so
there is one place that answers "what else happens when a meeting completes",
and so adding to that list does not mean editing the orchestrator.

Everything here shares one contract: it is dispatched, never run inline, and
its failure must not reach a recording that has already finished. The
orchestrator holds the single-slot GPU lane, and none of this needs a GPU.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def dispatch_post_processing_followups(recording_id: int) -> None:
    """Queue the follow-on work for a recording that just completed."""
    from backend.worker.tasks import (
        compute_delivery_analytics_task,
        index_transcript_task,
    )

    index_transcript_task.delay(recording_id)
    compute_delivery_analytics_task.delay(recording_id)
