"""From labelled pieces of speech to one speaker per live transcript line.

A line takes the label that holds a strict majority of the diarised speech
inside it. Held speech counts in the total but for no label, so a line that is
mostly held, or split between two voices, stays unlabelled. A line's label is
locked once every piece inside it is in a locked unit and the engine has
emitted every piece the line can have; it never changes after that. Held
speech locks too, when a re-cluster with every slot taken finds it no label
and its unit ages out, so such a line settles, unlabelled unless the rest of
its speech holds the majority.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from backend.utils.canonical_pipeline.live_labels import LiveLabelDecision
from backend.utils.live_speaker_labels import live_speaker_label
from backend.utils.time_units import seconds_to_ms

from .engine import LiveLabelEngine

UNKNOWN_SPEAKER = "UNKNOWN"


@dataclass(frozen=True)
class LabelTimeline:
    """Every retained piece with its current label (-1 while held) and lock."""

    start: np.ndarray
    end: np.ndarray
    label: np.ndarray
    locked: np.ndarray
    complete_until_s: float
    start_ms: np.ndarray = field(init=False, repr=False, compare=False)
    end_ms: np.ndarray = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # Pieces end on whole milliseconds, so a line's shares are compared in
        # integers: an even split is exactly even.
        for name, seconds in (("start_ms", self.start), ("end_ms", self.end)):
            object.__setattr__(
                self, name, np.array([seconds_to_ms(v) for v in seconds], np.int64)
            )

    @classmethod
    def from_engine(cls, engine: LiveLabelEngine) -> LabelTimeline:
        units = engine.units
        unit_label = np.array(units.label, np.int64)
        unit_locked = np.array(units.locked, bool)
        piece_unit = np.array([piece.unit for piece in engine.pieces], np.int64)
        labels = unit_label[piece_unit] if len(piece_unit) else piece_unit
        return cls(
            start=np.array([piece.start for piece in engine.pieces], np.float64),
            end=np.array([piece.end for piece in engine.pieces], np.float64),
            label=np.where(labels == engine.held_slot, -1, labels),
            locked=unit_locked[piece_unit] if len(piece_unit) else piece_unit > 0,
            complete_until_s=engine.processed_until_s,
        )

    def label_line(self, start_s: float, end_s: float) -> LiveLabelDecision:
        complete = end_s <= self.complete_until_s
        if not complete:
            return LiveLabelDecision(UNKNOWN_SPEAKER, False, None, True)
        line_start, line_end = seconds_to_ms(start_s), seconds_to_ms(end_s)
        overlap = np.minimum(self.end_ms, line_end) - np.maximum(
            self.start_ms, line_start
        )
        inside = overlap > 0
        if not inside.any():
            return LiveLabelDecision(UNKNOWN_SPEAKER, locked=complete, share=None)
        durations = overlap[inside]
        labels = self.label[inside]
        labelled = labels >= 0
        locked = complete and bool(self.locked[inside].all())
        if not labelled.any():
            return LiveLabelDecision(UNKNOWN_SPEAKER, locked=locked, share=0.0)
        per_label = np.zeros(int(labels.max()) + 1, np.int64)
        np.add.at(per_label, labels[labelled], durations[labelled])
        winner = int(np.argmax(per_label))
        held_by_winner, total = int(per_label[winner]), int(durations.sum())
        speaker = (
            # A strict majority; slot 0 is LIVE_PROVISIONAL_1, "Speaker 1".
            live_speaker_label(winner + 1)
            if 2 * held_by_winner > total
            else UNKNOWN_SPEAKER
        )
        return LiveLabelDecision(
            speaker, locked=locked, share=round(held_by_winner / total, 3)
        )
