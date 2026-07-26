"""Browser live-capture audio contract shared by transcode and live analysis.

This module is deliberately dependency-free. The API image ships without torch,
so anything on a request path that needs the live-capture source vocabulary --
notably the utterance serializer -- must be able to reach it without importing
``live_transcribe``.
"""

from __future__ import annotations

from typing import Any

BROWSER_LIVE_SAMPLE_RATE_HZ = 16_000
BROWSER_LIVE_CHANNEL_COUNT = 2

BROWSER_LIVE_SYSTEM_CHANNEL_INDEX = 0
BROWSER_LIVE_MICROPHONE_CHANNEL_INDEX = 1

BROWSER_LIVE_SYSTEM_SOURCE = "system"
BROWSER_LIVE_MICROPHONE_SOURCE = "microphone"

BROWSER_LIVE_SOURCE_NAME_BY_CHANNEL = {
    BROWSER_LIVE_SYSTEM_CHANNEL_INDEX: BROWSER_LIVE_SYSTEM_SOURCE,
    BROWSER_LIVE_MICROPHONE_CHANNEL_INDEX: BROWSER_LIVE_MICROPHONE_SOURCE,
}

# How much weight the live lane places on its channel-dominance reading for a
# given speech region. Only CLEAR means one source demonstrably carried the
# region; OVERLAP means both were active and NONE means neither dominated.
LIVE_SOURCE_AUTHORITY_CLEAR = "clear"
LIVE_SOURCE_AUTHORITY_OVERLAP = "overlap"
LIVE_SOURCE_AUTHORITY_NONE = "none"

_KNOWN_LIVE_SOURCES = frozenset(
    {BROWSER_LIVE_SYSTEM_SOURCE, BROWSER_LIVE_MICROPHONE_SOURCE}
)


def source_channel_from_confidence_payload(payload: Any) -> str | None:
    """Which capture channel carried this utterance, or None if unattributable.

    Reads the live lane's persisted ``source_channel_evidence`` and returns a
    source name only when that evidence is CLEAR. Overlapping speech and regions
    with no dominant channel deliberately return None: the caller renders no
    label at all rather than guessing, since a confident wrong attribution is
    worse than none.

    Note this describes the *audio channel*, never a person. A capture with no
    shared tab audio still carries every voice in the room on the microphone
    channel, so "microphone" must never be presented as "you".
    """
    if not isinstance(payload, dict):
        return None

    evidence = payload.get("source_channel_evidence")
    if not isinstance(evidence, dict):
        return None

    if evidence.get("authority") != LIVE_SOURCE_AUTHORITY_CLEAR:
        return None

    dominant_source = evidence.get("dominant_source")
    if dominant_source in _KNOWN_LIVE_SOURCES:
        return str(dominant_source)

    return None
