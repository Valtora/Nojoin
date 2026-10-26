"""Bounded packet/window scheduling for one native Nemotron stream.

The caller owns audio decoding and persistence. This object owns no model,
database or dispatch work; WindowInfer is the only replay injection boundary.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from backend.processing.nemotron_types import (
    NativeWindowResult,
    NemotronGeometry,
    NemotronStateError,
    ProbabilityBlock,
    WindowInfer,
    WindowRuntime,
    validate_audio,
    validate_cache,
    validate_probabilities,
)


class NemotronStream:
    """Consume contiguous PCM, trim duplicate prefixes and emit packet-timed frames."""

    def __init__(
        self, runtime: WindowRuntime, *, infer_window: WindowInfer | None = None
    ) -> None:
        if runtime.geometry != NemotronGeometry():
            raise NemotronStateError(
                "Nemotron stream geometry differs from the evaluated runtime"
            )
        self.geometry = runtime.geometry
        self._infer: WindowInfer = (
            infer_window if infer_window is not None else runtime.infer_window
        )
        self.speaker_cache: Any | None = None
        self.consumed_n = 0
        self.packet_cursor_n = 0
        self.next_frame = 0
        self.first = True
        self.finished = False
        self._failed = False
        self._audio = np.empty(0, np.float32)
        self._pending = np.empty(0, np.float32)

    @property
    def buffer(self) -> np.ndarray:
        """An owned copy of overlapping native audio followed by the partial packet."""
        return np.concatenate((self._audio, self._pending))

    def _check_open(self) -> None:
        if self._failed:
            raise NemotronStateError(
                "Nemotron stream failed; its mutated cache cannot be reused"
            )
        if self.finished:
            raise NemotronStateError("Nemotron stream has already received EOF")

    def feed(self, audio: np.ndarray, start_n: int) -> list[ProbabilityBlock]:
        """Buffer unseen samples; infer only at chronological 32,000-sample receipts."""
        self._check_open()
        validate_audio(audio)
        self._check_position(start_n, "audio start")
        if start_n > self.consumed_n:
            self._failed = True
            raise NemotronStateError(
                f"Nemotron audio gap: expected {self.consumed_n}, got {start_n}"
            )
        unseen = audio[max(0, self.consumed_n - start_n) :]
        blocks: list[ProbabilityBlock] = []
        while len(unseen):
            count = min(self.geometry.packet_n - len(self._pending), len(unseen))
            self._pending = np.concatenate((self._pending, unseen[:count]))
            self.consumed_n += count
            unseen = unseen[count:]
            if len(self._pending) == self.geometry.packet_n:
                self._audio = np.concatenate((self._audio, self._pending))
                self._pending = np.empty(0, np.float32)
                self.packet_cursor_n += self.geometry.packet_n
                blocks.extend(self._drain(self.packet_cursor_n))
        return blocks

    @staticmethod
    def _check_position(position: int, description: str) -> None:
        if type(position) is not int or position < 0:
            raise NemotronStateError(
                f"Nemotron {description} must be a non-negative integer sample index"
            )

    def _drain(self, emitted_at_n: int) -> list[ProbabilityBlock]:
        blocks: list[ProbabilityBlock] = []
        size = self._window_size()
        while len(self._audio) >= size:
            block = self._infer_next(
                self._audio[:size], last=False, emitted_at_n=emitted_at_n
            )
            if block is not None:
                blocks.append(block)
            size = self._window_size()
        return blocks

    def _window_size(self) -> int:
        if self.first:
            return self.geometry.num_samples_first_audio_chunk
        return self.geometry.num_samples_per_audio_chunk

    def _infer_next(
        self, window: np.ndarray, *, last: bool, emitted_at_n: int
    ) -> ProbabilityBlock | None:
        frames = self.geometry.output_frames(len(window), first=self.first, last=last)
        if frames == 0:
            return None
        # Native forwards update their cache in place. Any failed call or invalid
        # result permanently poisons this instance, even if its cursors did not move.
        self._failed = True
        try:
            result = self._infer(
                window.copy(),
                is_first=self.first,
                is_last=last,
                speaker_cache=self.speaker_cache,
            )
            self._validate_result(result, frames)
        except (RuntimeError, ValueError, TypeError, AttributeError) as exc:
            raise NemotronStateError(
                "Nemotron window failed; this stream cannot resume"
            ) from exc
        block = ProbabilityBlock(
            self.next_frame, result.probabilities.copy(), emitted_at_n
        )
        advance = self.geometry.advance_n(first=self.first)
        self.speaker_cache = result.speaker_cache
        self.next_frame += frames
        self.first = False
        self._audio = np.empty(0, np.float32) if last else self._audio[advance:].copy()
        self._failed = False
        return block

    def _validate_result(self, result: NativeWindowResult, frames: int) -> None:
        if not isinstance(result, NativeWindowResult):
            raise NemotronStateError(
                "Nemotron inference did not return NativeWindowResult"
            )
        validate_probabilities(result.probabilities, frames)
        if result.speaker_cache is None:
            raise NemotronStateError(
                "Nemotron inference returned frames without a streaming cache"
            )
        validate_cache(result.speaker_cache, self.geometry)

    def flush(self, *, audio_end_n: int) -> list[ProbabilityBlock]:
        """Deliver the final partial packet and EOF once, preserving the true end."""
        self._check_position(audio_end_n, "audio end")
        if self._failed:
            self._check_open()
        if audio_end_n != self.consumed_n:
            raise NemotronStateError(
                "Nemotron EOF does not match the consumed audio end"
            )
        if self.finished:
            return []
        self._audio = np.concatenate((self._audio, self._pending))
        self._pending = np.empty(0, np.float32)
        blocks = self._drain(audio_end_n)
        if len(self._audio):
            block = self._infer_next(self._audio, last=True, emitted_at_n=audio_end_n)
            if block is not None:
                blocks.append(block)
        self._audio = np.empty(0, np.float32)
        self.finished = True
        return blocks
