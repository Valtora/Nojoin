"""Native checkpoint continuation, malformed state and real CPU cache evidence."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from backend.processing.live_diarization.native_state import (
    encode_stream,
    restore_stream,
    validate_stream,
)
from backend.processing.live_diarization.nemotron import NemotronStream
from backend.processing.nemotron_types import NemotronGeometry, NemotronStateError
from backend.tests.fixtures.nemotron.fixture_tools import DIRECTORY, restore_cache
from backend.tests.nemotron_fakes import ObservedRuntime, cache_with_token


def numpy_cache(geometry: NemotronGeometry) -> Any:
    return cache_with_token(0)


def test_partial_packet_resume_preserves_next_raw_window_and_cache_token() -> None:
    audio = np.sin(np.arange(110013, dtype=np.float32) / 41)
    whole_runtime = ObservedRuntime()
    whole = NemotronStream(whole_runtime)
    expected = whole.feed(audio, 0)
    runtime = ObservedRuntime()
    interrupted = NemotronStream(runtime)
    before = interrupted.feed(audio[:45017], 0)
    meta, arrays = encode_stream(interrupted)
    resumed = restore_stream(meta, arrays, cache_factory=numpy_cache)
    continuation = ObservedRuntime()
    continuation.calls = runtime.calls.copy()
    resumed.bind_runtime(continuation)
    after = resumed.feed(audio[40000:], 40000)
    assert continuation.calls == whole_runtime.calls
    assert [(b.first_frame, b.emitted_at_n) for b in before + after] == [
        (b.first_frame, b.emitted_at_n) for b in expected
    ]
    np.testing.assert_array_equal(resumed.buffer, whole.buffer)
    assert resumed.speaker_cache.embeds[0, 0, 0] == len(whole_runtime.calls)


@pytest.mark.parametrize("damage", ["packet", "frame", "audio", "cache", "first"])
def test_incoherent_scheduler_is_rejected_before_cache_construction(
    damage: str,
) -> None:
    stream = NemotronStream(ObservedRuntime())
    stream.feed(np.zeros(45017, np.float32), 0)
    meta, arrays = encode_stream(stream)
    if damage == "packet":
        meta["packet_cursor_n"] += 1
    elif damage == "frame":
        meta["next_frame"] += 1
    elif damage == "audio":
        arrays["native_audio"][0] = np.nan
    elif damage == "cache":
        arrays["native_fifo"] = arrays["native_fifo"][:, :-1]
    else:
        meta["first"] = True

    def forbidden_factory(geometry: NemotronGeometry) -> Any:
        pytest.fail("cache reconstructed before numeric validation")

    with pytest.raises(NemotronStateError):
        restore_stream(meta, arrays, cache_factory=forbidden_factory)


def test_poisoned_cache_cannot_be_saved_or_rebound() -> None:
    stream = NemotronStream(ObservedRuntime())
    with pytest.raises(NemotronStateError, match="gap"):
        stream.feed(np.zeros(10, np.float32), 1)
    with pytest.raises(NemotronStateError, match="failed"):
        encode_stream(stream)
    with pytest.raises(NemotronStateError, match="failed"):
        stream.bind_runtime(ObservedRuntime())


def test_numeric_snapshot_does_not_alias_live_stream() -> None:
    stream = NemotronStream(ObservedRuntime())
    stream.feed(np.zeros(45017, np.float32), 0)
    meta, arrays = encode_stream(stream)
    resumed = restore_stream(meta, arrays, cache_factory=numpy_cache)
    stream.speaker_cache.embeds[:] = 99
    arrays["native_pending"][:] = 17
    assert resumed.speaker_cache.embeds[0, 0, 0] != 99
    assert resumed._pending[0] == 0


def test_real_recorded_cache_checkpoints_use_production_numeric_codec() -> None:
    schedule = json.loads((DIRECTORY / "window_schedule.json").read_text())
    with np.load(DIRECTORY / "state_checkpoints.npz", allow_pickle=False) as stored:
        for label, entry in schedule["checkpoints"].items():
            original = {
                name: stored[f"{label}_{name}"] for name in ("embeds", "probs", "fifo")
            }
            cache_meta = {
                key: value for key, value in entry.items() if key != "call_index"
            }
            cache = restore_cache(original, cache_meta)
            stream = NemotronStream(ObservedRuntime())
            stream.speaker_cache = cache
            stream.first = False
            stream.finished = True
            call = schedule["calls"][entry["call_index"]]
            stream.consumed_n = call["emitted_at_n"]
            stream.packet_cursor_n = stream.consumed_n // 32000 * 32000
            stream.next_frame = call["first_frame"] + call["frames"]
            meta, arrays = encode_stream(stream)
            restored = restore_stream(meta, arrays)
            for name in ("embeds", "probs", "fifo"):
                np.testing.assert_array_equal(
                    getattr(restored.speaker_cache, name), getattr(cache, name)
                )
            assert restored.speaker_cache.is_compressed == cache.is_compressed
            assert restored.speaker_cache.num_cache_frames == cache.num_cache_frames
            assert restored.speaker_cache.num_fifo_frames == cache.num_fifo_frames
            damaged = copy.deepcopy(meta)
            damaged["cache"]["num_fifo_frames"] = 265
            with pytest.raises(NemotronStateError):
                validate_stream(damaged, arrays)


def test_eof_round_trip_stays_idempotent(tmp_path: Path) -> None:
    stream = NemotronStream(ObservedRuntime())
    stream.feed(np.zeros(45017, np.float32), 0)
    stream.flush(audio_end_n=45017)
    meta, arrays = encode_stream(stream)
    restored = restore_stream(meta, arrays, cache_factory=numpy_cache)
    assert restored.flush(audio_end_n=45017) == []
    with pytest.raises(NemotronStateError, match="EOF"):
        restored.feed(np.zeros(1, np.float32), 45017)
