"""Delivery-analytics task: measure how people spoke on a processed recording.

Runs on the CPU lane rather than inline in the finalise pipeline. Finalise
holds the single-slot GPU lane, and this needs no GPU, so occupying that lane
to read a WAV would delay the next meeting for no benefit. It is the same task
the interface's per-recording "Analyse" action dispatches, so a recording made
before the feature existed and one made after it take the same path.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from sqlmodel import select

from backend.celery_app import celery_app
from backend.core.db import get_sync_session
from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.processing.pipeline_metrics import pipeline_metric_timer
from backend.utils.time import utc_now

logger = logging.getLogger(__name__)


def _load_delivery_utterances(session, recording_id: int) -> list:
    """Canonical utterances, reduced to what the delivery pass measures."""
    from backend.processing.delivery_descriptors import DeliveryUtterance
    from backend.utils.canonical_pipeline import list_active_utterances

    utterances = list_active_utterances(session, recording_id)
    rows = []
    for utterance in utterances:
        speaker_key = (
            f"rs:{utterance.recording_speaker_id}"
            if utterance.recording_speaker_id is not None
            else f"label:{utterance.speaker_label or 'unknown'}"
        )
        rows.append(
            DeliveryUtterance(
                speaker_key=speaker_key,
                start_ms=int(utterance.start_ms or 0),
                end_ms=int(utterance.end_ms or 0),
                word_count=len((utterance.text or "").split()),
                overlapped=bool(utterance.overlap_group_id),
            )
        )
    return rows


def _recording_uses_browser_capture(session, recording_id: int) -> bool:
    """Whether the two-channel source/microphone layout applies.

    Only a browser capture guarantees channel 0 is shared audio and channel 1
    is the microphone. An imported stereo file's channels are left and right,
    and reading them as capture sources would invent provenance.
    """
    from backend.models.pipeline import RecordingAudioChunk

    statement = (
        select(RecordingAudioChunk.id)
        .where(RecordingAudioChunk.recording_id == recording_id)
        .where(RecordingAudioChunk.source_kind == "browser")
        .limit(1)
    )
    return session.exec(statement).first() is not None


@celery_app.task(name="backend.worker.tasks.compute_delivery_analytics_task")
def compute_delivery_analytics_task(recording_id: int) -> dict[str, Any]:
    """Measure and persist delivery descriptors for one recording."""
    from backend.processing.delivery_descriptors import analyse_delivery
    from backend.utils.canonical_pipeline import get_canonical_transcript_revision

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
            # A recording whose audio is gone can never produce these, so this
            # is a terminal state the interface should report rather than a
            # failure worth retrying.
            transcript.analytics_status = "error"
            transcript.analytics_error_message = (
                "The recording's audio is no longer available, so delivery "
                "cannot be measured."
            )
            session.add(transcript)
            session.commit()
            return {"status": "skipped", "reason": "audio_missing"}

        transcript.analytics_status = "generating"
        transcript.analytics_error_message = None
        session.add(transcript)
        session.commit()

        try:
            utterances = _load_delivery_utterances(session, recording_id)
            browser_capture = _recording_uses_browser_capture(session, recording_id)
            watermark = get_canonical_transcript_revision(session, recording_id)

            with pipeline_metric_timer(
                stage="delivery_analytics",
                recording_id=recording_id,
                payload={"utterance_count": len(utterances)},
                log=logger,
            ) as metric:
                delivery = analyse_delivery(
                    recording.audio_path,
                    utterances,
                    browser_capture=browser_capture,
                )
                metric["payload"].update(
                    {
                        "speaker_count": len(delivery["speakers"]),
                        "skipped_overlapping": delivery["skipped_overlapping"],
                        "skipped_short": delivery["skipped_short"],
                    }
                )
        except Exception as exc:  # noqa: BLE001 -- boundary: analytics must never fail a meeting
            logger.warning(
                "Delivery analytics failed for recording %s: %s",
                recording_id,
                exc,
                exc_info=True,
            )
            session.rollback()
            transcript = session.exec(
                select(Transcript).where(Transcript.recording_id == recording_id)
            ).first()
            if transcript is not None:
                transcript.analytics_status = "error"
                transcript.analytics_error_message = (
                    "Delivery could not be measured for this recording."
                )
                session.add(transcript)
                session.commit()
            return {"status": "error", "recording_id": recording_id}

        transcript.analytics_payload = {
            "delivery": delivery,
            "method_version": delivery["method_version"],
            "computed_at": utc_now().isoformat(),
            # The cursor this was measured against. A later cursor means the
            # transcript moved underneath it, which the interface reports as
            # stale rather than silently recomputing: rereading the audio is
            # work the user should ask for.
            "event_watermark": watermark,
        }
        transcript.analytics_status = "completed"
        transcript.analytics_error_message = None
        session.add(transcript)
        session.commit()

    return {"status": "success", "recording_id": recording_id}
