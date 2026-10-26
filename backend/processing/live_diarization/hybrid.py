"""Packet-clock native labels with a causal, permanent clustering hand-back."""

from __future__ import annotations

import hashlib
from itertools import groupby
from uuid import uuid4

import numpy as np

from backend.processing.nemotron_types import ProbabilityBlock, validate_audio
from backend.utils.canonical_pipeline.live_labels import LiveLabelDecision

from .clustering import OnlineSpeakerClustering
from .engine import FeedResult, LiveLabelConfig, LiveLabelEngine, ModelFn
from .hybrid_identity import allocate_labels
from .hybrid_lines import label_line
from .hybrid_overlap import add_overlap
from .nemotron import NemotronStream
from .recluster import UnitBank, UnitTable

PACKET_N = 32000
NATIVE_ERRORS = (RuntimeError, ValueError, TypeError, ImportError, OSError, MemoryError)


def failure_reason(error: BaseException) -> str:
    """Classify catchable errors without retaining a traceback or GPU tensors."""
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, MemoryError) or "out of memory" in str(current).lower():
            return "oom"
        current = current.__cause__
    return "import_failed" if isinstance(error, ImportError) else "state_incompatible"


class HybridLiveState:
    """One capture's fixed selection, acoustic engines and displayed namespace."""

    def __init__(
        self,
        shadow: LiveLabelEngine,
        stream: NemotronStream | None,
        *,
        speaker_engine: str = "auto",
        reason: str | None = None,
        fingerprints: tuple[str, str] = ("", ""),
    ) -> None:
        self.shadow = shadow
        self.stream = stream
        self.speaker_engine = speaker_engine
        self.reason = reason
        self.asset_fingerprint, self.runtime_fingerprint = fingerprints
        self.route = "nemotron" if stream is not None else "clustering"
        self.consumed_n = 0
        self.packet_cursor_n = 0
        self.pending = np.empty(0, np.float32)
        self.channel_frames = np.zeros(8, np.int64)
        self.used_eighth_channel = False
        self.switch_emitted_at_n: int | None = None
        self.native_complete_frame = 0
        self.posterior_first_frame = 0
        self.posteriors = np.empty((0, 8), np.float32)
        self.overlap = np.empty((0, 8), np.float64)
        self.overlap_until_s = 0.0
        self.translation: dict[int, int] = {}
        self.display_labels = np.empty(0, np.int64)
        self.next_display_id = 8
        self.recording_id = 0
        self.capture_generation = str(uuid4())
        self.source_sequence = -1
        self.source_sample_offset = 0
        self.source_tail_sha256 = ""

    @property
    def config(self) -> LiveLabelConfig:
        return self.shadow.config

    @property
    def clustering(self) -> OnlineSpeakerClustering:
        return self.shadow.clustering

    @property
    def units(self) -> UnitTable:
        return self.shadow.units

    @property
    def bank(self) -> UnitBank:
        return self.shadow.bank

    @property
    def settled_ms(self) -> int:
        return self.shadow.settled_ms

    @settled_ms.setter
    def settled_ms(self, value: int) -> None:
        self.shadow.settled_ms = value

    def feed(self, audio: np.ndarray, start_n: int, model: ModelFn) -> FeedResult:
        validate_audio(audio)
        NemotronStream._check_position(start_n, "audio start")
        if self.route == "clustering" and self.switch_emitted_at_n is None:
            # Configured/first-use fallback retains today's engine batching and
            # partial-input behaviour. There is no arrival-order native cache.
            result = self.shadow.feed(audio, start_n, model)
            self.consumed_n = self.packet_cursor_n = self.shadow.audio_end_n
            self._allocate_labels()
            self.source_tail_sha256 = hashlib.sha256(
                self.shadow.audio[-PACKET_N:].tobytes()
            ).hexdigest()
            return result
        result = FeedResult()
        self._check_duplicate_source(audio, start_n)
        if start_n > self.consumed_n:
            self.fallback("audio_gap", self.consumed_n)
            if len(self.pending):
                self.shadow.feed(self.pending, self.packet_cursor_n, model)
                self._allocate_labels()
            self.pending = np.empty(0, np.float32)
            self.consumed_n = self.packet_cursor_n = start_n
        unseen = audio[max(0, self.consumed_n - start_n) :]
        while len(unseen):
            count = min(PACKET_N - len(self.pending), len(unseen))
            self.pending = np.concatenate((self.pending, unseen[:count]))
            self.consumed_n += count
            unseen = unseen[count:]
            if len(self.pending) == PACKET_N:
                packet = self.pending
                self.pending = np.empty(0, np.float32)
                self._packet(packet, model, result)
        self.source_tail_sha256 = hashlib.sha256(
            self._source_tail().tobytes()
        ).hexdigest()
        return result

    def _source_tail(self) -> np.ndarray:
        if not self.consumed_n:
            return np.empty(0, np.float32)
        return np.concatenate((self.shadow.audio, self.pending))[
            -min(self.consumed_n, PACKET_N) :
        ]

    def _check_duplicate_source(self, audio: np.ndarray, start_n: int) -> None:
        if self.route != "nemotron" or not self.consumed_n:
            return
        tail = self._source_tail()
        tail_start = self.consumed_n - len(tail)
        overlap_start = max(start_n, tail_start)
        overlap_end = min(start_n + len(audio), self.consumed_n)
        if overlap_end <= overlap_start:
            return
        previous = tail[overlap_start - tail_start : overlap_end - tail_start]
        supplied = audio[overlap_start - start_n : overlap_end - start_n]
        if not np.array_equal(previous, supplied):
            self.fallback("state_incompatible", self.consumed_n)

    def _packet(self, packet: np.ndarray, model: ModelFn, result: FeedResult) -> None:
        start_n = self.packet_cursor_n
        shadow_result = self.shadow.feed(packet, start_n, model)
        result.steps += shadow_result.steps
        result.founded += shadow_result.founded
        result.backfilled += shadow_result.backfilled
        result.reclusters.extend(shadow_result.reclusters)
        self.packet_cursor_n += len(packet)
        reason = None
        if self.stream is not None:
            try:
                self.accept_blocks(self.stream.feed(packet, start_n))
            except NATIVE_ERRORS as exc:
                reason = failure_reason(exc)
        # Outside the exception handler: release traceback-held native tensors.
        if reason is not None:
            self.fallback(reason, self.packet_cursor_n)
            from .hybrid_runtime import release_native

            release_native()
        self._accumulate_stable_overlap()
        if self.route == "clustering":
            self._allocate_labels()

    def accept_blocks(self, blocks: list[ProbabilityBlock]) -> None:
        # All windows made available by one packet share an emission event.
        # Accept its complete output before freezing that event's boundary.
        for emitted_at_n, event in groupby(blocks, key=lambda b: b.emitted_at_n):
            if self.route != "nemotron":
                break
            for block in event:
                if block.first_frame != self.native_complete_frame:
                    raise ValueError("Native frames are not contiguous")
                self.posteriors = np.concatenate((self.posteriors, block.probabilities))
                self.native_complete_frame += len(block.probabilities)
                active = block.probabilities.astype(np.float16) > 0.5
                self.channel_frames += active.sum(axis=0, dtype=np.int64)
                self.used_eighth_channel |= bool(active[:, 7].any())
            if bool((self.channel_frames >= 200).all()):
                self.fallback("channel_cap", emitted_at_n)

    def _accumulate_stable_overlap(self) -> None:
        if self.route != "nemotron":
            return
        # A backfill can move a piece to another unit only within the model window.
        stable_until = max(
            self.overlap_until_s,
            min(
                self.native_complete_frame / 100,
                self.shadow.processed_until_s - self.config.window_s,
            ),
        )
        self.overlap = add_overlap(
            self.shadow,
            self.posteriors,
            self.posterior_first_frame,
            self.overlap,
            (self.overlap_until_s, stable_until),
        )
        self.overlap_until_s = stable_until

    def fallback(self, reason: str, emitted_at_n: int) -> None:
        if self.route == "clustering":
            return
        self.overlap = add_overlap(
            self.shadow,
            self.posteriors,
            self.posterior_first_frame,
            self.overlap,
            (
                self.overlap_until_s,
                min(self.native_complete_frame / 100, self.shadow.processed_until_s),
            ),
        )
        labels = np.asarray(self.units.label, np.int64)
        for label in sorted(set(labels.tolist()) - {self.shadow.held_slot}):
            support = self.overlap[labels == label].sum(axis=0)
            self.translation[label] = (
                int(np.argmax(support)) if support.max() > 0 else self._fresh_id()
            )
        self.display_labels = labels.copy()
        self.route = "clustering"
        self.reason = reason
        self.switch_emitted_at_n = emitted_at_n
        self.stream = None
        self._allocate_labels()

    def _fresh_id(self) -> int:
        value = self.next_display_id
        self.next_display_id += 1
        return value

    def _allocate_labels(self) -> None:
        allocate_labels(self)

    def label_line(self, start_s: float, end_s: float) -> LiveLabelDecision:
        return label_line(self, start_s, end_s)

    def prune_pieces(self, before_s: float) -> None:
        piece_before_s = before_s
        if self.route == "nemotron":
            piece_before_s = min(before_s, self.overlap_until_s)
        self.shadow.prune_pieces(piece_before_s)
        keep_s = (
            min(before_s, self.overlap_until_s)
            if self.route == "nemotron"
            else before_s
        )
        remove = min(
            len(self.posteriors), max(0, int(keep_s * 100) - self.posterior_first_frame)
        )
        self.posteriors = self.posteriors[remove:].copy()
        self.posterior_first_frame += remove
