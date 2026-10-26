"""CPU-only test caches and window observations for the real packet scheduler."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

import numpy as np

from backend.processing.nemotron_types import NativeWindowResult, NemotronGeometry


def cache_with_token(token: int) -> Any:
    cache = SimpleNamespace(
        embeds=np.zeros((1, 264, 512), np.float32),
        probs=np.zeros((1, 264, 8), np.float32),
        fifo=np.zeros((1, 264, 512), np.float32),
        num_cache_frames=0,
        num_fifo_frames=0,
        is_initialized=True,
        is_compressed=False,
        speaker_cache_length=264,
        fifo_length=264,
        speaker_cache_update_period=222,
        num_speakers=8,
        subsampling_factor=8,
    )
    cache.embeds[0, 0, 0] = token
    return cache


class ObservedRuntime:
    geometry: NemotronGeometry
    calls: list[tuple[str, int, bool, bool]]

    def __init__(self) -> None:
        self.geometry = NemotronGeometry()
        self.calls = []

    def infer_window(
        self,
        audio_window: np.ndarray,
        *,
        is_first: bool,
        is_last: bool,
        speaker_cache: Any | None,
    ) -> NativeWindowResult:
        previous = 0 if speaker_cache is None else int(speaker_cache.embeds[0, 0, 0])
        assert previous == len(self.calls), (
            "cache token did not follow the window sequence"
        )
        self.calls.append(
            (
                hashlib.sha256(audio_window.tobytes()).hexdigest(),
                len(audio_window),
                is_first,
                is_last,
            )
        )
        frames = self.geometry.output_frames(
            len(audio_window), first=is_first, last=is_last
        )
        probabilities = np.full((frames, 8), 0.125, np.float32)
        return NativeWindowResult(probabilities, cache_with_token(previous + 1))
