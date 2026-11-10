"""Read-only native argmax and translated shadow line decisions."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING

import numpy as np

from backend.utils.canonical_pipeline.live_labels import LiveLabelDecision
from backend.utils.live_speaker_labels import live_speaker_label
from backend.utils.time_units import NATIVE_FRAME_MS, frame_range, seconds_to_ms

from .lines import LabelTimeline

if TYPE_CHECKING:
    from .hybrid import HybridLiveState


def label_line(
    engine: HybridLiveState,
    start_s: float,
    end_s: float,
    *,
    shadow_timeline: LabelTimeline | None = None,
) -> LiveLabelDecision:
    if engine.route == "nemotron" or (
        engine.switch_emitted_at_n is not None
        and seconds_to_ms(end_s) <= engine.native_complete_frame * NATIVE_FRAME_MS
    ):
        return native_line(engine, start_s, end_s)
    timeline = (
        shadow_timeline
        if shadow_timeline is not None
        else LabelTimeline.from_engine(engine.shadow)
    )
    decision = timeline.label_line(start_s, end_s)
    if not decision.speaker.startswith("LIVE_PROVISIONAL_"):
        return decision
    slot = int(decision.speaker.removeprefix("LIVE_PROVISIONAL_")) - 1
    display = engine.translation.get(slot)
    return LiveLabelDecision(
        live_speaker_label(display + 1) if display is not None else "UNKNOWN",
        locked=decision.locked,
        share=decision.share,
        diarisation_pending=decision.diarisation_pending,
    )


def prepare_label_line(
    engine: HybridLiveState,
) -> Callable[[float, float], LiveLabelDecision]:
    """Prepare retained shadow evidence once for one synchronous publication."""
    if engine.route == "nemotron":
        return partial(native_line, engine)
    timeline = LabelTimeline.from_engine(engine.shadow)
    if engine.switch_emitted_at_n is None and all(
        slot == display for slot, display in engine.translation.items()
    ):
        # Allocation and restored-state validation cover every shadow label.
        # Classic captures keep the original slot namespace and callback cost.
        return timeline.label_line
    return partial(label_line, engine, shadow_timeline=timeline)


def native_line(
    engine: HybridLiveState, start_s: float, end_s: float
) -> LiveLabelDecision:
    start_ms, end_ms = seconds_to_ms(start_s), seconds_to_ms(end_s)
    first, stop = frame_range(
        start_ms, end_ms, NATIVE_FRAME_MS, engine.posterior_first_frame
    )
    probabilities = engine.posteriors[first:stop]
    complete = end_ms <= engine.native_complete_frame * NATIVE_FRAME_MS
    if not complete:
        return LiveLabelDecision("UNKNOWN", False, None, True)
    if not len(probabilities):
        return LiveLabelDecision("UNKNOWN", locked=complete, share=None)
    totals = probabilities.sum(axis=0, dtype=np.float32)
    winner = int(np.argmax(totals))
    total = float(totals.sum())
    if not total:
        return LiveLabelDecision("UNKNOWN", locked=complete, share=None)
    return LiveLabelDecision(
        live_speaker_label(winner + 1),
        locked=complete,
        share=round(float(totals[winner]) / total, 3) if total else None,
    )
