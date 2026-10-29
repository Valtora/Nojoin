"""Enrich filesystem model status with optional Nemotron preparation metadata.

The API reads receipts and atomic pending markers without taking worker locks or
loading inference libraries. Hardware eligibility is explained by the UI; saved
CPU/classic selections can be identified here without probing the worker.
"""

import time
from typing import Any

from backend.models.system_public import ModelPreparationState
from backend.utils.config_manager import config_manager
from backend.utils.download_progress import get_download_progress
from backend.utils.forced_aligner_manifest import (
    load_asset_manifest as aligner_manifest,
)
from backend.utils.forced_aligner_model_utils import control as aligner_control
from backend.utils.nemotron_lock import pending_task
from backend.utils.nemotron_manifest import load_asset_manifest
from backend.utils.transcription_models import engine_has_native_word_timestamps


def add_nemotron_preparation_status(status: dict[str, Any]) -> None:
    """Add lifecycle and manifest size only to the native speaker model row."""
    native = status["nemotron"]
    progress = get_download_progress("nemotron")
    matching = progress if progress and progress.get("stage") == "nemotron" else {}
    native["state"] = _preparation_state(native, matching)
    native["size_bytes"] = sum(
        entry["size"] for entry in load_asset_manifest()["files"]
    )
    native["unused_reason"] = _unused_reason()
    if "forced_aligner" in status:
        aligner = status["forced_aligner"]
        progress = get_download_progress("forced_aligner")
        matching = (
            progress if progress and progress.get("stage") == "forced_aligner" else {}
        )
        aligner["state"] = _preparation_state(
            aligner,
            matching,
            bool(aligner_control.pending_task()),
            retry_wait=aligner_control.failed_recently(),
        )
        aligner["size_bytes"] = sum(
            entry["size"] for entry in aligner_manifest()["files"]
        )
        backend = str(config_manager.get("transcription_backend", "whisper"))
        aligner["unused_reason"] = (
            "Unused while the selected speech engine provides its own word timings."
            if engine_has_native_word_timestamps(backend)
            else None
        )


def _preparation_state(
    native: dict[str, Any],
    progress: dict[str, Any],
    pending: bool | None = None,
    *,
    retry_wait: bool = False,
) -> ModelPreparationState:
    # Use the same freshness window as is_download_in_progress. A stale Redis
    # record must not leave the row downloading after the worker disappears.
    fresh = 0 <= time.time() - progress.get("updated_at", 0) <= 30
    if (fresh and progress.get("status") == "downloading") or (
        pending_task() if pending is None else pending
    ):
        return "downloading"
    if native["downloaded"]:
        return "ready"
    if retry_wait:
        return "retry_wait"
    if progress.get("status") == "error" or native["verification_state"] == "invalid":
        return "failed"
    return "absent"


def _unused_reason() -> str | None:
    if config_manager.get("speaker_engine", "auto") == "classic":
        return "Unused while the classic speaker engine is selected."
    if str(config_manager.get("processing_device", "auto")).lower() == "cpu":
        return "Unused while processing is set to CPU."
    return None
