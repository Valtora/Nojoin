"""The ``live_speaker_labels`` setting, readable without the label engine.

The live task reads it before importing ``step``, so with labels explicitly
off a live run imports none of the engine, its state code or SciPy. The
shipped ``auto`` default runs labels only when the step finds CUDA.
"""

from __future__ import annotations

from backend.utils.config_manager import config_manager

LIVE_SPEAKER_LABELS_KEY = "live_speaker_labels"
LIVE_SPEAKER_LABELS_AUTO = "auto"


def live_speaker_labels_requested() -> bool:
    """Whether ``live_speaker_labels`` is ``auto``. Any other value is off."""
    mode = str(config_manager.get(LIVE_SPEAKER_LABELS_KEY, "off") or "off")
    return mode.strip().lower() == LIVE_SPEAKER_LABELS_AUTO
