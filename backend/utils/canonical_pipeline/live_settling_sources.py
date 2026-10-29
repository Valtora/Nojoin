"""Derive a live line's source badge from its own word audio evidence."""

from math import sqrt
from typing import Any

from backend.processing.browser_live_audio import (
    BROWSER_LIVE_SOURCE_NAME_BY_CHANNEL,
    source_channel_from_confidence_payload,
)
from backend.processing.live_audio_buffer import (
    LIVE_SOURCE_DOMINANCE_RATIO_THRESHOLD,
    LIVE_SOURCE_DOMINANT_SHARE_THRESHOLD,
    LIVE_SOURCE_OVERLAP_SHARE_THRESHOLD,
    _build_live_source_channel_evidence,
)


def partition_source_metadata(words: list[dict[str, Any]]) -> dict[str, Any]:
    """Pool channel RMS energy by sample count without reading audio again."""
    activities = [
        word["source_channel_activity"]
        for word in words
        if word.get("source_channel_activity")
    ]
    if not activities:
        return {}
    channels = max(int(activity.get("channel_count", 1)) for activity in activities)
    samples = sum(int(activity.get("sample_count", 0)) for activity in activities)
    activity: dict[str, Any] = {"channel_count": channels, "sample_count": samples}
    if channels > 1 and samples > 0:
        _channel_summary(activity, activities, channels, samples)
    evidence = _build_live_source_channel_evidence(activity, {}).to_payload()
    return {"source_channel_activity": activity, "source_channel_evidence": evidence}


def words_source_channel(words: list[dict[str, Any]]) -> str | None:
    """The source badge a line of these words shows, or None when unattributed."""
    return source_channel_from_confidence_payload(partition_source_metadata(words))


def _channel_summary(
    activity: dict[str, Any],
    activities: list[dict[str, Any]],
    channels: int,
    samples: int,
) -> None:
    rms = [
        sqrt(
            sum(
                float(item.get("channel_rms", [0] * channels)[index]) ** 2
                * int(item.get("sample_count", 0))
                for item in activities
                if len(item.get("channel_rms", [])) > index
            )
            / samples
        )
        for index in range(channels)
    ]
    total = sum(rms)
    shares = [value / total if total else 0 for value in rms]
    primary = max(range(channels), key=lambda index: shares[index])
    first, second = sorted(shares, reverse=True)[:2]
    primary_source = BROWSER_LIVE_SOURCE_NAME_BY_CHANNEL.get(
        primary, f"channel_{primary}"
    )
    dominant = first >= LIVE_SOURCE_DOMINANT_SHARE_THRESHOLD and (
        second == 0 or first / second >= LIVE_SOURCE_DOMINANCE_RATIO_THRESHOLD
    )
    activity.update(
        {
            "channel_rms": [round(value, 6) for value in rms],
            "channel_shares": [round(value, 4) for value in shares],
            "primary_channel": primary,
            "primary_source": primary_source,
            "primary_share": round(first, 4),
            "secondary_share": round(second, 4),
            "dominant_source": primary_source if dominant else None,
            "source_overlap": second >= LIVE_SOURCE_OVERLAP_SHARE_THRESHOLD,
        }
    )
