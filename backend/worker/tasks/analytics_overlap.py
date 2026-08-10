"""Measured-overlap task: detect overlapping speech in a recording's audio.

Runs on the GPU lane, unlike the delivery task: it runs the segmentation
model, which the finalise pipeline keeps resident there, and a recording
being measured has already finished processing, so the single slot is not
holding up a live meeting. On a CPU-only install the same lane runs it more
slowly, which is the same trade the rest of the pipeline makes.

The result lives under its own key of ``analytics_payload`` with its own
status inside the block rather than a new column: overlap depends only on
the audio, which never changes after processing, so it has no staleness
story and no lifecycle beyond pending/generating/completed/error.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import select

from backend.celery_app import celery_app
from backend.core.db import get_sync_session
from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.processing.pipeline_metrics import pipeline_metric_timer
from backend.utils.analytics_payload import AUDIO_OVERLAP_KEY, merge_analytics_payload
from backend.utils.time import utc_now

logger = logging.getLogger(__name__)


def _write_overlap_block(session, recording_id: int, block: dict[str, Any]) -> None:
    """Merge one overlap block under the row lock the shared column requires."""
    transcript = session.exec(
        select(Transcript)
        .where(Transcript.recording_id == recording_id)
        .with_for_update()
    ).first()
    if transcript is None:
        return
    transcript.analytics_payload = merge_analytics_payload(
        transcript.analytics_payload, {AUDIO_OVERLAP_KEY: block}
    )
    flag_modified(transcript, "analytics_payload")
    session.add(transcript)
    session.commit()


@celery_app.task(name="backend.worker.tasks.compute_overlap_analytics_task")
def compute_overlap_analytics_task(recording_id: int) -> dict[str, Any]:
    """Detect and persist overlapping-speech regions for one recording."""
    from backend.processing.audio_overlap import measure_audio_overlap
    from backend.utils.config_manager import config_manager

    with get_sync_session() as session:
        recording = session.get(Recording, recording_id)
        if recording is None:
            return {"status": "skipped", "reason": "recording_not_found"}

        transcript = session.exec(
            select(Transcript).where(Transcript.recording_id == recording_id)
        ).first()
        if transcript is None:
            return {"status": "skipped", "reason": "transcript_not_found"}

        if not recording.audio_path or not os.path.exists(recording.audio_path):
            _write_overlap_block(
                session,
                recording_id,
                {
                    "status": "error",
                    "error_message": (
                        "The recording's audio is no longer available, so "
                        "overlapping speech cannot be measured."
                    ),
                },
            )
            return {"status": "skipped", "reason": "audio_missing"}

        _write_overlap_block(session, recording_id, {"status": "generating"})

        try:
            hf_token = os.environ.get("HF_TOKEN") or config_manager.get("hf_token")
            with pipeline_metric_timer(
                stage="overlap_analytics",
                recording_id=recording_id,
                payload={},
                log=logger,
            ) as metric:
                block = measure_audio_overlap(recording.audio_path, hf_token)
                metric["payload"].update(
                    {
                        "region_count": block["region_count"],
                        "total_overlap_ms": block["total_overlap_ms"],
                    }
                )
        except Exception as exc:  # noqa: BLE001 -- boundary: analytics must never fail a meeting
            logger.warning(
                "Overlap analytics failed for recording %s: %s",
                recording_id,
                exc,
                exc_info=True,
            )
            session.rollback()
            _write_overlap_block(
                session,
                recording_id,
                {
                    "status": "error",
                    "error_message": (
                        "Overlapping speech could not be measured for this recording."
                    ),
                },
            )
            return {"status": "error", "recording_id": recording_id}

        block["status"] = "completed"
        block["computed_at"] = utc_now().isoformat()
        _write_overlap_block(session, recording_id, block)

    return {"status": "success", "recording_id": recording_id}
