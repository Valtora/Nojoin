"""Immutable native diarisation asset identity; safe in API/CPU processes."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

REPOSITORY = "nvidia/Nemotron-3-Diarization"
REVISION = "f667ed73aee57d40cc39428eb768b4fd87a0a29e"
FILENAMES = {"config.json", "processor_config.json", "model.safetensors"}
MANIFEST_PATH = Path(__file__).parents[1] / "processing" / "nemotron_assets.json"


class NemotronModelError(RuntimeError):
    """Managed native diarisation assets cannot be trusted or prepared."""


class NemotronOfflineError(NemotronModelError):
    """Required assets are absent or invalid while Hub downloads are disabled."""


def load_asset_manifest() -> dict[str, Any]:
    """Read a complete immutable three-file manifest, refusing overrides."""
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        _validate_manifest(manifest)
        return manifest
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise NemotronModelError("Native speaker asset manifest is invalid") from exc


def _validate_manifest(manifest: dict[str, Any]) -> None:
    expected = {
        "schema_version": 1,
        "repository": REPOSITORY,
        "revision": REVISION,
        "transformers_version": "5.20.2",
    }
    if any(manifest[key] != value for key, value in expected.items()):
        raise ValueError("Unexpected manifest identity")
    entries = manifest["files"]
    if len(entries) != 3 or {entry["filename"] for entry in entries} != FILENAMES:
        raise ValueError("Unexpected asset files")
    for entry in entries:
        if type(entry["size"]) is not int or entry["size"] <= 0:
            raise ValueError("Missing asset size")
        if not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
            raise ValueError("Missing asset digest")


def manifest_sha256() -> str:
    """Return the canonical manifest identity used by receipts and caches."""
    canonical = json.dumps(load_asset_manifest(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    """Read bounded JSON without importing model dependencies."""
    with path.open("rb") as source:
        data = source.read(65537)
    if len(data) > 65536:
        raise ValueError("Asset metadata is too large")
    parsed = json.loads(data)
    if not isinstance(parsed, dict):
        raise ValueError("Asset metadata must be an object")
    return parsed


def validate_geometry(path: Path) -> None:
    """Reject custom code, unexpected native classes and changed geometry."""
    model = read_json(path / "config.json")
    processor = read_json(path / "processor_config.json")
    _reject_custom_code(model)
    _reject_custom_code(processor)
    checks = [
        (model["model_type"], "nemotron3_diarization"),
        (model["architectures"], ["Nemotron3DiarizationForAudioFrameClassification"]),
        (model["audio_config"]["num_mel_bins"], 128),
        (model["audio_config"]["hidden_size"], 512),
        (model["audio_config"]["subsampling_factor"], 8),
        (model["head_config"]["num_speakers"], 8),
        (model["head_config"]["subsampling_factor"], 8),
        (model["chunk_length"], 340),
        (model["chunk_right_context"], 40),
        (model["fifo_length"], 40),
        (model["speaker_cache_update_period"], 300),
        (processor["processor_class"], "Nemotron3DiarizationProcessor"),
        (processor["streaming_mode"], "low_latency"),
        (processor["streaming_modes"]["low_latency"], [9, 4]),
        (processor["subsampling_factor"], 8),
        (processor["feature_extractor"]["sampling_rate"], 16000),
        (processor["feature_extractor"]["feature_size"], 128),
        (processor["feature_extractor"]["n_fft"], 512),
        (processor["feature_extractor"]["win_length"], 400),
        (processor["feature_extractor"]["hop_length"], 160),
        (processor["feature_extractor"]["preemphasis"], 0.97),
        (processor["feature_extractor"]["padding_side"], "right"),
        (processor["feature_extractor"]["padding_value"], 0.0),
        (
            processor["feature_extractor"]["feature_extractor_type"],
            "NemotronAsrStreamingFeatureExtractor",
        ),
    ]
    streaming = model["streaming_config"]
    checks.extend(
        (streaming[key], value)
        for key, value in {
            "speaker_cache_length": 264,
            "fifo_length": 264,
            "speaker_cache_update_period": 222,
            "num_speakers": 8,
            "subsampling_factor": 8,
        }.items()
    )
    if any(actual != expected for actual, expected in checks):
        raise ValueError("Unexpected native speaker geometry")


def _reject_custom_code(value: Any) -> None:
    if isinstance(value, dict):
        if "auto_map" in value:
            raise ValueError("Custom model code is forbidden")
        for child in value.values():
            _reject_custom_code(child)
    elif isinstance(value, list):
        for child in value:
            _reject_custom_code(child)
