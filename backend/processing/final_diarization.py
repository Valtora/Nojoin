"""Community-first final speakers with a local, best-effort offline native pass."""

from __future__ import annotations

import asyncio
import gc
import logging
import traceback
from concurrent.futures import CancelledError
from typing import TYPE_CHECKING

import numpy as np

from backend.processing.engines.errors import is_task_interruption
from backend.processing.nemotron_types import NemotronStateError, validate_probabilities
from backend.utils.config_manager import config_manager

if TYPE_CHECKING:
    from pyannote.core import Annotation

logger = logging.getLogger(__name__)


def _clear_exception_frames(error: BaseException) -> None:
    """Discard tensor owners even when a logger retains a chained exception."""
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        traceback.clear_frames(current.__traceback__)
        for linked in (current.__cause__, current.__context__):
            if linked is not None:
                pending.append(linked)


def _effective_device(config: dict | None) -> str:
    import torch

    get_config = config.get if config else config_manager.get
    device = get_config("processing_device", "auto")
    if not torch.cuda.is_available():
        return "cpu"
    return "cuda" if device == "auto" else device


def _release_community() -> None:
    import torch

    from backend.processing.diarize import release_pipeline_cache

    release_pipeline_cache()
    gc.collect()
    torch.cuda.empty_cache()


def release_final_native() -> None:
    """Release shared native references after inference call frames have exited."""
    import sys

    try:
        module = sys.modules.get("backend.processing.nemotron_model")
        if module is not None:
            module.release_nemotron_model_cache()
        gc.collect()
        torch = sys.modules.get("torch")
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception as exc:
        if is_task_interruption(exc) or isinstance(exc, CancelledError):
            raise
        logger.exception("Could not fully release final native speaker memory")


def _read_audio(audio_path: str) -> np.ndarray:
    import soundfile as sf

    audio, sample_rate = sf.read(audio_path, dtype="float32", always_2d=False)
    if sample_rate != 16000 or audio.ndim != 1:
        raise NemotronStateError("Final native speakers require 16000 Hz mono audio")
    return audio


def _native_pass(
    audio_path: str,
    community: Annotation,
    device: str,
    status: dict,
    recording_id: int | None,
) -> Annotation:
    """Contain failed inference frames so outer cleanup can collect CUDA objects."""
    runtime = None
    status.update(route="native", reason="running")
    try:
        from backend.processing.diarization_union import (
            native_annotation,
            union_diarization,
        )
        from backend.processing.nemotron_model import load_nemotron_model
        from backend.utils.nemotron_model_utils import inspect_nemotron_model

        resolution = inspect_nemotron_model()
        if resolution.state != "verified":
            status.update(route="ineligible", reason=f"assets_{resolution.state}")
            from backend.services.model_preparation import request_nemotron_preparation

            request_nemotron_preparation(recording_id=recording_id)
            return community
        _release_community()
        audio = _read_audio(audio_path)
        runtime = load_nemotron_model(device=device)
        probabilities = runtime.infer_offline(audio)
        if not isinstance(probabilities, np.ndarray) or probabilities.ndim != 2:
            raise NemotronStateError("Final native probabilities must be a matrix")
        validate_probabilities(probabilities, len(audio) // 160 + 1)
        if np.any(probabilities[:, 7].astype(np.float16) > 0.5):
            status.update(route="community_channel_7", reason="channel_7_active")
            return community
        result = union_diarization(
            community, native_annotation(probabilities, uri=community.uri)
        )
        status.update(route="union", reason="channel_7_inactive")
        return result
    except Exception as exc:
        if is_task_interruption(exc) or isinstance(exc, CancelledError):
            status.update(route="interrupted", reason=type(exc).__name__)
            # Remove inference frames holding CUDA tensors before final cleanup.
            _clear_exception_frames(exc)
            exc.__traceback__ = None
            raise
        logger.exception("Final native speaker pass failed; keeping community speakers")
        status.update(route="failed", reason=type(exc).__name__)
        # Log handlers may retain exc_info; discard completed inference-frame locals.
        _clear_exception_frames(exc)
    except asyncio.CancelledError as exc:
        status.update(route="interrupted", reason=type(exc).__name__)
        _clear_exception_frames(exc)
        exc.__traceback__ = None
        raise
    finally:
        runtime = None
    return community


def diarize_final_audio(
    audio_path: str,
    config: dict | None = None,
    max_speakers: int | None = None,
    *,
    status: dict | None = None,
    recording_id: int | None = None,
) -> Annotation | None:
    """Return today's annotation unchanged when native cannot safely improve it."""
    from backend.processing.diarize import diarize_audio

    outcome = status if status is not None else {}
    outcome.update(route="ineligible", reason="community_empty")
    community = diarize_audio(audio_path, config=config, max_speakers=max_speakers)
    if community is None or not community:
        return community
    selection = str(config_manager.get("speaker_engine", "auto") or "auto")
    if selection not in {"auto", "classic"}:
        logger.warning("Invalid speaker_engine value; using auto")
        selection = "auto"
    if selection == "classic" or max_speakers is not None:
        outcome["reason"] = "classic" if selection == "classic" else "speaker_cap"
        return community
    device = _effective_device(config)
    if not device.startswith("cuda"):
        outcome["reason"] = "cpu"
        return community
    try:
        return _native_pass(audio_path, community, device, outcome, recording_id)
    finally:
        release_final_native()
