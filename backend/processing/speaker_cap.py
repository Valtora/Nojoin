"""Shared validation for the optional per-recording speaker cap.

Deliberately free of heavy imports so the API layer can validate a submitted cap
without pulling torch or pyannote into the request process.

The cap is applied as pyannote's ``max_speakers`` and never as ``num_speakers``.
An upper bound and an exact count behave identically when the user's number is
right, but when the user overcounts, ``max_speakers`` still returns the true
lower number whereas ``num_speakers`` forces a split -- which is the
over-clustering failure this feature exists to prevent.
"""

from __future__ import annotations

MIN_SPEAKER_CAP = 1
MAX_SPEAKER_CAP = 50


def normalize_speaker_cap(value: object) -> int | None:
    """Coerce a submitted speaker cap to a usable int, or ``None``.

    ``None`` means auto-detect. Anything unparseable or out of range also
    returns ``None`` so a bad value degrades to auto-detect rather than
    failing a recording that has already been captured.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        cap = int(value)
    except (TypeError, ValueError):
        return None
    if cap < MIN_SPEAKER_CAP or cap > MAX_SPEAKER_CAP:
        return None
    return cap
