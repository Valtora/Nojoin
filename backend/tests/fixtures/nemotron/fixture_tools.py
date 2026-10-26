"""Local golden IO and test-only native state reconstruction; never downloads."""

from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np

DIRECTORY = Path(__file__).resolve().parent
ARRAY_SHAPES = {"embeds": (1, 264, 512), "probs": (1, 264, 8), "fifo": (1, 264, 512)}


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def waveform(path: Path) -> np.ndarray:
    with wave.open(str(path)) as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (
            16000,
            1,
            2,
        ):
            raise ValueError("Golden audio must be mono PCM16 at 16 kHz")
        return (
            np.frombuffer(source.readframes(source.getnframes()), "<i2").astype(
                np.float32
            )
            / 32768
        )


def manifest(directory: Path = DIRECTORY) -> dict[str, Any]:
    record = json.loads((directory / "manifest.json").read_text())
    for name, entry in record["artifacts"].items():
        path = directory / name
        if path.stat().st_size != entry["bytes"] or sha256(path) != entry["sha256"]:
            raise ValueError(f"Golden artefact does not match manifest: {name}")
    return record


def cache_arrays(cache: Any) -> dict[str, np.ndarray]:
    """Keep only live prefixes; native attention never reads unused capacity."""
    return {
        name: getattr(cache, name)[:, :count].detach().cpu().numpy().copy()
        for name, count in (
            ("embeds", cache.num_cache_frames),
            ("probs", cache.num_cache_frames),
            ("fifo", cache.num_fifo_frames),
        )
    }


def cache_metadata(cache: Any) -> dict[str, Any]:
    return {
        name: getattr(cache, name)
        for name in (
            "num_cache_frames",
            "num_fifo_frames",
            "is_initialized",
            "is_compressed",
        )
    }


def restore_cache(arrays: dict[str, np.ndarray], metadata: dict[str, Any]) -> Any:
    """Reconstruct pinned native state for tests, without a production codec claim."""
    import torch
    from transformers.models.nemotron3_diarization.configuration_nemotron3_diarization import (
        Nemotron3DiarizationStreamingConfig,
    )
    from transformers.models.nemotron3_diarization.modeling_nemotron3_diarization import (
        Nemotron3DiarizationSpeakerCache,
    )

    for name in ("num_cache_frames", "num_fifo_frames"):
        count = metadata[name]
        if type(count) is not int or not 0 <= count <= 264:
            raise ValueError(f"Invalid native checkpoint count: {name}")
    for name in ("is_initialized", "is_compressed"):
        if type(metadata[name]) is not bool:
            raise ValueError(f"Invalid native checkpoint flag: {name}")
    cache = Nemotron3DiarizationSpeakerCache(Nemotron3DiarizationStreamingConfig())
    for name, shape in ARRAY_SHAPES.items():
        value = arrays[name]
        count = metadata["num_fifo_frames" if name == "fifo" else "num_cache_frames"]
        if (
            value.shape != (shape[0], count, shape[2])
            or value.dtype != np.float32
            or not np.isfinite(value).all()
        ):
            raise ValueError(f"Invalid native checkpoint array: {name}")
        storage = torch.zeros(shape, dtype=torch.float32)
        storage[:, :count] = torch.from_numpy(value.copy())
        setattr(cache, name, storage)
    for name, value in metadata.items():
        setattr(cache, name, value)
    return cache


def local_runtime(model_directory: Path) -> Any:
    """Select explicit test assets while retaining the production verifier/loader."""
    from backend.processing import nemotron_model
    from backend.utils import nemotron_model_utils as assets

    assets.verify_nemotron_model(model_directory)
    identity = assets.load_asset_manifest()
    resolution = assets.NemotronModelResolution(
        "verified",
        model_directory,
        identity["revision"],
        assets.manifest_sha256(),
        None,
    )
    with patch.object(assets, "inspect_nemotron_model", return_value=resolution):
        return nemotron_model.load_nemotron_model(device="cuda")
