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
from backend.processing.speaker_cap import normalize_speaker_cap
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


def _engine_selection() -> str:
    selection = str(config_manager.get("speaker_engine", "auto") or "auto")
    if selection not in {"auto", "classic"}:
        logger.warning("Invalid speaker_engine value; using auto")
        selection = "auto"
    return selection


def _asset_state() -> str | None:
    """Nemotron's asset state, or None when it cannot be read.

    None sends a capped recording down the native route, whose own inspection
    then records the failure as it does for an uncapped one.
    """
    from backend.utils.nemotron_model_utils import inspect_nemotron_model

    try:
        return inspect_nemotron_model().state
    except Exception as exc:
        if is_task_interruption(exc) or isinstance(exc, CancelledError):
            raise
        return None


def _request_preparation(state: str, status: dict, recording_id: int | None) -> None:
    """Queue first-use preparation; this recording keeps community speakers."""
    from backend.services.model_preparation import request_nemotron_preparation

    status.update(route="ineligible", reason=f"assets_{state}")
    request_nemotron_preparation(recording_id=recording_id)


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
            _request_preparation(resolution.state, status, recording_id)
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
    """Return today's annotation unchanged when native cannot safely improve it.

    A recording with a speaker cap goes through ``_diarize_capped`` instead. An
    out-of-range cap is no cap, as ``diarize_audio`` treats it.
    """
    from backend.processing.diarize import diarize_audio

    outcome = status if status is not None else {}
    cap = normalize_speaker_cap(max_speakers)
    if cap is not None:
        return _diarize_capped(audio_path, config, cap, outcome, recording_id)
    outcome.update(route="ineligible", reason="community_empty")
    community = diarize_audio(audio_path, config=config, max_speakers=max_speakers)
    if community is None or not community:
        return community
    if _engine_selection() == "classic":
        outcome["reason"] = "classic"
        return community
    device = _effective_device(config)
    if not device.startswith("cuda"):
        outcome["reason"] = "cpu"
        return community
    try:
        return _native_pass(audio_path, community, device, outcome, recording_id)
    finally:
        release_final_native()


def _diarize_capped(
    audio_path: str,
    config: dict | None,
    cap: int,
    outcome: dict,
    recording_id: int | None,
) -> Annotation | None:
    """Final speakers for a recording with a speaker cap (#871).

    Nemotron is prepared and run as for an uncapped recording, over an uncapped
    community-1 result, and ``choose_capped_speakers`` keeps the union only if
    it fits the cap. Where no union can be made (classic, CPU, assets not yet
    verified), community-1 runs once, with the cap.
    """
    from backend.processing.diarize import diarize_audio

    outcome.update(route="ineligible", reason="community_empty", cap=cap)
    device = "" if _engine_selection() == "classic" else _effective_device(config)
    state = _asset_state() if device.startswith("cuda") else None
    if not device.startswith("cuda") or state not in (None, "verified"):
        community = diarize_audio(audio_path, config=config, max_speakers=cap)
        if community is None or not community:
            return community
        if state is None:
            outcome["reason"] = "cpu" if device else "classic"
        else:
            try:
                _request_preparation(state, outcome, recording_id)
            except Exception as exc:
                if is_task_interruption(exc) or isinstance(exc, CancelledError):
                    raise
                logger.exception("Could not request Nemotron preparation")
        outcome["speakers"] = len(community.labels())
        _log_capped(outcome, recording_id)
        return community

    community = diarize_audio(audio_path, config=config)
    if community is None or not community:
        return community
    try:
        candidate = _native_pass(audio_path, community, device, outcome, recording_id)
    finally:
        release_final_native()
    return choose_capped_speakers(
        audio_path, config, cap, community, candidate, outcome, recording_id
    )


def choose_capped_speakers(  # noqa: PLR0913 - the pass's inputs and its status
    audio_path: str,
    config: dict | None,
    cap: int,
    community: Annotation,
    candidate: Annotation,
    status: dict,
    recording_id: int | None = None,
) -> Annotation | None:
    """Keep the union when it fits the cap, otherwise community-1 with the cap.

    ``candidate`` is what the native pass returned for the uncapped
    ``community``, and ``status`` holds its route. The union's raw speaker
    count is its number of distinct labels: each Nemotron channel with any
    frame above 0.5, plus each community-1 speaker that shares no time with
    Nemotron's speech. When that is over the cap, the union is counted again
    after the phantom filter, as the pipeline applies it to the kept result
    next (``_union_speakers_after_filter``), and that count decides. The union
    is kept unfiltered, so the pipeline's own run of the filter is the only
    one applied to it.

    Community-1 with the cap is the uncapped run when that already fits:
    pyannote applies ``max_speakers`` only when it finds more speakers than
    that, so a second run would return the same speakers. Otherwise community-1
    runs again with the cap, after Nemotron has been released.
    """
    from backend.processing.diarize import diarize_audio

    union_speakers = len(candidate.labels()) if status.get("route") == "union" else None
    community_speakers = len(community.labels())
    status.update(
        native_route=status.get("route"),
        native_reason=status.get("reason"),
        cap=cap,
        union_speakers=union_speakers,
        community_speakers=community_speakers,
    )
    counted = union_speakers
    if union_speakers is not None and union_speakers > cap:
        filtered = _union_speakers_after_filter(candidate, audio_path, config)
        status["union_speakers_filtered"] = filtered
        if filtered is not None:
            counted = filtered
    if counted is not None and counted <= cap:
        status.update(route="union", reason="union_within_cap")
        result: Annotation | None = candidate
    else:
        reused = community_speakers <= cap
        status.update(
            route="community_capped",
            reason="union_unavailable" if union_speakers is None else "union_over_cap",
            community_reused=reused,
        )
        result = (
            community
            if reused
            else diarize_audio(audio_path, config=config, max_speakers=cap)
        )
    status["speakers"] = len(result.labels()) if result is not None else None
    _log_capped(status, recording_id)
    return result


def _union_speakers_after_filter(
    union: Annotation, audio_path: str, config: dict | None
) -> int | None:
    """The union's speaker count after the phantom filter the pipeline runs.

    The same filter, settings and audio as ``run_phantom_filter``. With the
    filter off in settings it returns the union unchanged, so this is the raw
    count. None when the filter fails, as the pipeline then keeps the
    unfiltered result and the raw count stands.
    """
    from backend.processing.phantom_filter import filter_phantom_speakers

    try:
        return len(filter_phantom_speakers(union, audio_path, config=config).labels())
    except Exception as exc:  # noqa: BLE001 -- boundary: a failed filter leaves the raw count, as the pipeline then keeps the unfiltered result
        if is_task_interruption(exc) or isinstance(exc, CancelledError):
            raise
        logger.warning(
            "Phantom filter failed while counting the union's speakers; "
            "using its raw count: %s",
            type(exc).__name__,
        )
        return None


def _log_capped(status: dict, recording_id: int | None) -> None:
    """One line per capped recording, counts only, for comparing runs."""
    fields = (
        "route",
        "reason",
        "cap",
        "union_speakers",
        "union_speakers_filtered",
        "community_speakers",
        "community_reused",
        "speakers",
        "native_route",
        "native_reason",
    )
    logger.info(
        "Capped final speakers for recording %s: %s",
        recording_id,
        " ".join(f"{key}={status[key]}" for key in fields if key in status),
    )
