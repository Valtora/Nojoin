"""Capture-frozen runtime selection and best-effort native release."""

from __future__ import annotations

import gc
import logging
import sys
from pathlib import Path

from backend.utils.config_manager import config_manager

from .engine import LiveLabelConfig
from .hybrid import NATIVE_ERRORS, HybridLiveState, failure_reason
from .nemotron import NemotronStream
from .state import new_engine

logger = logging.getLogger(__name__)


def release_native() -> None:
    """Release model/cache references after leaving an inference exception frame."""
    from backend.processing.nemotron_model import release_nemotron_model_cache

    release_nemotron_model_cache()
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is not None and torch.cuda.is_available():
        torch.cuda.empty_cache()


def start_capture(
    live_dir: Path, config: LiveLabelConfig, *, device: str, recording_id: int
) -> HybridLiveState:
    """Select once. A missing/invalid model never switches into this capture later."""
    selection = str(config_manager.get("speaker_engine", "auto") or "auto")
    if selection not in {"auto", "classic"}:
        logger.warning("Invalid speaker_engine value; using auto")
        selection = "auto"
    engine = HybridLiveState(
        new_engine(live_dir, config), None, speaker_engine=selection
    )
    engine.recording_id = recording_id
    if selection == "classic" or not device.startswith("cuda"):
        engine.reason = "configured" if selection == "classic" else "cpu"
        return engine
    from backend.utils.nemotron_model_utils import inspect_nemotron_model

    reason = None
    runtime = None
    try:
        resolution = inspect_nemotron_model()
        if resolution.state != "verified":
            engine.reason = (
                "model_missing"
                if resolution.state in {"missing", "partial"}
                else "model_invalid"
            )
            return engine
        from backend.processing.nemotron_model import load_nemotron_model

        runtime = load_nemotron_model(device=device)
        engine = HybridLiveState(
            engine.shadow,
            NemotronStream(runtime),
            speaker_engine=selection,
            fingerprints=(runtime.asset_fingerprint, runtime.runtime_fingerprint),
        )
        engine.recording_id = recording_id
    except NATIVE_ERRORS as exc:
        reason = failure_reason(exc)
    if reason is not None:
        engine.reason = reason
        runtime = None
        release_native()
    return engine


def bind_capture(engine: HybridLiveState, *, device: str) -> None:
    """Rebind restored CPU cache only to the exact saved runtime identity."""
    if engine.stream is None:
        return
    reason = None
    runtime = None
    try:
        from backend.processing.nemotron_model import load_nemotron_model

        runtime = load_nemotron_model(device=device)
        if (
            runtime.asset_fingerprint != engine.asset_fingerprint
            or runtime.runtime_fingerprint != engine.runtime_fingerprint
        ):
            reason = "state_incompatible"
        else:
            engine.stream.bind_runtime(runtime)
    except NATIVE_ERRORS as exc:
        reason = failure_reason(exc)
    if reason is not None:
        engine.fallback(reason, engine.consumed_n)
        runtime = None
        release_native()


def has_label_history(recording_id: int) -> bool:
    """A missing checkpoint cannot restart an already published namespace."""
    from sqlmodel import select

    from backend.core.db import get_sync_session
    from backend.models.pipeline import TranscriptUtteranceEvent

    session = get_sync_session()
    try:
        statement = (
            select(TranscriptUtteranceEvent.id)
            .where(TranscriptUtteranceEvent.recording_id == recording_id)
            .where(TranscriptUtteranceEvent.event_type == "live_speaker_label")
            .limit(1)
        )
        return session.execute(statement).first() is not None
    finally:
        session.close()
