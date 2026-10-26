"""Recover shadow allocation failures without retrying native or damaged cursors."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np

from .engine import FeedResult, ModelFn
from .hybrid import HybridLiveState, failure_reason
from .hybrid_runtime import release_native
from .hybrid_state import load_hybrid, save_hybrid
from .state import LiveLabelStateError

if TYPE_CHECKING:
    from .step import LiveLabelRun


def _load_shadow(
    run: LiveLabelRun, engine: HybridLiveState, loader: Callable[[], ModelFn]
) -> ModelFn:
    try:
        return loader()
    except (RuntimeError, MemoryError) as exc:
        if not engine.asset_fingerprint or failure_reason(exc) != "oom":
            raise
    # Native adds memory pressure. Latch and persist its removal before a single
    # retry, even if the shadow loader fails again. Leave exception scope first.
    engine.fallback("oom", engine.consumed_n)
    release_native()
    save_hybrid(run.live_dir, engine)
    return loader()


def feed_capture(
    run: LiveLabelRun, engine: HybridLiveState, loader: Callable[[], ModelFn]
) -> tuple[HybridLiveState, FeedResult]:
    """A failed shadow forward may have moved cursors; restore before retrying."""
    model = _load_shadow(run, engine, loader)
    samples = run.audio.numpy() if hasattr(run.audio, "numpy") else run.audio
    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    start_n = int(round(run.audio_start_s * engine.config.sample_rate))
    try:
        return engine, engine.feed(audio, start_n, model)
    except (RuntimeError, MemoryError) as exc:
        if not engine.asset_fingerprint or failure_reason(exc) != "oom":
            raise
    # Discard the mutated stream and shadow, then restore only durable accepted
    # evidence. The bank codec truncates any uncommitted append rows.
    engine.stream = None
    restored = load_hybrid(run.live_dir, engine.config, recording_id=run.recording_id)
    if restored is None:
        release_native()
        raise LiveLabelStateError("Missing checkpoint after shadow allocation failure")
    restored.fallback("oom", restored.consumed_n)
    release_native()
    save_hybrid(run.live_dir, restored)
    return restored, restored.feed(audio, start_n, loader())
