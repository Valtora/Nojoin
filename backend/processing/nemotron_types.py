"""Lightweight Nemotron window contracts and pinned sample geometry.

This module and the packet scheduler do not import Torch or Transformers.
Audio is already mono float32 PCM at 16 kHz when it reaches this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

NEMOTRON_TRANSFORMERS_VERSION = "5.21.0"


class NemotronStateError(RuntimeError):
    """Audio, scheduling or a returned native state violates the runtime contract."""


@dataclass(frozen=True)
class NemotronGeometry:
    """The evaluated low-latency geometry; settings cannot override it."""

    sample_rate: int = 16000
    packet_n: int = 32000
    hop_length: int = 160
    n_fft: int = 512
    win_length: int = 400
    feature_size: int = 128
    preemphasis: float = 0.97
    subsampling_factor: int = 8
    chunk_length: int = 9
    chunk_right_context: int = 4
    speaker_cache_length: int = 264
    fifo_length: int = 264
    speaker_cache_update_period: int = 222
    hidden_size: int = 512
    num_speakers: int = 8

    @property
    def num_mel_frames_per_step(self) -> int:
        return self.chunk_length * self.subsampling_factor

    @property
    def num_mel_frames_per_audio_chunk(self) -> int:
        return (self.chunk_length + self.chunk_right_context) * self.subsampling_factor

    @property
    def num_samples_first_audio_chunk(self) -> int:
        return (
            self.num_mel_frames_per_audio_chunk - 1
        ) * self.hop_length + self.win_length // 2

    @property
    def num_samples_per_audio_chunk(self) -> int:
        return self.num_mel_frames_per_audio_chunk * self.hop_length + self.win_length

    def advance_n(self, *, first: bool) -> int:
        advance = self.num_mel_frames_per_step * self.hop_length
        return advance - self.n_fft // 2 if first else advance

    def output_frames(self, samples: int, *, first: bool, last: bool) -> int:
        if not last:
            return self.num_mel_frames_per_step
        if first:
            return samples // self.hop_length
        padded = samples + self.n_fft // 2 - self.hop_length
        return max(0, (padded - self.n_fft) // self.hop_length + 1)


@dataclass(frozen=True)
class ProbabilityBlock:
    """New 10 ms frames, with the packet receipt that made them available."""

    first_frame: int
    probabilities: np.ndarray
    emitted_at_n: int


@dataclass(frozen=True)
class NativeWindowResult:
    """Owned CPU fp32 posteriors and the per-stream native cache, if initialised."""

    probabilities: np.ndarray
    speaker_cache: Any | None


class WindowInfer(Protocol):
    def __call__(
        self,
        audio_window: np.ndarray,
        *,
        is_first: bool,
        is_last: bool,
        speaker_cache: Any | None,
    ) -> NativeWindowResult: ...


class WindowRuntime(Protocol):
    @property
    def geometry(self) -> NemotronGeometry: ...

    def infer_window(
        self,
        audio_window: np.ndarray,
        *,
        is_first: bool,
        is_last: bool,
        speaker_cache: Any | None,
    ) -> NativeWindowResult: ...


def validate_audio(audio: np.ndarray) -> None:
    """Reject malformed audio before buffering or feature extraction."""
    if (
        not isinstance(audio, np.ndarray)
        or audio.dtype != np.float32
        or audio.ndim != 1
    ):
        raise NemotronStateError(
            "Nemotron needs a mono float32 audio array at 16000 Hz"
        )
    if not np.isfinite(audio).all():
        raise NemotronStateError("Nemotron audio contains non-finite samples")


def validate_probabilities(probabilities: np.ndarray, frames: int) -> None:
    if not isinstance(probabilities, np.ndarray) or probabilities.dtype != np.float32:
        raise NemotronStateError("Nemotron probabilities must be a CPU float32 array")
    if probabilities.shape != (frames, 8):
        raise NemotronStateError(
            f"Nemotron returned {probabilities.shape}, expected {(frames, 8)}"
        )
    if not np.isfinite(probabilities).all() or np.any(
        (probabilities < 0) | (probabilities > 1)
    ):
        raise NemotronStateError(
            "Nemotron probabilities contain non-finite or out-of-range values"
        )


def validate_cache(cache: Any, geometry: NemotronGeometry) -> None:
    """Check native or synthetic CPU cache storage without importing Torch."""
    policy = {
        "speaker_cache_length": geometry.speaker_cache_length,
        "fifo_length": geometry.fifo_length,
        "speaker_cache_update_period": geometry.speaker_cache_update_period,
        "num_speakers": geometry.num_speakers,
        "subsampling_factor": geometry.subsampling_factor,
    }
    if any(getattr(cache, name, None) != value for name, value in policy.items()):
        raise NemotronStateError(
            "Nemotron cache policy differs from the streaming geometry"
        )
    expected = {
        "embeds": (1, geometry.speaker_cache_length, geometry.hidden_size),
        "probs": (1, geometry.speaker_cache_length, geometry.num_speakers),
        "fifo": (1, geometry.fifo_length, geometry.hidden_size),
    }
    for name, shape in expected.items():
        value = getattr(cache, name, None)
        if getattr(value, "shape", None) != shape:
            raise NemotronStateError(
                f"Nemotron cache {name} has incompatible dimensions"
            )
        device = getattr(value, "device", None)
        if device is not None and str(device) != "cpu":
            raise NemotronStateError(
                f"Nemotron cache {name} must be CPU-owned between windows"
            )
        array = np.asarray(value)
        if array.dtype != np.float32 or not np.isfinite(array).all():
            raise NemotronStateError(
                f"Nemotron cache {name} must contain finite float32 values"
            )
    for name in ("num_cache_frames", "num_fifo_frames"):
        count = getattr(cache, name, None)
        if type(count) is not int or not 0 <= count <= geometry.speaker_cache_length:
            raise NemotronStateError(f"Nemotron cache {name} is invalid")
    if (
        getattr(cache, "is_initialized", None) is not True
        or type(getattr(cache, "is_compressed", None)) is not bool
    ):
        raise NemotronStateError(
            "Nemotron cache initialisation/compression flags are invalid"
        )
    if np.any((np.asarray(cache.probs) < 0) | (np.asarray(cache.probs) > 1)):
        raise NemotronStateError("Nemotron cache contains out-of-range probabilities")
