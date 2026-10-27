"""Shared limits for completed-region and settled live lines."""

# Default maximum length (seconds) of a trailing utterance before a forced decode.
DEFAULT_FORCED_MAX = 8.0
# Absolute maximum length (seconds) of any emitted provisional live segment.
DEFAULT_MAX_SEGMENT_S = 30.0
# Keep natural Q&A handovers separate without fragmenting normal short pauses.
LIVE_MIN_SILENCE_MS = 320
