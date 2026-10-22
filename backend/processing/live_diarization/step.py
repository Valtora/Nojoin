"""The label step of one live run: audio in, labels on the run's lines out.

The live task calls ``run_live_speaker_labels`` after its text and lane state
are saved, inside a try block of its own, so nothing here can fail the run or
delay its text. The step loads the engine, feeds it the run's new audio, writes
the labels in a second transaction, and saves the engine.

``live_speaker_labels`` (``data/config.json``) switches it: ``off`` (the
default) or ``auto``, which runs it when the live lane's device is CUDA. Any
other value is off. Off returns before any model or state is touched.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from backend.processing.pipeline_metrics import record_pipeline_metric
from backend.utils.canonical_pipeline.live_labels import (
    LiveLabelDecision,
    LiveLabelWrite,
    apply_live_speaker_labels,
)
from backend.utils.config_manager import config_manager

from .engine import FeedResult, LiveLabelConfig, LiveLabelEngine, ModelFn
from .files import DISABLED_FILENAME, remove_live_label_state
from .lines import LabelTimeline
from .state import LiveLabelStateError, load_engine, new_engine, save_engine

logger = logging.getLogger(__name__)

LIVE_SPEAKER_LABELS_KEY = "live_speaker_labels"
LIVE_SPEAKER_LABELS_AUTO = "auto"
# Pieces this far before the earliest line that can still change are kept.
PRUNE_MARGIN_S = 1.0
CONFIG = LiveLabelConfig()


def live_speaker_labels_device() -> str | None:
    """The device live labels run on, or None when they are off.

    ``auto`` means on when the live lane runs on CUDA: the model pass costs
    about a third of real time on 16 CPU threads, which the lane cannot spare.
    """
    mode = str(config_manager.get(LIVE_SPEAKER_LABELS_KEY, "off") or "off")
    if mode.strip().lower() != LIVE_SPEAKER_LABELS_AUTO:
        return None
    import torch

    device = str(config_manager.get("processing_device", "auto") or "auto").lower()
    if not torch.cuda.is_available():
        return None
    if device == "auto":
        return "cuda"
    return device if device.startswith("cuda") else None


@dataclass(frozen=True)
class LiveLabelRun:
    """One live run, as the label step sees it.

    ``audio`` is the run's combined mono clip (carried buffer and drained
    chunks) starting at ``audio_start_s`` on the recording timeline. The
    engine skips the part it has already seen. ``next_text_start_s`` is where
    the next run's text can start; no line will ever need a piece before it.
    """

    recording_id: int
    sequence: int
    live_dir: Path
    audio: Any
    audio_start_s: float
    next_text_start_s: float


def _load_model(device: str, config: LiveLabelConfig) -> ModelFn:
    from .models import LiveLabelModel

    return LiveLabelModel(device, config)


def _mono_samples(audio: Any) -> np.ndarray:
    samples = audio.numpy() if hasattr(audio, "numpy") else audio
    return np.asarray(samples, dtype=np.float32).reshape(-1)


def _recording_in_flight(session, recording_id: int) -> bool:
    from backend.models.recording import Recording, RecordingStatus

    recording = session.get(Recording, recording_id)
    return recording is not None and recording.status in {
        RecordingStatus.UPLOADING,
        RecordingStatus.PAUSED,
    }


def _still_capturing(recording_id: int) -> bool:
    from backend.core.db import get_sync_session

    session = get_sync_session()
    try:
        return _recording_in_flight(session, recording_id)
    finally:
        session.close()


def _write_labels(recording_id: int, engine: LiveLabelEngine) -> LiveLabelWrite | None:
    """Write the labels, or return None once the capture has ended."""
    from backend.core.db import get_sync_session

    timeline = LabelTimeline.from_engine(engine)

    def decide(start_ms: int, end_ms: int) -> LiveLabelDecision:
        return timeline.label_line(start_ms / 1000.0, end_ms / 1000.0)

    session = get_sync_session()
    try:
        if not _recording_in_flight(session, recording_id):
            return None
        write = apply_live_speaker_labels(
            session,
            recording_id=recording_id,
            settled_ms=engine.settled_ms,
            decide=decide,
        )
        session.commit()
        return write
    finally:
        session.close()


def _save(
    run: LiveLabelRun, engine: LiveLabelEngine, write: LiveLabelWrite | None
) -> None:
    """Save the engine; after a write, also advance the settled lines and prune."""
    if write is not None:
        engine.settled_ms = write.settled_ms
        open_from_s = (
            run.next_text_start_s
            if write.open_from_s is None
            else min(write.open_from_s, run.next_text_start_s)
        )
        engine.prune_pieces(open_from_s - PRUNE_MARGIN_S)
    save_engine(run.live_dir, engine)


def _load_or_disable(run: LiveLabelRun) -> LiveLabelEngine | None:
    try:
        engine = load_engine(run.live_dir, CONFIG)
    except LiveLabelStateError as exc:
        # Starting over would hand earlier speakers' numbers to new voices, so
        # labels stop for this recording; its text is unaffected.
        (run.live_dir / DISABLED_FILENAME).touch()
        record_pipeline_metric(
            stage="live_speaker_labels",
            recording_id=run.recording_id,
            payload={
                "sequence": run.sequence,
                "reason": "state_unusable",
                "error": str(exc),
            },
            status="error",
            log=logger,
        )
        return None
    return engine if engine is not None else new_engine(run.live_dir, CONFIG)


def _record(
    run: LiveLabelRun,
    engine: LiveLabelEngine,
    feed: FeedResult,
    write: LiveLabelWrite,
    started: float,
) -> None:
    record_pipeline_metric(
        stage="live_speaker_labels",
        recording_id=run.recording_id,
        payload={
            "sequence": run.sequence,
            "steps": feed.steps,
            "founded": feed.founded,
            "backfilled": feed.backfilled,
            "reclusters": len(feed.reclusters),
            "relabelled_units": sum(r.relabelled for r in feed.reclusters),
            "lines_changed": write.changed,
            "speakers": int(engine.clustering.active.sum()),
            "units": engine.units.count,
        },
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
        log=logger,
    )


def run_live_speaker_labels(run: LiveLabelRun) -> None:
    """Label the run's lines. Raises on failure; the live task catches it."""
    device = live_speaker_labels_device()
    if device is None or (run.live_dir / DISABLED_FILENAME).exists():
        return
    started = time.perf_counter()
    engine = _load_or_disable(run)
    if engine is None:
        return
    feed = engine.feed(
        _mono_samples(run.audio),
        int(round(run.audio_start_s * CONFIG.sample_rate)),
        _load_model(device, CONFIG),
    )

    ended = False
    write: LiveLabelWrite | None = None
    try:
        write = _write_labels(run.recording_id, engine)
        ended = write is None
    finally:
        # Saved even when the write failed: the audio is consumed, and the
        # next run labels these lines from the saved engine.
        if ended:
            remove_live_label_state(run.live_dir)
        else:
            _save(run, engine, write)
    if write is None:
        return
    # Finalize can land between the write and the save; it removes the state,
    # and no live run comes after it, so this run removes what it just saved.
    if not _still_capturing(run.recording_id):
        remove_live_label_state(run.live_dir)
        return
    _record(run, engine, feed, write, started)
