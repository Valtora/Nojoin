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

from dataclasses import dataclass

import numpy as np

from backend.utils.canonical_pipeline.live_labels import LiveLabelDecision
from backend.utils.live_speaker_labels import live_speaker_label

from .engine import LiveLabelEngine

UNKNOWN_SPEAKER = "UNKNOWN"
# Share of a line's diarised speech the winning label must exceed.
LINE_MAJORITY = 0.5


@dataclass(frozen=True)
class LabelTimeline:
    """Every retained piece with its current label (-1 while held) and lock."""

    start: np.ndarray
    end: np.ndarray
    label: np.ndarray
    locked: np.ndarray
    complete_until_s: float

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
        overlap = np.minimum(self.end, end_s) - np.maximum(self.start, start_s)
        inside = overlap > 0
        if not inside.any():
            return LiveLabelDecision(UNKNOWN_SPEAKER, locked=complete, share=None)
        seconds = overlap[inside]
        labels = self.label[inside]
        labelled = labels >= 0
        locked = complete and bool(self.locked[inside].all())
        if not labelled.any():
            return LiveLabelDecision(UNKNOWN_SPEAKER, locked=locked, share=0.0)
        per_label = np.bincount(labels[labelled], weights=seconds[labelled])
        winner = int(np.argmax(per_label))
        share = float(per_label[winner] / seconds.sum())
        speaker = (
            # Slot 0 is LIVE_PROVISIONAL_1, shown as "Speaker 1".
            live_speaker_label(winner + 1) if share > LINE_MAJORITY else UNKNOWN_SPEAKER
        )
        return LiveLabelDecision(speaker, locked=locked, share=round(share, 3))
