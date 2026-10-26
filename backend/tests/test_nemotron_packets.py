"""Exercise packet receipts, native overlaps, EOF and invalid callback state."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from backend.processing.live_diarization.nemotron import NemotronStream
from backend.processing.nemotron_types import (
    NativeWindowResult,
    NemotronGeometry,
    NemotronStateError,
)
from backend.tests.nemotron_fakes import ObservedRuntime, cache_with_token


def _audio(samples: int) -> np.ndarray:
    return np.random.default_rng(710).uniform(-0.5, 0.5, samples).astype(np.float32)


def test_first_packet_emits_two_windows_at_receipt_not_native_window_end() -> None:
    runtime = ObservedRuntime()
    stream = NemotronStream(runtime)
    audio = _audio(32000)
    blocks = stream.feed(audio, 0)
    expected_windows = (audio[:16680], audio[11264:28304])
    assert runtime.calls == [
        (hashlib.sha256(window.tobytes()).hexdigest(), len(window), index == 0, False)
        for index, window in enumerate(expected_windows)
    ]
    assert [(b.first_frame, len(b.probabilities), b.emitted_at_n) for b in blocks] == [
        (0, 72, 32000),
        (72, 72, 32000),
    ]
    np.testing.assert_array_equal(stream.buffer, audio[22784:])


def test_long_backlog_matches_paced_overlapping_and_partial_clips() -> None:
    audio = _audio(100213)
    whole_runtime, paced_runtime = ObservedRuntime(), ObservedRuntime()
    whole, paced = NemotronStream(whole_runtime), NemotronStream(paced_runtime)
    expected = whole.feed(audio, 0) + whole.flush(audio_end_n=len(audio))
    actual = []
    for start, end in [
        (0, 10000),
        (0, 32000),
        (30000, 64500),
        (60000, 96000),
        (96000, len(audio)),
    ]:
        actual.extend(paced.feed(audio[start:end], start))
    actual.extend(paced.flush(audio_end_n=len(audio)))
    assert whole_runtime.calls == paced_runtime.calls
    assert [(b.first_frame, b.emitted_at_n) for b in actual] == [
        (b.first_frame, b.emitted_at_n) for b in expected
    ]
    np.testing.assert_array_equal(
        np.concatenate([b.probabilities for b in actual]),
        np.concatenate([b.probabilities for b in expected]),
    )
    assert paced.next_frame == len(audio) // 160
    assert actual[-1].emitted_at_n == len(audio)


@pytest.mark.parametrize("samples", [0, 1, 159, 160, 16679])
def test_short_capture_has_one_eof_and_no_forward_for_zero_valid_frames(
    samples: int,
) -> None:
    runtime = ObservedRuntime()
    stream = NemotronStream(runtime)
    assert stream.feed(_audio(samples), 0) == []
    blocks = stream.flush(audio_end_n=samples)
    assert stream.finished
    assert stream.consumed_n == samples
    assert stream.next_frame == samples // 160
    assert len(runtime.calls) == int(samples >= 160)
    if runtime.calls:
        assert runtime.calls[0][1:] == (samples, True, True)
        assert blocks[0].emitted_at_n == samples
    assert stream.flush(audio_end_n=samples) == []
    with pytest.raises(NemotronStateError, match="already received EOF"):
        stream.feed(np.empty(0, np.float32), samples)


def test_partial_packet_waits_until_eof_even_when_a_native_window_fits() -> None:
    runtime = ObservedRuntime()
    stream = NemotronStream(runtime)
    assert stream.feed(_audio(20000), 0) == []
    blocks = stream.flush(audio_end_n=20000)
    assert [b.emitted_at_n for b in blocks] == [20000, 20000]
    assert [c[3] for c in runtime.calls] == [False, True]
    assert sum(len(b.probabilities) for b in blocks) == 125


def test_gap_permanently_rejects_inference_instead_of_inserting_silence() -> None:
    runtime = ObservedRuntime()
    stream = NemotronStream(runtime)
    stream.feed(_audio(32000), 0)
    with pytest.raises(NemotronStateError, match="audio gap"):
        stream.feed(_audio(100), 32001)
    with pytest.raises(NemotronStateError, match="failed"):
        stream.feed(_audio(100), 32000)
    assert len(runtime.calls) == 2


@pytest.mark.parametrize(
    "audio",
    [
        np.zeros((2, 3), np.float32),
        np.zeros(4, np.float64),
        np.array([np.nan], np.float32),
    ],
)
def test_bad_audio_is_rejected_before_callback(audio: np.ndarray) -> None:
    runtime = ObservedRuntime()
    with pytest.raises(NemotronStateError):
        NemotronStream(runtime).feed(audio, 0)
    assert runtime.calls == []


@pytest.mark.parametrize("position", [-1, 0.5, True])
def test_sample_positions_are_non_negative_integers(position: Any) -> None:
    with pytest.raises(NemotronStateError, match="integer"):
        NemotronStream(ObservedRuntime()).feed(_audio(10), position)


def test_eof_must_match_real_audio_end() -> None:
    stream = NemotronStream(ObservedRuntime())
    stream.feed(_audio(100), 0)
    with pytest.raises(NemotronStateError, match="does not match"):
        stream.flush(audio_end_n=99)
    assert not stream.finished
    assert stream.flush(audio_end_n=100) == []


@pytest.mark.parametrize(
    "bad", ["shape", "dtype", "nonfinite", "range", "none", "cache_shape", "policy"]
)
def test_invalid_callback_result_poisoning_prevents_reusing_mutated_cache(
    bad: str,
) -> None:
    def infer(audio: np.ndarray, **kwargs: Any) -> NativeWindowResult:
        probabilities = np.full((72, 8), 0.5, np.float32)
        cache = cache_with_token(1)
        if bad == "shape":
            probabilities = probabilities[:-1]
        if bad == "dtype":
            probabilities = probabilities.astype(np.float64)
        if bad == "nonfinite":
            probabilities[0, 0] = np.nan
        if bad == "range":
            probabilities[0, 0] = 1.1
        if bad == "none":
            cache = None
        if bad == "cache_shape":
            cache.fifo = cache.fifo[:, :-1]
        if bad == "policy":
            cache.fifo_length = 40
        return NativeWindowResult(probabilities, cache)

    stream = NemotronStream(ObservedRuntime(), infer_window=infer)
    with pytest.raises(NemotronStateError):
        stream.feed(_audio(32000), 0)
    with pytest.raises(NemotronStateError, match="failed"):
        stream.flush(audio_end_n=32000)
    assert stream.next_frame == 0


def test_callback_exception_after_native_mutation_cannot_be_retried() -> None:
    runtime = ObservedRuntime()
    stream = NemotronStream(runtime)
    stream.feed(_audio(32000), 0)

    def fail(audio: np.ndarray, **kwargs: Any) -> NativeWindowResult:
        kwargs["speaker_cache"].embeds[0, 0, 0] = 99
        raise RuntimeError("failed after cache mutation")

    stream._infer = fail
    with pytest.raises(NemotronStateError, match="cannot resume"):
        stream.feed(_audio(32000), 32000)
    assert stream.speaker_cache.embeds[0, 0, 0] == 99
    with pytest.raises(NemotronStateError, match="failed"):
        stream.feed(_audio(32000), 32000)
    assert stream.next_frame == 144


def test_meetings_do_not_share_cache_and_buffers_are_owned() -> None:
    runtime = ObservedRuntime()
    stream = NemotronStream(runtime)
    other = NemotronStream(ObservedRuntime())
    source = _audio(32000)
    stream.feed(source, 0)
    other.feed(source, 0)
    assert stream.speaker_cache is not other.speaker_cache
    copy = stream.buffer
    copy[:] = 0
    source[:] = 0
    assert np.any(stream.buffer != 0)
    assert np.any(other.buffer != 0)


def test_non_evaluated_geometry_is_rejected() -> None:
    runtime = SimpleNamespace(geometry=NemotronGeometry(chunk_length=6))
    with pytest.raises(NemotronStateError, match="geometry"):
        NemotronStream(runtime)
