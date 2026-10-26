"""The label step of one live run: audio in, labels on the run's lines out.

The live task calls ``run_live_speaker_labels`` after its text and lane state
are saved, inside a try block of its own, so nothing here can fail the run or
delay its text. The step feeds the engines on their own sample clock and saves
decision state before writing labels, then saves settled/pruned progress.

``live_speaker_labels`` (``data/config.json``) switches it: ``off`` (the
default) or ``auto``, which runs it when the live lane's device is CUDA. Any
other value is off. Off returns before any model or state is touched.
"""

from __future__ import annotations

import fcntl
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.processing.pipeline_metrics import record_pipeline_metric
from backend.utils.canonical_pipeline.live_labels import (
    LiveLabelDecision,
    LiveLabelWrite,
    apply_live_speaker_labels,
)
from backend.utils.config_manager import config_manager

from .engine import FeedResult, LiveLabelConfig, LiveLabelEngine, ModelFn
from .files import (
    BANK_FILENAME,
    DISABLED_FILENAME,
    LOCK_FILENAME,
    remove_live_label_state,
)
from .hybrid import HybridLiveState
from .hybrid_execution import feed_capture
from .hybrid_runtime import bind_capture, has_label_history, start_capture
from .hybrid_source import update_source_cursor
from .hybrid_state import load_hybrid, save_hybrid
from .lines import LabelTimeline
from .setting import live_speaker_labels_requested
from .state import LiveLabelStateError, save_engine

logger = logging.getLogger(__name__)

# Pieces this far before the earliest line that can still change are kept.
PRUNE_MARGIN_S = 1.0
CONFIG = LiveLabelConfig()
# How long a run waits for the recording's previous label step. The step runs
# after the lane state is saved, so with two GPU lanes the next run's step can
# start before this one ends; both would load, feed and save the same state.
LOCK_WAIT_S = 10.0
LOCK_POLL_S = 0.05


def live_speaker_labels_device() -> str | None:
    """The device live labels run on, or None when they are off.

    ``auto`` means on when the live lane runs on CUDA: the model pass costs
    about a third of real time on 16 CPU threads, which the lane cannot spare.
    """
    if not live_speaker_labels_requested():
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


def _recording_in_flight(session, recording_id: int, *, lock: bool = False) -> bool:
    from backend.models.recording import Recording, RecordingStatus

    if lock:
        # FOR NO KEY UPDATE: blocks a status change, not an insert that
        # references the recording (an audio chunk, an utterance event).
        recording = session.get(
            Recording, recording_id, with_for_update={"key_share": True}
        )
    else:
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


def _write_labels(
    recording_id: int, engine: LiveLabelEngine | HybridLiveState
) -> LiveLabelWrite | None:
    """Write the labels, or return None once the capture has ended."""
    from backend.core.db import get_sync_session

    timeline = (
        engine
        if isinstance(engine, HybridLiveState)
        else LabelTimeline.from_engine(engine)
    )

    def decide(start_ms: int, end_ms: int) -> LiveLabelDecision:
        return timeline.label_line(start_ms / 1000.0, end_ms / 1000.0)

    session = get_sync_session()
    try:
        # Locked until the labels commit: finalize's status change waits, so no
        # label lands after the capture has ended.
        if not _recording_in_flight(session, recording_id, lock=True):
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
    run: LiveLabelRun,
    engine: LiveLabelEngine | HybridLiveState,
    write: LiveLabelWrite | None,
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
    if isinstance(engine, HybridLiveState):
        save_hybrid(run.live_dir, engine)
    else:
        save_engine(run.live_dir, engine)


def _load_or_disable(run: LiveLabelRun, device: str) -> HybridLiveState | None:
    try:
        engine = load_hybrid(run.live_dir, CONFIG, recording_id=run.recording_id)
        if engine is None and (
            (run.live_dir / BANK_FILENAME).exists()
            or has_label_history(run.recording_id)
        ):
            raise LiveLabelStateError("Missing checkpoint for existing live identities")
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
    if engine is None:
        engine = start_capture(
            run.live_dir, CONFIG, device=device, recording_id=run.recording_id
        )
    else:
        bind_capture(engine, device=device)
    return engine


def _record(
    run: LiveLabelRun,
    engine: HybridLiveState,
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
            "route": engine.route,
            "reason": engine.reason,
            "switch_emitted_at_n": engine.switch_emitted_at_n,
        },
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
        log=logger,
    )


@contextmanager
def _recording_lock(live_dir: Path) -> Iterator[bool]:
    """Hold the recording's label lock; yields False if it stays taken too long."""
    with open(live_dir / LOCK_FILENAME, "a") as handle:
        deadline = time.monotonic() + LOCK_WAIT_S
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(LOCK_POLL_S)
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def run_live_speaker_labels(run: LiveLabelRun) -> None:
    """Label the run's lines. Raises on failure; the live task catches it."""
    device = live_speaker_labels_device()
    if device is None or (run.live_dir / DISABLED_FILENAME).exists():
        return
    if not _still_capturing(run.recording_id):
        # Queued behind finalize: no model pass, and no lock file made; any
        # state still here goes.
        remove_live_label_state(run.live_dir)
        return
    with _recording_lock(run.live_dir) as held:
        if held:
            _label_run(run, device)
            return
    # This run's audio is not labelled; the next run bridges the gap.
    record_pipeline_metric(
        stage="live_speaker_labels",
        recording_id=run.recording_id,
        payload={"sequence": run.sequence, "reason": "busy"},
        status="skipped",
        log=logger,
    )


def _label_run(run: LiveLabelRun, device: str) -> None:
    started = time.perf_counter()
    engine = _load_or_disable(run, device)
    if engine is None:
        return
    ended = False
    consumed_saved = False
    feed: FeedResult | None = None
    write: LiveLabelWrite | None = None
    try:
        # Persist the capture-frozen selection even if loading the shadow fails.
        if engine.source_sequence < 0:
            save_hybrid(run.live_dir, engine)
        engine, feed = feed_capture(run, engine, lambda: _load_model(device, CONFIG))
        update_source_cursor(engine, run)
        # All IDs and decisions must exist on disk before a DB callback can lock
        # a line. A crash before the DB commit replays this retained evidence.
        save_hybrid(run.live_dir, engine)
        consumed_saved = True
        write = _write_labels(run.recording_id, engine)
        ended = write is None
    except (FileNotFoundError, ValueError):
        if _still_capturing(run.recording_id):
            raise
        ended = True
    finally:
        try:
            # Saved even when the write failed: the audio is consumed, and the
            # next run labels these lines from the saved engine.
            ended = ended or not _still_capturing(run.recording_id)
            if not ended and consumed_saved:
                _save(run, engine, write)
        except (FileNotFoundError, ValueError):
            # Finalize removed the files under a stalled save: once the capture
            # has ended, that is not a failure to report.
            if _still_capturing(run.recording_id):
                raise
            ended = True
        finally:
            # The capture can end at any point up to here (finalize, or a
            # finalize whose master cannot be built), removing the state, and
            # no live run comes after it. Checked after the save, so this run
            # removes what it saved; a capture ending later is finalize's.
            ended = ended or not _still_capturing(run.recording_id)
            if ended:
                remove_live_label_state(run.live_dir)
    if write is not None and feed is not None and not ended:
        _record(run, engine, feed, write, started)
