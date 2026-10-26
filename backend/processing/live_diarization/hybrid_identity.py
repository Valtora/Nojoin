"""Permanent display identities over the classic engine's recyclable slots."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .hybrid import HybridLiveState


def allocate_labels(engine: HybridLiveState) -> None:
    """Keep a slot's display only while a unit witnesses its cluster continuity.

    Classic retires a slot when reclustering moves every unit off it. A later
    founding can reuse that number. Checking immutable unit IDs also detects a
    retirement followed by reuse inside one feed (for example after an audio
    gap); sampling only the active-slot mask would miss that transition.

    Reclustering retains a numeric name only with positive overlap from its
    previous units. Locked units never move, so their displays remain stable.
    No acoustic state, labels or allocation policy is changed here.
    """
    labels = np.asarray(engine.units.label, np.int64)
    if engine.switch_emitted_at_n is not None:
        previous = engine.display_labels
        retained = labels[: len(previous)]
        continuing = set(retained[retained == previous].tolist())
        engine.translation = {
            slot: display
            for slot, display in engine.translation.items()
            if slot in continuing
        }
    for slot in sorted(set(labels.tolist()) - {engine.shadow.held_slot}):
        if slot not in engine.translation:
            engine.translation[slot] = (
                slot if engine.switch_emitted_at_n is None else engine._fresh_id()
            )
            engine.next_display_id = max(
                engine.next_display_id, engine.translation[slot] + 1
            )
    engine.display_labels = labels.copy()
