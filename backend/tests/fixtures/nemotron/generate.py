#!/usr/bin/env python3
"""Explicit local golden generation; run only in an authorised offline GPU container."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from backend.tests.fixtures.nemotron.fixture_tools import (
    cache_arrays,
    cache_metadata,
    local_runtime,
    sha256,
    waveform,
)

SOURCE_SHA256 = "3cfce3297ebb7faeb53c02727eea5e9f2c76e9ed4b62db3b03d73e66836e91f3"
SAMPLES = 338080
CHECKPOINTS = (28,)


def extract(source: Path, target: Path) -> None:
    if sha256(source) != SOURCE_SHA256:
        raise ValueError("ES2004d source digest differs from the retained study")
    with wave.open(str(source)) as original:
        if (
            original.getframerate(),
            original.getnchannels(),
            original.getsampwidth(),
        ) != (16000, 1, 2):
            raise ValueError("Source must already be mono PCM16 at 16 kHz")
        pcm = original.readframes(SAMPLES)
    if len(pcm) != SAMPLES * 2:
        raise ValueError("Source excerpt is truncated")
    with wave.open(str(target), "wb") as excerpt:
        excerpt.setparams((1, 2, 16000, SAMPLES, "NONE", "not compressed"))
        excerpt.writeframes(pcm)


def replay(
    runtime: Any, audio: np.ndarray
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    from backend.processing.live_diarization.nemotron import NemotronStream

    calls: list[dict[str, Any]] = []
    saved: dict[str, np.ndarray] = {}
    states: dict[str, Any] = {}
    first_frame, start_n = 0, 0

    def observe(
        window: np.ndarray, *, is_first: bool, is_last: bool, speaker_cache: Any
    ) -> Any:
        nonlocal first_frame, start_n
        import hashlib

        result = runtime.infer_window(
            window, is_first=is_first, is_last=is_last, speaker_cache=speaker_cache
        )
        calls.append(
            {
                "index": len(calls),
                "start_n": start_n,
                "samples": len(window),
                "sha256": hashlib.sha256(window.astype("<f4").tobytes()).hexdigest(),
                "is_first": is_first,
                "is_last": is_last,
                "first_frame": first_frame,
                "frames": len(result.probabilities),
            }
        )
        first_frame += len(result.probabilities)
        start_n += 11264 if is_first else 11520
        step = len(calls)
        if step in CHECKPOINTS or is_last:
            label = "eof" if is_last else str(step)
            saved.update(
                {
                    f"{label}_{name}": value
                    for name, value in cache_arrays(result.speaker_cache).items()
                }
            )
            states[label] = {
                "call_index": step - 1,
                **cache_metadata(result.speaker_cache),
            }
        return result

    stream = NemotronStream(runtime, infer_window=observe)
    blocks = []
    for position in range(0, len(audio), 32000):
        blocks.extend(stream.feed(audio[position : position + 32000], position))
    blocks.extend(stream.flush(audio_end_n=len(audio)))
    if len(calls) != len(blocks):
        raise ValueError(
            "Every recorded model call must correspond to an emitted block"
        )
    for call, block in zip(calls, blocks, strict=True):
        call["emitted_at_n"] = block.emitted_at_n
        if call["first_frame"] != block.first_frame:
            raise ValueError("Model and packet frame clocks disagree")
    saved["probabilities"] = np.concatenate([block.probabilities for block in blocks])
    final_cache = states.pop("eof")
    return {"calls": calls, "checkpoints": states, "final_cache": final_cache}, saved


def compare_repeats(
    runs: list[tuple[dict[str, Any], dict[str, np.ndarray]]], policy: dict[str, Any]
) -> dict[str, Any]:
    baseline, arrays = runs[0]
    maximum = {"probabilities": 0.0, "cache": 0.0}
    for schedule, current in runs[1:]:
        if schedule != baseline or current.keys() != arrays.keys():
            raise ValueError("Repeat changed the window schedule or cache metadata")
        for name, expected in arrays.items():
            kind = "probabilities" if name == "probabilities" else "cache"
            actual = current[name]
            drift = float(np.max(np.abs(actual - expected), initial=0))
            maximum[kind] = max(maximum[kind], drift)
            if not np.allclose(actual, expected, **policy[kind]):
                raise ValueError(
                    f"Repeat violates pre-registered {kind} tolerance: {drift}"
                )
        a, b = arrays["probabilities"], current["probabilities"]
        if not np.array_equal(a.astype(np.float16) > 0.5, b.astype(np.float16) > 0.5):
            raise ValueError("Repeat changes stored-float16 route/activity decisions")
        if int(a.sum(axis=0).argmax()) != int(b.sum(axis=0).argmax()):
            raise ValueError("Repeat changes whole-excerpt raw-probability argmax")
    return {
        "repeats": len(runs),
        "maximum_absolute_drift": maximum,
        "tolerances": policy,
    }


def write_outputs(
    output: Path, schedule: dict[str, Any], arrays: dict[str, np.ndarray]
) -> None:
    np.savez_compressed(
        output / "native_packets.npz", probabilities=arrays["probabilities"]
    )
    np.savez_compressed(
        output / "state_checkpoints.npz",
        **{k: v for k, v in arrays.items() if k.startswith("28_")},
    )
    (output / "window_schedule.json").write_text(json.dumps(schedule, indent=2) + "\n")


def record(
    runtime: Any, args: argparse.Namespace, repeats: dict[str, Any]
) -> dict[str, Any]:
    import torch
    import transformers

    from backend.utils.nemotron_model_utils import load_asset_manifest, manifest_sha256

    gpu = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
        text=True,
    ).strip()
    artifacts = {}
    for name in (
        "es2004d-first-21_13.wav",
        "native_packets.npz",
        "state_checkpoints.npz",
        "window_schedule.json",
    ):
        path = args.output / name
        artifacts[name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    return {
        "schema_version": 1,
        "transformers_version": transformers.__version__,
        "asset_manifest_sha256": manifest_sha256(),
        "asset_revision": load_asset_manifest()["revision"],
        "source": {
            "name": "ES2004d.Mix-Headset.wav",
            "sha256": SOURCE_SHA256,
            "corpus_url": "https://groups.inf.ed.ac.uk/ami/corpus/",
            "licence": "CC BY 4.0",
            "start_sample": 0,
        },
        "sample_rate": 16000,
        "samples": SAMPLES,
        "packet_samples": 32000,
        "full_packets": SAMPLES // 32000,
        "tail_samples": SAMPLES % 32000,
        "runtime": {
            "torch": torch.__version__,
            "python": platform.python_version(),
            "gpu": gpu,
            "fingerprint": runtime.runtime_fingerprint,
            "dtype": "float32",
            "attention": "sdpa",
            "tf32_matmul": torch.backends.cuda.matmul.allow_tf32,
            "tf32_cudnn": torch.backends.cudnn.allow_tf32,
        },
        "repeat_gate": repeats,
        "tolerance_policy_sha256": sha256(args.policy),
        "generated_utc": datetime.now(UTC).isoformat(),
        "artifacts": artifacts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--policy",
        type=Path,
        required=True,
        help="pre-registered repeat tolerance JSON",
    )
    args = parser.parse_args()
    import torch

    from backend.processing.nemotron_model import release_nemotron_model_cache

    torch.set_num_threads(4)
    policy = json.loads(args.policy.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    target = args.output / "es2004d-first-21_13.wav"
    extract(args.source, target)
    runtime = local_runtime(args.model_dir)
    try:
        runs = [replay(runtime, waveform(target)) for _ in range(3)]
        measured = compare_repeats(runs, policy)
        write_outputs(args.output, *runs[0])
        result = record(runtime, args, measured)
        (args.output / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
        total = sum(
            path.stat().st_size for path in args.output.iterdir() if path.is_file()
        )
        if total >= 1500000:
            raise ValueError(f"Generated fixture exceeds 1.5 MB: {total}")
        print(json.dumps({"bytes": total, **measured}, indent=2), flush=True)
    finally:
        release_nemotron_model_cache()


if __name__ == "__main__":
    main()
