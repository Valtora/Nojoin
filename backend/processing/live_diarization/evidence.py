"""Read saved diariser evidence without loading models or changing its bank."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zipfile import BadZipFile

import numpy as np

from backend.utils.canonical_pipeline.live_labels import LiveLabelDecision
from backend.utils.live_speaker_labels import live_speaker_label

from .engine import LiveLabelConfig
from .files import DISABLED_FILENAME
from .lines import LabelTimeline
from .state import LiveLabelStateError, _validate, read_snapshot


@dataclass(frozen=True)
class SpeakerEvidence:
    """Owned arrays from one saved label state."""

    timeline: LabelTimeline
    posteriors: np.ndarray
    posterior_first_frame: int
    native_complete_frame: int
    native: bool
    switch_frame: int | None

    def decide(self, start_ms: int, end_ms: int) -> LiveLabelDecision:
        """Resolve a word against the same route as normal live labels."""
        if not self.native and not (
            self.switch_frame is not None and end_ms <= self.native_complete_frame * 10
        ):
            return self.timeline.label_line(start_ms / 1000, end_ms / 1000)
        first = max(0, math.floor(start_ms / 10) - self.posterior_first_frame)
        stop = max(0, math.ceil(end_ms / 10) - self.posterior_first_frame)
        probabilities = self.posteriors[first:stop]
        complete = end_ms <= self.native_complete_frame * 10
        if not complete:
            return LiveLabelDecision("UNKNOWN", False, None, True)
        if not len(probabilities):
            return LiveLabelDecision("UNKNOWN", complete, None)
        totals = probabilities.sum(axis=0, dtype=np.float32)
        total = float(totals.sum())
        if not total:
            return LiveLabelDecision("UNKNOWN", complete, None)
        winner = int(np.argmax(totals))
        return LiveLabelDecision(
            live_speaker_label(winner + 1), complete, float(totals[winner] / total)
        )

    def speech_spans(self) -> list[tuple[float, float]]:
        """Return observed speech intervals, including held classic speech."""
        spans = list(zip(self.timeline.start.tolist(), self.timeline.end.tolist()))
        if self.native:
            frames = np.flatnonzero(np.any(self.posteriors >= 0.25, axis=1))
            spans.extend(
                (
                    (int(frame) + self.posterior_first_frame) / 100,
                    (int(frame) + self.posterior_first_frame + 1) / 100,
                )
                for frame in frames
            )
        return spans


def _from_snapshot(meta: dict[str, Any], arrays: dict[str, Any]) -> SpeakerEvidence:
    hybrid = meta.get("hybrid") or {}
    units = arrays["piece_unit"]
    labels = arrays["unit_label"][units]
    held_slot = len(arrays["active"]) - 1
    translation = dict(hybrid.get("translation", []))
    labels = np.array(
        [translation.get(int(label), int(label)) for label in labels], np.int64
    )
    timeline = LabelTimeline(
        start=arrays["piece_start"],
        end=arrays["piece_end"],
        label=np.where(arrays["unit_label"][units] == held_slot, -1, labels),
        locked=arrays["unit_locked"][units],
        complete_until_s=(int(meta["next_end_n"]) - LiveLabelConfig().step_n) / 16000,
    )
    return SpeakerEvidence(
        timeline,
        arrays.get("hybrid_posteriors", np.zeros((0, 8), np.float32)),
        int(hybrid.get("posterior_first_frame", 0)),
        int(hybrid.get("native_complete_frame", 0)),
        hybrid.get("route") == "nemotron",
        hybrid.get("switch_emitted_at_n"),
    )


def available_speaker_evidence(
    live_dir: Path, *, recording_id: int
) -> SpeakerEvidence | None:
    """Read the saved label state; missing or invalid evidence stays absent.

    The live task runs its label step after this read, under the same drain
    lock, so the state read here is the last one that step saved. It reads the
    envelope only: constructing a UnitBank could truncate the bank.
    """
    if (live_dir / DISABLED_FILENAME).exists():
        return None
    try:
        snapshot = read_snapshot(live_dir, LiveLabelConfig())
        if snapshot is None:
            return None
        meta, arrays = snapshot
        if (meta.get("hybrid") or {}).get("recording_id", recording_id) != recording_id:
            return None
        _validate(live_dir, LiveLabelConfig(), meta, arrays)
        if meta.get("hybrid") is not None:
            from .hybrid_state import _validate_identity

            _validate_identity(meta["hybrid"], arrays, recording_id)
        return _from_snapshot(meta, arrays)
    except (
        OSError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        BadZipFile,
        LiveLabelStateError,
    ):
        # Evidence is optional; text never waits for a diariser or state repair.
        return None
