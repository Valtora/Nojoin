"""Record the last drained normalised source position without owning its clock."""

from __future__ import annotations

import wave
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .hybrid import HybridLiveState
    from .step import LiveLabelRun


def update_source_cursor(engine: HybridLiveState, run: LiveLabelRun) -> None:
    if run.sequence < engine.source_sequence:
        return
    engine.source_sequence = run.sequence
    path = run.live_dir.parent / f"{run.sequence}.wav"
    # Pure engine/replay callers need no source files. Production capture WAVs
    # remain beside live/ until ordinary capture finalisation removes them.
    engine.source_sample_offset = 0
    if path.exists():
        with wave.open(str(path), "rb") as source:
            engine.source_sample_offset = (
                source.getnframes() * engine.config.sample_rate
                + source.getframerate()
                - 1
            ) // source.getframerate()
