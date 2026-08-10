"""Deterministic meeting analytics derived from canonical transcript rows.

The tier exposed here needs no AI provider, reads no audio, and stores
nothing, so it is available on every recording in a library the moment the
feature ships rather than only on recordings processed after it.
"""

from .metrics import (
    Turn,
    UtteranceRow,
    build_turns,
    compute_deterministic_metrics,
)
from .query import compute_recording_analytics
from .warnings import build_attribution_warning

__all__ = [
    "Turn",
    "UtteranceRow",
    "build_attribution_warning",
    "build_turns",
    "compute_deterministic_metrics",
    "compute_recording_analytics",
]
