"""Durable label evidence selected by a fenced database acceptance record.

The recording's label flock covers preparation, acceptance and recovery. New
bank rows are an unaccepted suffix until the database selects their snapshot;
loading the selected snapshot truncates any suffix left by a rejected run.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlmodel import select

from backend.models.pipeline import ProcessingRun, ProcessingRunKind
from backend.utils.canonical_pipeline.queries import ensure_processing_run

from .files import CHECKPOINT_PREFIX, STATE_FILENAME, STATE_TMP_FILENAME
from .hybrid_state import load_hybrid
from .state import LiveLabelStateError

if TYPE_CHECKING:
    from .engine import LiveLabelConfig
    from .hybrid import HybridLiveState

CHECKPOINT_KEY = "live-label-checkpoint"


def new_checkpoint_filename() -> str:
    """Name one immutable prepared snapshot, never the recovery mirror."""
    return f"{CHECKPOINT_PREFIX}{uuid4().hex}.npz"


def accept_checkpoint(session: Any, recording_id: int, filename: str) -> None:
    """Select already durable evidence; the caller owns Transcript and commits."""
    run = ensure_processing_run(
        session,
        recording_id=recording_id,
        run_kind=ProcessingRunKind.ROLLING_DIARIZATION,
        trigger_source="live_label_checkpoint",
        idempotency_key=CHECKPOINT_KEY,
    )
    run.model_metadata = {"checkpoint": filename}
    session.add(run)


def load_checkpoint(
    live_dir: Path, config: LiveLabelConfig, *, recording_id: int
) -> HybridLiveState | None:
    """Resolve acceptance before file I/O, including after an epoch takeover."""
    from backend.core.db import get_sync_session

    with get_sync_session() as session:
        run = session.execute(
            select(ProcessingRun)
            .where(ProcessingRun.recording_id == recording_id)
            .where(ProcessingRun.idempotency_key == CHECKPOINT_KEY)
        ).scalar_one_or_none()
        filename = (run.model_metadata or {}).get("checkpoint") if run else None
    if filename is None:
        return load_hybrid(live_dir, config, recording_id=recording_id)
    if not isinstance(filename, str) or not filename.startswith(CHECKPOINT_PREFIX):
        raise LiveLabelStateError("Invalid accepted label checkpoint")
    identifier = filename.removeprefix(CHECKPOINT_PREFIX).removesuffix(".npz")
    try:
        UUID(hex=identifier)
    except ValueError as exc:
        raise LiveLabelStateError("Invalid accepted label checkpoint") from exc
    engine = load_hybrid(live_dir, config, recording_id=recording_id, filename=filename)
    if engine is None:
        raise LiveLabelStateError("Missing accepted label checkpoint")
    return engine


def mirror_checkpoint(live_dir: Path, filename: str) -> None:
    """Refresh the compatibility mirror and prune old evidence outside the fence."""
    temporary = live_dir / STATE_TMP_FILENAME
    temporary.unlink(missing_ok=True)
    os.link(live_dir / filename, temporary)
    os.replace(temporary, live_dir / STATE_FILENAME)
    for candidate in live_dir.glob(f"{CHECKPOINT_PREFIX}*"):
        if candidate.name != filename:
            candidate.unlink(missing_ok=True)
