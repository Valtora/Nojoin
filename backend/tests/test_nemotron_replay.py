"""CPU scheduling replay; the recorded posterior oracle does not validate inference."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from backend.processing.live_diarization.nemotron import NemotronStream
from backend.processing.nemotron_types import (
    NativeWindowResult,
    NemotronGeometry,
)
from backend.tests.fixtures.nemotron.fixture_tools import (
    DIRECTORY,
    cache_arrays,
    manifest,
    restore_cache,
    waveform,
)
from scripts.evaluate_nemotron import (
    DEFAULT_RECORD,
    pool,
    read_record,
    render,
    verify_supplied_sources,
)


@pytest.fixture(scope="module")
def golden() -> dict[str, Any]:
    info = manifest()
    policy = DIRECTORY / "tolerance-policy.json"
    assert (
        hashlib.sha256(policy.read_bytes()).hexdigest()
        == info["tolerance_policy_sha256"]
    )
    assert json.loads(policy.read_text()) == info["repeat_gate"]["tolerances"]
    assert (
        sum(path.stat().st_size for path in DIRECTORY.iterdir() if path.is_file())
        < 1500000
    )
    schedule = json.loads((DIRECTORY / "window_schedule.json").read_text())
    with np.load(DIRECTORY / "native_packets.npz", allow_pickle=False) as stored:
        probabilities = stored["probabilities"].copy()
    return {
        "manifest": info,
        "schedule": schedule,
        "probabilities": probabilities,
        "audio": waveform(DIRECTORY / "es2004d-first-21_13.wav"),
    }


class RecordedWindowInfer:
    """Check actual source windows and prior native-cache token before answering."""

    def __init__(self, golden: dict[str, Any]) -> None:
        self.golden = golden
        self.index = 0

    def __call__(
        self, audio: np.ndarray, *, is_first: bool, is_last: bool, speaker_cache: Any
    ) -> NativeWindowResult:
        calls = self.golden["schedule"]["calls"]
        expected = calls[self.index]
        assert (len(audio), is_first, is_last) == (
            expected["samples"],
            expected["is_first"],
            expected["is_last"],
        )
        assert (
            hashlib.sha256(audio.astype("<f4").tobytes()).hexdigest()
            == expected["sha256"]
        )
        if self.index == 0:
            assert speaker_cache is None
            speaker_cache = restore_cache(
                {
                    name: np.zeros(shape, np.float32)
                    for name, shape in {
                        "embeds": (1, 0, 512),
                        "probs": (1, 0, 8),
                        "fifo": (1, 0, 512),
                    }.items()
                },
                {
                    "num_cache_frames": 0,
                    "num_fifo_frames": 0,
                    "is_initialized": True,
                    "is_compressed": False,
                },
            )
        else:
            assert float(speaker_cache.embeds[0, 0, 0]) == self.index
        speaker_cache.embeds[0, 0, 0] = self.index + 1
        self.index += 1
        first, frames = expected["first_frame"], expected["frames"]
        return NativeWindowResult(
            self.golden["probabilities"][first : first + frames].copy(), speaker_cache
        )


def replay(golden: dict[str, Any], mode: str) -> tuple[NemotronStream, list[Any]]:
    oracle = RecordedWindowInfer(golden)
    stream = NemotronStream(
        SimpleNamespace(geometry=NemotronGeometry()), infer_window=oracle
    )
    audio = golden["audio"]
    blocks = []
    step = {"packets": 32000, "backlog": len(audio), "overlap": 21017}[mode]
    for start in range(0, len(audio), step):
        prefix = min(start, 791) if mode == "overlap" else 0
        blocks.extend(stream.feed(audio[start - prefix : start + step], start - prefix))
    blocks.extend(stream.flush(audio_end_n=len(audio)))
    assert oracle.index == len(golden["schedule"]["calls"])
    return stream, blocks


@pytest.mark.parametrize("mode", ["packets", "backlog", "overlap"])
def test_real_packet_scheduler_replays_recorded_windows(
    golden: dict[str, Any], mode: str
) -> None:
    stream, blocks = replay(golden, mode)
    calls = golden["schedule"]["calls"]
    assert [(b.first_frame, len(b.probabilities), b.emitted_at_n) for b in blocks] == [
        (c["first_frame"], c["frames"], c["emitted_at_n"]) for c in calls
    ]
    np.testing.assert_array_equal(
        np.concatenate([block.probabilities for block in blocks]),
        golden["probabilities"],
    )
    assert stream.consumed_n == 338080
    assert stream.next_frame == len(golden["probabilities"])
    assert stream.finished and len(stream.buffer) == 0
    assert stream.flush(audio_end_n=338080) == []


def test_schedule_matches_independent_sample_arithmetic(golden: dict[str, Any]) -> None:
    audio = golden["audio"]
    start, first_frame = 0, 0
    calls = golden["schedule"]["calls"]
    for index, call in enumerate(calls):
        last = index == len(calls) - 1
        size = len(audio) - start if last else 16680 if index == 0 else 17040
        emitted = (
            len(audio)
            if last
            else min(len(audio), ((start + size + 31999) // 32000) * 32000)
        )
        frames = (size + 96 - 512) // 160 + 1 if last else 72
        assert (
            call["index"],
            call["start_n"],
            call["samples"],
            call["first_frame"],
            call["frames"],
            call["emitted_at_n"],
        ) == (index, start, size, first_frame, frames, emitted)
        assert (
            call["sha256"]
            == hashlib.sha256(
                audio[start : start + size].astype("<f4").tobytes()
            ).hexdigest()
        )
        first_frame += frames
        start += 11264 if index == 0 else 11520
    assert len(calls) == 29
    assert first_frame == 2113
    assert sum(c["is_last"] for c in calls) == 1
    assert sum(c["is_first"] for c in calls) == 1
    assert calls[-1]["samples"] == 15776
    assert calls[-1]["frames"] == 97
    assert np.count_nonzero((golden["probabilities"] > 0.5).any(axis=0)) >= 2


def test_changed_pcm_fails_the_window_oracle(golden: dict[str, Any]) -> None:
    altered = {**golden, "audio": golden["audio"].copy()}
    altered["audio"][17] += 1 / 32768
    with pytest.raises(AssertionError):
        replay(altered, "packets")


def test_recorded_native_checkpoints_reconstruct_and_reject_bad_shapes(
    golden: dict[str, Any],
) -> None:
    expected_counts = {
        "28": (0, 252, False),
    }
    assert golden["schedule"]["final_cache"] == {
        "call_index": 28,
        "num_cache_frames": 222,
        "num_fifo_frames": 43,
        "is_initialized": True,
        "is_compressed": False,
    }
    with np.load(DIRECTORY / "state_checkpoints.npz", allow_pickle=False) as stored:
        for label, metadata in golden["schedule"]["checkpoints"].items():
            arrays = {
                name: stored[f"{label}_{name}"] for name in ("embeds", "probs", "fifo")
            }
            state = {
                key: value for key, value in metadata.items() if key != "call_index"
            }
            restored = restore_cache(arrays, state)
            for name, value in cache_arrays(restored).items():
                np.testing.assert_array_equal(value, arrays[name])
            if label in expected_counts:
                assert (
                    restored.num_cache_frames,
                    restored.num_fifo_frames,
                    restored.is_compressed,
                ) == expected_counts[label]
            with pytest.raises(ValueError, match="array"):
                restore_cache({**arrays, "fifo": arrays["fifo"][:, :-1]}, state)
            with pytest.raises(ValueError, match="count"):
                restore_cache(arrays, {**state, "num_cache_frames": 265})


def test_evaluation_recomputes_additive_metrics_and_rejects_missing_inputs(
    tmp_path: Path,
) -> None:
    record = read_record(DEFAULT_RECORD)
    headset = [row for row in record["streams"] if row["group"] == "AMI headset"]
    first, der = pool(headset, "hf-argmax")
    assert first == pytest.approx(78.065, abs=0.001)
    assert der == pytest.approx(26.872, abs=0.001)
    assert "Numerical/output parity: FAILS" in render(record)
    with pytest.raises(ValueError, match="Missing retained comparison input"):
        verify_supplied_sources(record, tmp_path)


def test_recorded_only_cli_needs_no_model_or_network() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/evaluate_nemotron.py", "--recorded-only"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "| Mix-Headset_ES2004d | 222229/222230 |" in result.stdout
    assert "| Lords | hf-argmax | 25.015 | 82.921 |" in result.stdout
