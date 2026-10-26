"""Numeric CPU checkpoint codec for the pinned native packet scheduler.

Decoding never loads model assets. Only cache reconstruction lazily imports the
pinned native class; tests may supply a factory returning numpy-backed storage.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

import numpy as np

from backend.processing.nemotron_types import (
    NemotronGeometry,
    NemotronStateError,
    validate_audio,
    validate_cache,
)

from .nemotron import NemotronStream

CacheFactory = Callable[[NemotronGeometry], Any]
_CACHE_FIELDS = (
    "num_cache_frames",
    "num_fifo_frames",
    "is_initialized",
    "is_compressed",
)
_CURSORS = ("consumed_n", "packet_cursor_n", "next_frame")


class _UnboundRuntime:
    geometry = NemotronGeometry()

    def infer_window(self, *args: Any, **kwargs: Any) -> Any:
        raise NemotronStateError("Restored Nemotron stream needs a matching runtime")


def encode_stream(
    stream: NemotronStream,
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """Copy scheduler and CPU cache state; poisoned streams cannot be saved."""
    if stream._failed:
        raise NemotronStateError("A failed Nemotron stream cannot be checkpointed")
    meta: dict[str, Any] = {
        "geometry": asdict(stream.geometry),
        **{name: getattr(stream, name) for name in _CURSORS},
        "first": stream.first,
        "finished": stream.finished,
        "cache": None,
    }
    arrays = {
        "native_audio": stream._audio.copy(),
        "native_pending": stream._pending.copy(),
    }
    if stream.speaker_cache is not None:
        validate_cache(stream.speaker_cache, stream.geometry)
        meta["cache"] = {
            name: getattr(stream.speaker_cache, name) for name in _CACHE_FIELDS
        }
        for name in ("embeds", "probs", "fifo"):
            arrays[f"native_{name}"] = np.asarray(
                getattr(stream.speaker_cache, name)
            ).copy()
    validate_stream(meta, arrays)
    return meta, arrays


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise NemotronStateError(f"Invalid native checkpoint: {reason}")


def validate_stream(meta: dict[str, Any], arrays: dict[str, np.ndarray]) -> None:
    """Validate all numeric state without constructing a native cache."""
    geometry = NemotronGeometry()
    _require(meta["geometry"] == asdict(geometry), "geometry")
    for name in _CURSORS:
        _require(type(meta[name]) is int and meta[name] >= 0, name)
    for name in ("first", "finished"):
        _require(type(meta[name]) is bool, name)
    for name in ("native_audio", "native_pending"):
        validate_audio(arrays[name])
    pending = len(arrays["native_pending"])
    _require(pending < geometry.packet_n, "partial packet length")
    _require(meta["packet_cursor_n"] % geometry.packet_n == 0, "packet grid")
    if meta["finished"]:
        _require(pending == 0 and len(arrays["native_audio"]) == 0, "EOF buffers")
        _require(
            0 <= meta["consumed_n"] - meta["packet_cursor_n"] < geometry.packet_n,
            "EOF packet cursor",
        )
        _require(
            meta["next_frame"] * geometry.hop_length <= meta["consumed_n"],
            "EOF frame cursor",
        )
    else:
        _validate_open(meta, arrays, pending, geometry)
    cache = meta["cache"]
    _require((cache is None) == meta["first"], "first/cache agreement")
    if cache is not None:
        _require(meta["next_frame"] > 0, "cache without emitted frames")
        _validate_cache_arrays(cache, arrays, geometry)


def _validate_open(
    meta: dict[str, Any],
    arrays: dict[str, np.ndarray],
    pending: int,
    geometry: NemotronGeometry,
) -> None:
    _require(meta["consumed_n"] == meta["packet_cursor_n"] + pending, "consumed cursor")
    _require(meta["next_frame"] % geometry.num_mel_frames_per_step == 0, "frame grid")
    _require(not meta["first"] or meta["next_frame"] == 0, "first frame")
    raw_start = (
        0
        if meta["first"]
        else meta["next_frame"] * geometry.hop_length - geometry.n_fft // 2
    )
    _require(
        meta["packet_cursor_n"] - raw_start == len(arrays["native_audio"]),
        "raw overlap cursor",
    )
    window = (
        geometry.num_samples_first_audio_chunk
        if meta["first"]
        else geometry.num_samples_per_audio_chunk
    )
    _require(len(arrays["native_audio"]) < window, "undrained raw window")


def _validate_cache_arrays(
    cache: dict[str, Any],
    arrays: dict[str, np.ndarray],
    geometry: NemotronGeometry,
) -> None:
    for name in ("num_cache_frames", "num_fifo_frames"):
        maximum = (
            geometry.fifo_length
            if name == "num_fifo_frames"
            else geometry.speaker_cache_length
        )
        _require(type(cache[name]) is int and 0 <= cache[name] <= maximum, name)
    _require(cache["is_initialized"] is True, "cache initialisation")
    _require(type(cache["is_compressed"]) is bool, "cache compression")
    shapes = {
        "embeds": (1, geometry.speaker_cache_length, geometry.hidden_size),
        "probs": (1, geometry.speaker_cache_length, geometry.num_speakers),
        "fifo": (1, geometry.fifo_length, geometry.hidden_size),
    }
    for name, shape in shapes.items():
        value = arrays[f"native_{name}"]
        _require(
            value.shape == shape and value.dtype == np.float32,
            f"cache {name} shape/dtype",
        )
        _require(bool(np.isfinite(value).all()), f"cache {name} values")
    probs = arrays["native_probs"]
    _require(bool(np.all((probs >= 0) & (probs <= 1))), "cache probabilities")


def _native_cache(geometry: NemotronGeometry) -> Any:
    from transformers.models.nemotron3_diarization.configuration_nemotron3_diarization import (
        Nemotron3DiarizationStreamingConfig,
    )
    from transformers.models.nemotron3_diarization.modeling_nemotron3_diarization import (
        Nemotron3DiarizationSpeakerCache,
    )

    config = Nemotron3DiarizationStreamingConfig(
        speaker_cache_length=geometry.speaker_cache_length,
        fifo_length=geometry.fifo_length,
        speaker_cache_update_period=geometry.speaker_cache_update_period,
        num_speakers=geometry.num_speakers,
        subsampling_factor=geometry.subsampling_factor,
    )
    return Nemotron3DiarizationSpeakerCache(config)


def restore_stream(
    meta: dict[str, Any],
    arrays: dict[str, np.ndarray],
    *,
    cache_factory: CacheFactory | None = None,
) -> NemotronStream:
    """Restore a validated stream, unbound until the caller supplies its runtime."""
    validate_stream(meta, arrays)
    stream = NemotronStream(_UnboundRuntime())
    for name in (*_CURSORS, "first", "finished"):
        setattr(stream, name, meta[name])
    stream._audio = arrays["native_audio"].copy()
    stream._pending = arrays["native_pending"].copy()
    if meta["cache"] is not None:
        stream.speaker_cache = _restore_cache(
            meta["cache"], arrays, cache_factory, stream.geometry
        )
    return stream


def _restore_cache(
    meta: dict[str, Any],
    arrays: dict[str, np.ndarray],
    factory: CacheFactory | None,
    geometry: NemotronGeometry,
) -> Any:
    cache = (factory or _native_cache)(geometry)
    for name in ("embeds", "probs", "fifo"):
        value: Any = arrays[f"native_{name}"].copy()
        if factory is None:
            import torch

            value = torch.from_numpy(value)
        setattr(cache, name, value)
    for name in _CACHE_FIELDS:
        setattr(cache, name, meta[name])
    validate_cache(cache, geometry)
    return cache
