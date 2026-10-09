"""Validation and resolution of the user-overridable processing values."""

from __future__ import annotations

import logging
import math

import pytest

from backend.processing import embedding, phantom_filter
from backend.processing.processing_tuning import (
    normalise_tuning_value,
    resolve_tuning,
    validate_tuning_candidate,
)
from backend.utils import transcript_utils

# The values every reader used before any of them could be overridden. A change
# here is a change to processing defaults, which this feature promised not to make.
TODAYS_LITERALS = {
    "vad_threshold": 0.5,
    "asr_word_end_padding_s": 0.2,
    "phantom_max_duration_s": 3.0,
    "phantom_max_segments": 3,
    "phantom_embedding_floor": 0.35,
    "phantom_merge_threshold": 0.60,
    "speaker_merge_threshold": 0.70,
    "word_flip_max_duration_s": 0.45,
    "word_flip_max_gap_s": 0.25,
}


@pytest.mark.parametrize(("key", "literal"), sorted(TODAYS_LITERALS.items()))
@pytest.mark.parametrize("config", [None, {}, {"unrelated": 1}])
def test_unset_values_resolve_to_todays_literals(config, key, literal) -> None:
    resolved = resolve_tuning(config, key)

    assert resolved == literal
    assert type(resolved) is type(literal)


def test_module_constants_keep_todays_literals() -> None:
    assert phantom_filter.PHANTOM_MAX_DURATION_S == 3.0
    assert phantom_filter.PHANTOM_MAX_SEGMENTS == 3
    assert phantom_filter.PHANTOM_EMBEDDING_FLOOR == 0.35
    assert phantom_filter.PHANTOM_MERGE_THRESHOLD == 0.60
    assert embedding.DUPLICATE_SPEAKER_MERGE_THRESHOLD == 0.70
    assert transcript_utils.ISOLATED_WORD_FLIP_MAX_DURATION_S == 0.45
    assert transcript_utils.ISOLATED_WORD_FLIP_MAX_GAP_S == 0.25


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("vad_threshold", 0.15),
        ("vad_threshold", 0.90),
        ("asr_word_end_padding_s", 0.05),
        ("asr_word_end_padding_s", 0.80),
        ("phantom_max_duration_s", 0),
        ("phantom_max_segments", 20),
        ("speaker_merge_threshold", 0.30),
        ("speaker_merge_threshold", 1),
        ("word_flip_max_gap_s", 0.0),
    ],
)
def test_boundary_values_are_accepted(key, value) -> None:
    assert normalise_tuning_value(key, value) == value


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("vad_threshold", 0.1499),
        ("vad_threshold", 0.95),
        ("speaker_merge_threshold", 0.29),
        ("phantom_max_segments", 21),
        ("phantom_max_segments", -1),
        ("phantom_max_segments", 2.5),
        ("phantom_max_segments", True),
        ("vad_threshold", True),
        ("vad_threshold", math.nan),
        ("vad_threshold", math.inf),
        ("vad_threshold", "0.3"),
        ("vad_threshold", [0.3]),
    ],
)
def test_unusable_values_are_rejected(key, value) -> None:
    assert normalise_tuning_value(key, value) is None


def test_integer_key_accepts_an_integral_float_as_int() -> None:
    value = normalise_tuning_value("phantom_max_segments", 5.0)

    assert value == 5
    assert type(value) is int


def test_resolve_uses_a_valid_value() -> None:
    assert resolve_tuning({"vad_threshold": 0.3}, "vad_threshold") == 0.3


def test_resolve_ignores_an_invalid_value_with_a_warning(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        resolved = resolve_tuning({"vad_threshold": 1.7}, "vad_threshold")

    assert resolved == 0.5
    assert "vad_threshold" in caplog.text


def test_resolve_treats_an_explicit_none_as_unset_without_warning(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        resolved = resolve_tuning({"vad_threshold": None}, "vad_threshold")

    assert resolved == 0.5
    assert caplog.text == ""


def test_candidate_rejects_a_floor_at_or_above_the_default_merge() -> None:
    # Merge unset: its effective value is the 0.60 default.
    with pytest.raises(ValueError):
        validate_tuning_candidate({"phantom_embedding_floor": 0.65})
    with pytest.raises(ValueError):
        validate_tuning_candidate({"phantom_embedding_floor": 0.60})


def test_candidate_accepts_a_floor_below_a_raised_merge() -> None:
    validate_tuning_candidate(
        {"phantom_embedding_floor": 0.65, "phantom_merge_threshold": 0.8}
    )


def test_candidate_counts_an_unusable_value_as_its_default() -> None:
    # A stored out-of-range merge threshold is ignored when processing, so the
    # check must compare against the default it falls back to.
    with pytest.raises(ValueError):
        validate_tuning_candidate(
            {"phantom_embedding_floor": 0.7, "phantom_merge_threshold": 4.0}
        )
