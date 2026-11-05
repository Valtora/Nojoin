"""The one rule for a name that is only a placeholder for a speaker.

A placeholder names no one: the notes prompts, voiceprint matching and the
rename and promote routes all refuse it as a person's name, and must agree on
what it is. Standard library only, since the API, the worker and the processing
modules all import it.
"""

from __future__ import annotations

import re

# ``N:L0`` and ``C:<label>`` are the labels final diarisation with a native
# engine gives a speaker (``processing/diarization_union.py``); until named, they
# are as much a placeholder as ``SPEAKER_00``. "New Voice N" is a name earlier
# releases gave unmatched voices.
PLACEHOLDER_SPEAKER_PATTERN = re.compile(
    r"^(SPEAKER_\d+|Speaker \d+|Unknown|New Voice .*|[NC]:\S+)$",
    re.IGNORECASE,
)


def is_placeholder_speaker_name(name: str | None) -> bool:
    """Whether ``name`` is empty or a placeholder rather than a person's name."""
    cleaned_name = (name or "").strip()
    if not cleaned_name:
        return True
    return bool(PLACEHOLDER_SPEAKER_PATTERN.match(cleaned_name))
