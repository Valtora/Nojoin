"""Immutable forced-aligner asset identity, safe in API and CPU processes."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from backend.utils.pinned_model_control import read_json

REPOSITORY = "Qwen/Qwen3-ForcedAligner-0.6B"
REVISION = "c7cbfc2048c462b0d63a45797104fc9db3ad62b7"
FILENAMES = {
    "chat_template.json",
    "config.json",
    "generation_config.json",
    "merges.txt",
    "model.safetensors",
    "preprocessor_config.json",
    "tokenizer_config.json",
    "vocab.json",
}
MANIFEST_PATH = Path(__file__).parents[1] / "processing" / "forced_aligner_assets.json"


class ForcedAlignerError(RuntimeError):
    """Forced-alignment assets or inference cannot be trusted or completed."""


def load_asset_manifest() -> dict[str, Any]:
    """Read the immutable complete set of local model and tokeniser assets."""
    try:
        manifest = read_json(MANIFEST_PATH)
        expected = {
            "schema_version": 1,
            "repository": REPOSITORY,
            "revision": REVISION,
            "transformers_version": "5.21.0",
        }
        if any(manifest[key] != value for key, value in expected.items()):
            raise ValueError("Unexpected manifest identity")
        entries = manifest["files"]
        if (
            len(entries) != len(FILENAMES)
            or {entry["filename"] for entry in entries} != FILENAMES
        ):
            raise ValueError("Unexpected asset files")
        for entry in entries:
            if type(entry["size"]) is not int or entry["size"] <= 0:
                raise ValueError("Missing asset size")
            if not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
                raise ValueError("Missing asset digest")
        return manifest
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ForcedAlignerError("Forced aligner asset manifest is invalid") from exc


def manifest_sha256() -> str:
    """Return the canonical manifest identity for receipts and pending tasks."""
    canonical = json.dumps(load_asset_manifest(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _reject_custom_code(value: Any) -> None:
    if isinstance(value, dict):
        if "auto_map" in value or "custom_pipelines" in value:
            raise ValueError("Custom model code is forbidden")
        for child in value.values():
            _reject_custom_code(child)
    elif isinstance(value, list):
        for child in value:
            _reject_custom_code(child)


def validate_metadata(path: Path) -> None:
    """Reject custom-code metadata before any native model loader runs."""
    for filename in FILENAMES:
        if filename.endswith(".json") and filename != "vocab.json":
            _reject_custom_code(read_json(path / filename))
    model = read_json(path / "config.json")
    if model.get("model_type") != "qwen3_asr":
        raise ValueError("Unexpected forced aligner model type")
    if model.get("thinker_config", {}).get("model_type") != "qwen3_forced_aligner":
        raise ValueError("Unexpected forced aligner configuration")
