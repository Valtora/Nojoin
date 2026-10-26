"""Explicit local CUDA golden; ordinary upstream CPU CI reports a precise skip."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from backend.tests.fixtures.nemotron.fixture_tools import (
    DIRECTORY,
    cache_arrays,
    cache_metadata,
    local_runtime,
    manifest,
    restore_cache,
    waveform,
)


@pytest.fixture(scope="module")
def verified_runtime() -> Any:
    if os.getenv("NOJOIN_RUN_NEMOTRON_INFERENCE") != "1":
        pytest.skip("set NOJOIN_RUN_NEMOTRON_INFERENCE=1 for the local CUDA golden")
    selected = os.getenv("NOJOIN_NEMOTRON_MODEL_DIR")
    if not selected:
        pytest.skip(
            "NOJOIN_NEMOTRON_MODEL_DIR must select the exact verified local snapshot"
        )
    import torch

    from backend.processing.nemotron_model import release_nemotron_model_cache
    from backend.utils.nemotron_model_utils import verify_nemotron_model

    if not torch.cuda.is_available():
        pytest.skip("the native inference golden requires available CUDA")
    verify_nemotron_model(Path(selected))
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    runtime = local_runtime(Path(selected))
    try:
        yield runtime
    finally:
        release_nemotron_model_cache()


def assert_checkpoint(
    cache: Any, label: str, schedule: dict[str, Any], tolerance: dict[str, float]
) -> Any:
    metadata = {
        key: value
        for key, value in schedule["checkpoints"][label].items()
        if key != "call_index"
    }
    assert cache_metadata(cache) == metadata
    with np.load(DIRECTORY / "state_checkpoints.npz", allow_pickle=False) as stored:
        arrays = {
            name: stored[f"{label}_{name}"] for name in ("embeds", "probs", "fifo")
        }
        for name, actual in cache_arrays(cache).items():
            np.testing.assert_allclose(actual, arrays[name], **tolerance)
    return restore_cache(arrays, metadata)


def test_production_loader_golden_and_resumed_native_checkpoints(
    verified_runtime: Any,
) -> None:
    from backend.processing.live_diarization.nemotron import NemotronStream
    from backend.processing.nemotron_types import NativeWindowResult

    info = manifest()
    assert verified_runtime.asset_fingerprint == info["asset_manifest_sha256"]
    assert verified_runtime.runtime_fingerprint == info["runtime"]["fingerprint"]
    schedule = json.loads((DIRECTORY / "window_schedule.json").read_text())
    tolerance = info["repeat_gate"]["tolerances"]
    with np.load(DIRECTORY / "native_packets.npz", allow_pickle=False) as stored:
        expected = stored["probabilities"].copy()
    index = 0

    def compare(
        window: np.ndarray, *, is_first: bool, is_last: bool, speaker_cache: Any
    ) -> Any:
        nonlocal index
        call = schedule["calls"][index]
        assert (len(window), is_first, is_last) == (
            call["samples"],
            call["is_first"],
            call["is_last"],
        )
        assert (
            hashlib.sha256(window.astype("<f4").tobytes()).hexdigest() == call["sha256"]
        )
        result = verified_runtime.infer_window(
            window, is_first=is_first, is_last=is_last, speaker_cache=speaker_cache
        )
        first, count = call["first_frame"], call["frames"]
        reference = expected[first : first + count]
        np.testing.assert_allclose(
            result.probabilities, reference, **tolerance["probabilities"]
        )
        np.testing.assert_array_equal(
            result.probabilities.astype(np.float16) > 0.5,
            reference.astype(np.float16) > 0.5,
        )
        assert int(result.probabilities.sum(axis=0).argmax()) == int(
            reference.sum(axis=0).argmax()
        )
        index += 1
        label = "eof" if is_last else str(index)
        if label in schedule["checkpoints"]:
            restored = assert_checkpoint(
                result.speaker_cache, label, schedule, tolerance["cache"]
            )
            return NativeWindowResult(result.probabilities, restored)
        return result

    stream = NemotronStream(verified_runtime, infer_window=compare)
    audio = waveform(DIRECTORY / "es2004d-first-21_13.wav")
    blocks = []
    for position in range(0, len(audio), 32000):
        blocks.extend(stream.feed(audio[position : position + 32000], position))
    blocks.extend(stream.flush(audio_end_n=len(audio)))
    assert index == len(schedule["calls"])
    assert cache_metadata(stream.speaker_cache) == {
        key: value
        for key, value in schedule["final_cache"].items()
        if key != "call_index"
    }
    assert (
        stream.speaker_cache.num_cache_frames,
        stream.speaker_cache.num_fifo_frames,
    ) == (222, 43)
    assert np.count_nonzero((expected > 0.5).any(axis=0)) >= 2
    assert [(b.first_frame, len(b.probabilities), b.emitted_at_n) for b in blocks] == [
        (c["first_frame"], c["frames"], c["emitted_at_n"]) for c in schedule["calls"]
    ]
    assert not any(module.startswith("nemo.") for module in __import__("sys").modules)


def test_first_and_last_short_window_uses_streaming_cache(
    verified_runtime: Any,
) -> None:
    from backend.processing.nemotron_frontend import check_feature_equivalence

    audio = waveform(DIRECTORY / "es2004d-first-21_13.wav")[:800]
    options = dict(
        sampling_rate=16000,
        is_streaming=True,
        is_first_audio_chunk=True,
        is_last_audio_chunk=True,
        return_tensors="pt",
    )
    check_feature_equivalence(
        verified_runtime.cpu_streaming_processor(audio, **options),
        verified_runtime.streaming_processor(audio, **options),
    )
    result = verified_runtime.infer_window(
        audio, is_first=True, is_last=True, speaker_cache=None
    )
    assert result.probabilities.shape == (5, 8)
    assert result.speaker_cache.fifo_length == 264
    assert result.speaker_cache.speaker_cache_update_period == 222


def test_cpu_cuda_feature_equivalence_for_every_fixture_window(
    verified_runtime: Any,
) -> None:
    from backend.processing.nemotron_frontend import check_feature_equivalence

    manifest()
    audio = waveform(DIRECTORY / "es2004d-first-21_13.wav")
    schedule = json.loads((DIRECTORY / "window_schedule.json").read_text())
    position = 0
    checked = []
    for call in schedule["calls"]:
        window = audio[position : position + call["samples"]]
        assert (
            hashlib.sha256(window.astype("<f4").tobytes()).hexdigest() == call["sha256"]
        )
        options = dict(
            sampling_rate=16000,
            is_streaming=True,
            is_first_audio_chunk=call["is_first"],
            is_last_audio_chunk=call["is_last"],
            return_tensors="pt",
        )
        reference = verified_runtime.cpu_streaming_processor(window, **options)
        actual = verified_runtime.streaming_processor(window, **options)
        assert actual["input_features"].device.type == "cuda"
        assert actual["attention_mask"].device.type == "cuda"
        check_feature_equivalence(reference, actual)
        checked.append((call["is_first"], call["is_last"]))
        position += verified_runtime.geometry.advance_n(first=call["is_first"])
    assert len(checked) == len(schedule["calls"]) == 29
    assert {(True, False), (False, False), (False, True)} == set(checked)
