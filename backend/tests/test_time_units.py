"""One rule per direction when a time changes unit."""

from __future__ import annotations

import pytest

from backend.utils.time_units import (
    NATIVE_FRAME_MS,
    frame_range,
    samples_to_ms,
)


@pytest.mark.parametrize("sample_rate_hz", [0, -16000])
def test_a_sample_rate_must_be_positive(sample_rate_hz):
    with pytest.raises(ValueError, match="sample rate"):
        samples_to_ms(100, sample_rate_hz)


def test_a_word_covers_every_frame_it_touches():
    # 1040.5 s to 1041.16 s: the end sits on a frame edge, so it reaches no
    # frame further (ceil of 1041.16 * 100 in floats is 104117).
    assert frame_range(1_040_500, 1_041_160, NATIVE_FRAME_MS) == (104_050, 104_116)
    # Part of a frame at either end still counts.
    assert frame_range(1_001, 1_019, NATIVE_FRAME_MS) == (100, 102)


def test_a_frame_range_indexes_the_retained_frames():
    assert frame_range(1_000, 1_050, NATIVE_FRAME_MS, first_frame=98) == (2, 7)
    # Before the first retained frame there is nothing to read.
    assert frame_range(0, 500, NATIVE_FRAME_MS, first_frame=100) == (0, 0)
