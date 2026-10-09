"""Shipped defaults and bounds for the processing values a user may override.

Deliberately free of heavy imports, like ``speaker_cap``, so the API can
validate a submitted value without pulling torch or pyannote into the request
process. The processing modules take their default constants from this table,
so each default has one source.

Every key is optional on a user's settings. Unset (``None``) means inherit: the
install's ``config.json`` value when one is set, else the default below. The
worker never fails a recording over a value it cannot use: an invalid value is
logged and the default is used instead.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TuningSpec:
    """One overridable value: its shipped default and inclusive bounds."""

    key: str
    default: float
    minimum: float
    maximum: float
    integer: bool = False


VAD_THRESHOLD_KEY = "vad_threshold"
ASR_WORD_END_PADDING_KEY = "asr_word_end_padding_s"
PHANTOM_MAX_DURATION_KEY = "phantom_max_duration_s"
PHANTOM_MAX_SEGMENTS_KEY = "phantom_max_segments"
PHANTOM_EMBEDDING_FLOOR_KEY = "phantom_embedding_floor"
PHANTOM_MERGE_THRESHOLD_KEY = "phantom_merge_threshold"
SPEAKER_MERGE_THRESHOLD_KEY = "speaker_merge_threshold"
WORD_FLIP_MAX_DURATION_KEY = "word_flip_max_duration_s"
WORD_FLIP_MAX_GAP_KEY = "word_flip_max_gap_s"

TUNING_SPECS: dict[str, TuningSpec] = {
    spec.key: spec
    for spec in (
        # Silero exits speech below max(threshold - 0.15, 0.01): under 0.15 the
        # hysteresis collapses and nearly everything counts as speech.
        TuningSpec(VAD_THRESHOLD_KEY, 0.5, 0.15, 0.90),
        # A pad up to the 0.8 s segment pause can never reach the next word;
        # a pad of 0 makes zero-length words no speaker turn can overlap.
        TuningSpec(ASR_WORD_END_PADDING_KEY, 0.2, 0.05, 0.80),
        # The two candidate ceilings are ANDed, so either at 0 turns the
        # phantom filter off.
        TuningSpec(PHANTOM_MAX_DURATION_KEY, 3.0, 0.0, 10.0),
        TuningSpec(PHANTOM_MAX_SEGMENTS_KEY, 3, 0, 20, integer=True),
        # The floor must stay below the merge threshold (see
        # validate_tuning_candidate), or no brief speaker is ever retained.
        TuningSpec(PHANTOM_EMBEDDING_FLOOR_KEY, 0.35, 0.0, 0.95),
        TuningSpec(PHANTOM_MERGE_THRESHOLD_KEY, 0.60, 0.05, 1.0),
        # 0.30 sits under the measured same-person 10th percentile (0.363) and
        # far above the different-person median (0.073); see ARCHITECTURE.md,
        # "Speaker Cap And Voiceprint Versioning".
        TuningSpec(SPEAKER_MERGE_THRESHOLD_KEY, 0.70, 0.30, 1.00),
        # Past about 2 s a single-word "flip" is a genuine interjection.
        TuningSpec(WORD_FLIP_MAX_DURATION_KEY, 0.45, 0.0, 2.0),
        TuningSpec(WORD_FLIP_MAX_GAP_KEY, 0.25, 0.0, 1.0),
    )
}

TUNING_KEYS: tuple[str, ...] = tuple(TUNING_SPECS)


def normalise_tuning_value(key: str, value: object) -> float | None:
    """Return ``value`` as a usable number for ``key``, or ``None``.

    ``None`` comes back both for an unset value and for one that cannot be
    used: a non-number (``bool`` included), NaN or infinity, an integer too
    large to convert, a fraction for an integer key, or anything outside the
    key's bounds. Never raises.
    """
    spec = TUNING_SPECS[key]
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        # An int too large for a float (JSON allows any number of digits)
        # raises rather than becoming infinity.
        return None
    if not math.isfinite(number):
        # A float NaN or infinity; every bound below would reject it too.
        return None
    if not spec.minimum <= number <= spec.maximum:
        return None
    if spec.integer:
        if not number.is_integer():
            return None
        return int(number)
    return number


def _effective(config: Mapping[str, object] | None, key: str) -> float:
    value = normalise_tuning_value(key, config.get(key)) if config else None
    return TUNING_SPECS[key].default if value is None else value


def resolve_tuning(config: Mapping[str, object] | None, key: str) -> float:
    """Return ``config[key]`` when it is usable, else the shipped default.

    A set value that cannot be used is logged and ignored, never raised: a
    processing run must not fail over a tuning value.
    """
    raw = config.get(key) if config else None
    if raw is not None and normalise_tuning_value(key, raw) is None:
        logger.warning(
            "Ignoring invalid %s=%r; using the default %s.",
            key,
            raw,
            TUNING_SPECS[key].default,
        )
    return _effective(config, key)


def phantom_thresholds_conflict(config: Mapping[str, object] | None) -> bool:
    """Whether the effective phantom floor is not below the merge threshold.

    The filter retains a brief speaker only when its best similarity falls in
    ``[floor, merge)``. With the floor at or above the merge threshold that band
    is empty and every candidate is reassigned or merged.
    """
    floor = _effective(config, PHANTOM_EMBEDDING_FLOOR_KEY)
    merge = _effective(config, PHANTOM_MERGE_THRESHOLD_KEY)
    return floor >= merge


def validate_tuning_candidate(candidate: Mapping[str, object]) -> None:
    """Reject a settings candidate whose values cannot work together.

    ``candidate`` is the settings a save would leave in effect. Unset or
    unusable values count as their defaults, as they would when processing.

    Raises:
        ValueError: The phantom floor is not below the phantom merge threshold.
    """
    if phantom_thresholds_conflict(candidate):
        raise ValueError(
            f"{PHANTOM_EMBEDDING_FLOOR_KEY} must be lower than {PHANTOM_MERGE_THRESHOLD_KEY}."
        )
