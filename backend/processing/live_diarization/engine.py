"""The live label engine: audio in, a speaker label per piece of speech out.

Every ``step_s`` (0.5 s) the segmentation and embedding models score the last
``window_s`` (5 s) of audio. The clustering assigns each local speaker, and the
last ``step_s`` of the window becomes pieces of speech, each in a turn unit.
A held voice's piece shows no label; while it is still inside the window, a
later step labels it once a confidently assigned local speaker covers at least
``backfill_cover`` of its frames (back-fill). Every ``packet_s`` the engine
checks whether a re-cluster is due (``recluster.py``).

The engine holds no model and no database: ``feed`` takes the model as a
callable, and the caller persists the engine with ``state.py`` between runs.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

import numpy as np

from .clustering import ClusteringParams, OnlineSpeakerClustering, StepAssignment
from .recluster import (
    ReclusterParams,
    ReclusterResult,
    StepObservation,
    UnitBank,
    UnitTable,
    recluster,
)

# windows (batch, samples) -> segmentation (batch, frames, local) and
# unit-norm embeddings (batch, local, dim).
ModelFn = Callable[[np.ndarray], tuple[np.ndarray, np.ndarray]]


@dataclass(frozen=True)
class LiveLabelConfig:
    """Every threshold of the live label engine.

    The defaults are the ``x_d45h_rc10`` policy of issue #487, tuned on one
    House of Lords sitting and checked on three held-out ones.
    """

    sample_rate: int = 16000
    window_s: float = 5.0
    step_s: float = 0.5
    packet_s: float = 2.0
    embedding_dim: int = 256
    model_batch: int = 8
    backfill_cover: float = 0.5
    overlap_gamma: float = 3.0
    overlap_beta: float = 10.0
    clustering: ClusteringParams = ClusteringParams(
        tau_active=0.6,
        rho_update=0.6,
        delta_new=0.45,
        share_delta=0.6,
        max_speakers=128,
    )
    recluster: ReclusterParams = ReclusterParams(
        every_s=10.0,
        threshold=0.45,
        min_units=2,
        max_steps=10,
        lock_passes=2,
        lock_age_s=600.0,
        recent_s=1200.0,
        max_units=1000,
        anchors=10,
    )

    @property
    def window_n(self) -> int:
        return int(self.window_s * self.sample_rate)

    @property
    def step_n(self) -> int:
        return int(self.step_s * self.sample_rate)

    @property
    def packet_n(self) -> int:
        return int(self.packet_s * self.sample_rate)

    def fingerprint(self) -> str:
        return hashlib.sha256(repr(asdict(self)).encode()).hexdigest()[:16]


@dataclass
class Piece:
    """A stretch of one local speaker's speech, in seconds on the recording.

    ``unit`` is its turn unit, whose label it shows. ``open`` marks a held
    piece that a later step may still back-fill.
    """

    start: float
    end: float
    unit: int
    open: bool = False


@dataclass
class FeedResult:
    steps: int = 0
    founded: int = 0
    backfilled: int = 0
    reclusters: list[ReclusterResult] = field(default_factory=list)


@dataclass
class _Window:
    """One step's model output, placed on the recording."""

    end_n: int
    step: int
    segmentation: np.ndarray
    embeddings: np.ndarray


class LiveLabelEngine:
    """Clustering, units and pieces for one recording, between saves."""

    def __init__(self, config: LiveLabelConfig, bank: UnitBank) -> None:
        self.config = config
        self.bank = bank
        self.clustering = OnlineSpeakerClustering(config.clustering)
        self.units = UnitTable(config.clustering.max_speakers, config.embedding_dim)
        self.pieces: list[Piece] = []
        self.audio = np.zeros(config.window_n, np.float32)
        # Sample positions on the recording timeline; None until audio arrives.
        self.origin_n: int | None = None
        self.audio_end_n = 0
        self.next_end_n = 0
        self.next_recluster_s = 0.0
        # Every live line ending at or before this has its final label.
        self.settled_ms = 0

    @property
    def held_slot(self) -> int:
        return self.clustering.held_slot

    @property
    def processed_until_s(self) -> float:
        """End of the last step: every piece before it has been emitted."""
        return (self.next_end_n - self.config.step_n) / self.config.sample_rate

    def _start(self, start_n: int) -> None:
        self.origin_n = start_n
        self.audio_end_n = start_n
        self.next_end_n = start_n + self.config.step_n
        self.next_recluster_s = start_n / self.config.sample_rate + (
            self.config.recluster.every_s
        )
        self.settled_ms = int(round(start_n * 1000 / self.config.sample_rate))

    def _align_to(self, start_n: int, audio: np.ndarray) -> np.ndarray:
        """Trim audio already seen, or bridge a gap before it."""
        if start_n < self.audio_end_n:
            return audio[self.audio_end_n - start_n :]
        gap = start_n - self.audio_end_n
        if gap == 0:
            return audio
        if gap <= self.config.window_n:
            return np.concatenate([np.zeros(gap, np.float32), audio])
        # A long gap (a failed run, a resumed lane): restart the window there
        # and move the step grid past it, keeping every speaker.
        assert self.origin_n is not None
        step_n = self.config.step_n
        self.audio = np.zeros(self.config.window_n, np.float32)
        self.audio_end_n = start_n
        steps_in = (start_n - self.origin_n) // step_n + 1
        self.next_end_n = self.origin_n + steps_in * step_n
        return audio

    def _windows(self, audio: np.ndarray, model: ModelFn) -> list[_Window]:
        """Run the model over every step the new audio completes."""
        config = self.config
        buffer = np.concatenate([self.audio, audio.astype(np.float32)])
        buffer_end_n = self.audio_end_n + len(audio)
        ends: list[int] = []
        while self.next_end_n <= buffer_end_n:
            ends.append(self.next_end_n)
            self.next_end_n += config.step_n
        self.audio_end_n = buffer_end_n
        self.audio = buffer[-config.window_n :].copy()
        if not ends:
            return []
        assert self.origin_n is not None
        offset = buffer_end_n - len(buffer)
        windows = np.stack(
            [buffer[end - offset - config.window_n : end - offset] for end in ends]
        )
        # Bounded batches: a lane that fell behind hands over many steps at once.
        outputs = [
            model(windows[i : i + config.model_batch])
            for i in range(0, len(windows), config.model_batch)
        ]
        segmentation = np.concatenate([seg for seg, _ in outputs])
        embeddings = np.concatenate([emb for _, emb in outputs])
        return [
            _Window(
                end_n=end,
                step=(end - self.origin_n) // config.step_n - 1,
                segmentation=segmentation[i].astype(np.float32),
                embeddings=embeddings[i],
            )
            for i, end in enumerate(ends)
        ]

    def feed(self, audio: np.ndarray, start_n: int, model: ModelFn) -> FeedResult:
        """Consume mono audio that starts at sample ``start_n`` of the recording."""
        if self.origin_n is None:
            self._start(start_n)
        audio = self._align_to(start_n, audio)
        result = FeedResult()
        assert self.origin_n is not None
        for window in self._windows(audio, model):
            self._step(window, result)
            if (window.end_n - self.origin_n) % self.config.packet_n == 0:
                self._maybe_recluster(window.end_n / self.config.sample_rate, result)
        return result

    def _maybe_recluster(self, t_now: float, result: FeedResult) -> None:
        if t_now < self.next_recluster_s:
            return
        result.reclusters.append(
            recluster(
                self.units, self.bank, self.clustering, self.config.recluster, t_now
            )
        )
        self.next_recluster_s += self.config.recluster.every_s

    def _step(self, window: _Window, result: FeedResult) -> None:
        config = self.config
        assignment = self.clustering.identify(window.segmentation, window.embeddings)
        unit_of_slot = self.units.observe(
            StepObservation(
                pairs=assignment.all_pairs,
                embeddings=window.embeddings,
                segmentation=window.segmentation,
                step=window.step,
                t_now=window.end_n / config.sample_rate,
                rho_update=config.clustering.rho_update,
                max_steps=config.recluster.max_steps,
            ),
            self.bank,
        )
        first_new = len(self.pieces)
        self._emit(window, assignment, unit_of_slot)
        result.backfilled += self._backfill(window, assignment, unit_of_slot, first_new)
        result.steps += 1
        result.founded += assignment.founded

    def _frame_times(self, window: _Window) -> tuple[np.ndarray, float]:
        frames = window.segmentation.shape[0]
        resolution = self.config.window_s / frames
        start_s = window.end_n / self.config.sample_rate - self.config.window_s
        return start_s + (np.arange(frames) + 0.5) * resolution, resolution

    def _emit(
        self, window: _Window, assignment: StepAssignment, unit_of_slot: dict[int, int]
    ) -> None:
        """Turn the last ``step_s`` of the window into pieces, per local speaker."""
        config = self.config
        middles, resolution = self._frame_times(window)
        end_s = window.end_n / config.sample_rate
        selected = (middles >= end_s - config.step_s) & (middles < end_s)
        times = middles[selected]
        tau = config.clustering.tau_active
        for local, slot in assignment.all_pairs:
            active = window.segmentation[selected, local] > tau
            i = 0
            while i < len(active):
                if not active[i]:
                    i += 1
                    continue
                j = i
                while j < len(active) and active[j]:
                    j += 1
                self.pieces.append(
                    Piece(
                        start=round(float(times[i] - resolution / 2), 3),
                        end=round(float(times[j - 1] + resolution / 2), 3),
                        unit=unit_of_slot[slot],
                        open=slot == self.held_slot,
                    )
                )
                i = j

    def _backfill(
        self,
        window: _Window,
        assignment: StepAssignment,
        unit_of_slot: dict[int, int],
        first_new: int,
    ) -> int:
        """Label earlier held pieces that a confident local speaker now covers."""
        middles, _ = self._frame_times(window)
        window_start = window.end_n / self.config.sample_rate - self.config.window_s
        tau = self.config.clustering.tau_active
        confident = [
            (local, slot)
            for local, slot in assignment.all_pairs
            if slot != self.held_slot
        ]
        filled = 0
        for piece in self.pieces[:first_new]:
            if not piece.open:
                continue
            if piece.start < window_start - 1e-6:
                piece.open = False  # left the window: the next re-cluster labels it
                continue
            frames = (middles >= piece.start) & (middles < piece.end)
            best_slot, best_cover = None, -1.0
            for local, slot in confident:
                cover = (
                    float((window.segmentation[frames, local] > tau).mean())
                    if frames.any()
                    else 0.0
                )
                if cover >= self.config.backfill_cover and cover > best_cover:
                    best_slot, best_cover = slot, cover
            if best_slot is not None:
                piece.unit = unit_of_slot[best_slot]
                piece.open = False
                filled += 1
        return filled

    def prune_pieces(self, before_s: float) -> None:
        """Drop pieces that end before ``before_s``; no line can need them."""
        self.pieces = [piece for piece in self.pieces if piece.end >= before_s]
