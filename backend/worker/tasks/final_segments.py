"""Combine stage of the finalize pipeline: ASR + diarization into final segments.

Extracted from ``pipeline.py`` to keep that module within its size budget. The
``transcript_utils`` import stays inside the function, as it was in the
pipeline, so the stage tests can stub that module per test.
"""

import logging
from collections.abc import Mapping
from typing import Any

from backend.processing.pipeline_metrics import record_pipeline_metric

logger = logging.getLogger(__name__)


def combine_and_consolidate_segments(
    transcription_result: dict,
    diarization_result,
    *,
    enable_diarization: bool,
    recording_id: int,
    config: Mapping[str, Any] | None = None,
) -> list[dict]:
    """Merge ASR + diarization into consolidated final segments.

    When no combined result is available (combination skipped or failed) every
    ASR segment is emitted pinned to the ``UNKNOWN`` speaker, preserving any
    ``id``/``words`` payload. This is the load-bearing fallback that keeps a
    transcript even without usable diarization. ``config`` (the owner's merged
    settings) supplies the single-word flip smoothing limits.
    """
    from backend.utils.transcript_utils import (
        combine_transcription_diarization,
        consolidate_diarized_transcript,
    )

    combined_segments = []
    if diarization_result:
        combined_segments = combine_transcription_diarization(
            transcription_result, diarization_result, config
        )
    else:
        logger.info("Diarization result missing or disabled. Skipping combination.")

    logger.info(
        f"Combined segments count: {len(combined_segments) if combined_segments else 0}"
    )

    if not combined_segments:
        if enable_diarization and diarization_result:
            logger.warning(
                "Combination failed despite having diarization result. Using raw transcription segments with UNKNOWN speaker."
            )
        else:
            logger.info(
                "Using raw transcription segments (Diarization disabled or failed)."
            )

        for seg in transcription_result.get("segments", []):
            fallback_segment = {
                "start": seg["start"],
                "end": seg["end"],
                "speaker": "UNKNOWN",
                "text": seg["text"].strip(),
            }
            if seg.get("id"):
                fallback_segment["id"] = seg["id"]
            if seg.get("words"):
                fallback_segment["words"] = seg["words"]
            combined_segments.append(fallback_segment)

    final_segments = consolidate_diarized_transcript(combined_segments)
    record_pipeline_metric(
        stage="final_segments_built",
        recording_id=recording_id,
        payload={"segment_count": len(final_segments)},
        log=logger,
    )
    logger.info("Final segments after consolidation: %s", len(final_segments))
    return final_segments
