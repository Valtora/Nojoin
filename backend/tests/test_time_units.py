"""One rule per direction when a time changes unit."""

from __future__ import annotations

import pytest

from backend.utils.time_units import (
    NATIVE_FRAME_MS,
    frame_range,
    samples_to_ms,
    samples_to_seconds,
    seconds_to_samples,
)


@pytest.mark.parametrize("sample_rate_hz", [8000, 16000, 22050, 44100, 48000])
def test_a_sample_count_survives_a_trip_through_seconds(sample_rate_hz):
    # Sizes a cut can take, among them the 16,208 and 32,001 that truncating
    # the seconds product left one sample short.
    for samples in (1, 16208, 32001, 88641, 3 * 3600 * sample_rate_hz + 7):
        seconds = samples_to_seconds(samples, sample_rate_hz)

        assert seconds_to_samples(seconds, sample_rate_hz) == samples


@pytest.mark.parametrize(
    ("samples", "sample_rate_hz", "expected_ms"),
    [
        # Exactly half a millisecond goes to the even neighbour.
        (8, 16000, 0),
        (24, 16000, 2),
        (40, 16000, 2),
        # 0.4375 ms and 0.5625 ms.
        (7, 16000, 0),
        (9, 16000, 1),
    ],
)
def test_samples_round_to_the_nearest_millisecond_and_halves_to_even(
    samples, sample_rate_hz, expected_ms
):
    assert samples_to_ms(samples, sample_rate_hz) == expected_ms


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
