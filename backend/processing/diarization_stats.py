"""Per-speaker statistics for a diarization result.

Kept free of heavy imports: it only duck-types the pyannote ``Annotation`` API
(``itertracks``/``get_overlap``) so it can be exercised without loading torch.

The numbers here answer the question the phantom-speaker filter cannot: when a
recording comes back with more speakers than the user expected, are the extra
clusters negligible fragments or do they hold real speech?
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def summarize_diarization_speakers(
    annotation, max_speakers: int | None = None
) -> dict[str, Any]:
    """Summarise speech duration and segment count per speaker label."""
    per_label: dict[str, dict[str, float]] = {}

    if annotation is not None:
        for segment, _track, label in annotation.itertracks(yield_label=True):
            entry = per_label.setdefault(str(label), {"speech_s": 0.0, "segments": 0})
            entry["speech_s"] += float(segment.duration)
            entry["segments"] += 1

    total_speech_s = sum(entry["speech_s"] for entry in per_label.values())

    overlapped_speech_s = None
    if annotation is not None:
        try:
            overlapped_speech_s = round(
                sum(float(seg.duration) for seg in annotation.get_overlap()), 3
            )
        except Exception as e:  # noqa: BLE001 -- boundary: overlap reporting is best-effort
            logger.debug("Could not compute overlapped speech for metrics: %s", e)

    speakers = [
        {
            "label": label,
            "speech_s": round(entry["speech_s"], 3),
            "segment_count": int(entry["segments"]),
            "share": (
                round(entry["speech_s"] / total_speech_s, 4)
                if total_speech_s > 0
                else 0.0
            ),
        }
        for label, entry in sorted(
            per_label.items(), key=lambda kv: kv[1]["speech_s"], reverse=True
        )
    ]

    return {
        "speaker_count": len(per_label),
        "total_speech_s": round(total_speech_s, 3),
        "overlapped_speech_s": overlapped_speech_s,
        "max_speakers_requested": max_speakers,
        "cap_applied": max_speakers is not None,
        "cap_binding": (
            bool(max_speakers is not None and len(per_label) >= max_speakers)
        ),
        "speakers": speakers,
    }


def speech_seconds_by_label(annotation) -> dict[str, float]:
    """Total speech seconds per diarization label.

    Returns an empty map rather than raising when the annotation is absent or
    not iterable: this feeds survivor selection in the merge pass, which must
    degrade to its other tiebreaks rather than abort the whole pass.
    """
    totals: dict[str, float] = {}
    if annotation is None:
        return totals
    try:
        for segment, _track, label in annotation.itertracks(yield_label=True):
            totals[str(label)] = totals.get(str(label), 0.0) + float(segment.duration)
    except Exception as e:  # noqa: BLE001 -- boundary: survivor evidence is optional
        logger.debug("Could not compute per-speaker speech duration: %s", e)
        return {}
    return totals
