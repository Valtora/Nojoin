"""Small local assets exercise verification without network or model weights."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from backend.utils import nemotron_manifest as manifest
from backend.utils import nemotron_model_utils as assets


def write_manifest(path: Path, payloads: dict[str, bytes]) -> None:
    data: dict[str, Any] = {
        "schema_version": 1,
        "repository": manifest.REPOSITORY,
        "revision": manifest.REVISION,
        "transformers_version": "5.20.2",
        "files": [
            {
                "filename": name,
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            for name, payload in payloads.items()
        ],
    }
    path.write_text(json.dumps(data))


@pytest.fixture
def native_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, dict[str, bytes]]:
    model = {
        "model_type": "nemotron3_diarization",
        "architectures": ["Nemotron3DiarizationForAudioFrameClassification"],
        "audio_config": {
            "num_mel_bins": 128,
            "hidden_size": 512,
            "subsampling_factor": 8,
        },
        "head_config": {"num_speakers": 8, "subsampling_factor": 8},
        "chunk_length": 340,
        "chunk_right_context": 40,
        "fifo_length": 40,
        "speaker_cache_update_period": 300,
        "streaming_config": {
            "speaker_cache_length": 264,
            "fifo_length": 264,
            "speaker_cache_update_period": 222,
            "num_speakers": 8,
            "subsampling_factor": 8,
        },
    }
    processor = {
        "processor_class": "Nemotron3DiarizationProcessor",
        "streaming_mode": "low_latency",
        "streaming_modes": {"low_latency": [9, 4]},
        "subsampling_factor": 8,
        "feature_extractor": {
            "sampling_rate": 16000,
            "feature_size": 128,
            "n_fft": 512,
            "win_length": 400,
            "hop_length": 160,
            "preemphasis": 0.97,
            "padding_side": "right",
            "padding_value": 0.0,
            "feature_extractor_type": "NemotronAsrStreamingFeatureExtractor",
        },
    }
    payloads = {
        "config.json": json.dumps(model).encode(),
        "processor_config.json": json.dumps(processor).encode(),
        "model.safetensors": b"local test weight bytes",
    }
    test_manifest = tmp_path / "manifest.json"
    write_manifest(test_manifest, payloads)
    monkeypatch.setattr(manifest, "MANIFEST_PATH", test_manifest)
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    snapshot = assets.nemotron_repo_path() / "snapshots" / manifest.REVISION
    return snapshot, payloads


def local_files(snapshot: Path, payloads: dict[str, bytes]) -> None:
    snapshot.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads.items():
        (snapshot / name).write_bytes(payload)
