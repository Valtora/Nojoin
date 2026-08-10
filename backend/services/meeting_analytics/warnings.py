"""Decide when analytics should disclose that speaker attribution may be wrong.

Every figure on the analytics surface is attributed to a speaker, and
diarisation splits one person into several often enough that the project
measured it: across this project's own library, 29% of same-person voiceprint
pairs scored below the merge threshold. A confidently wrong talk share is the
worst output this feature can produce, so the conditions that predict one are
reported rather than left for the user to notice.

The warning is conditional on purpose. A banner shown on every recording is
read as boilerplate within a week and then protects nobody on the meeting where
it mattered.
"""

from __future__ import annotations

from typing import Any

from .constants import (
    HIGH_OVERLAP_SHARE_THRESHOLD,
    LOW_SHARE_SPEAKER_COUNT_TRIGGER,
    LOW_SHARE_SPEAKER_THRESHOLD,
)


def _low_share_speakers(metrics: dict[str, Any]) -> list[str]:
    talk_time = metrics.get("talk_time", {})
    return [
        speaker_key
        for speaker_key, figures in talk_time.items()
        if 0 < figures.get("share_of_speech", 0.0) < LOW_SHARE_SPEAKER_THRESHOLD
    ]


def build_attribution_warning(
    *,
    metrics: dict[str, Any],
    speakers: list[dict[str, Any]],
    max_speakers: int | None,
) -> dict[str, Any] | None:
    """Return a structured warning, or None when nothing suggests a problem.

    Reasons are returned as codes rather than prose so the interface owns the
    wording and the MCP surface stays stable for an assistant to branch on.
    """
    reasons: list[dict[str, Any]] = []

    fragments = _low_share_speakers(metrics)
    if len(fragments) >= LOW_SHARE_SPEAKER_COUNT_TRIGGER:
        reasons.append(
            {
                "code": "low_share_clusters",
                "speaker_count": len(fragments),
                "speaker_keys": fragments,
            }
        )

    overlap_share = metrics.get("overlap", {}).get("overlap_share", 0.0)
    if overlap_share >= HIGH_OVERLAP_SHARE_THRESHOLD:
        reasons.append({"code": "high_overlap", "overlap_share": overlap_share})

    # A cap that bound is the case the user themselves suspected: they set an
    # upper bound because they expected over-splitting, and it was reached.
    speaker_count = len(speakers)
    if max_speakers is not None and speaker_count >= max_speakers:
        reasons.append(
            {
                "code": "speaker_cap_bound",
                "max_speakers": max_speakers,
                "speaker_count": speaker_count,
            }
        )

    unnamed = [
        speaker["speaker_key"] for speaker in speakers if not speaker.get("is_named")
    ]
    if unnamed:
        reasons.append({"code": "unnamed_speakers", "speaker_keys": unnamed})

    if not reasons:
        return None
    return {"reasons": reasons}
