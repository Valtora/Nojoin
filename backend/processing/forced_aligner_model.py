"""Verified native alignment with serial inference and explicit timing failures."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np

from backend.processing.alignment_spans import retained_timings
from backend.processing.engines.errors import is_task_interruption
from backend.processing.forced_aligner_native import (
    ALIGNMENT_TEMPLATE,
    load_native_model,
    load_native_processor,
)
from backend.utils import forced_aligner_model_utils as assets
from backend.utils.forced_aligner_manifest import ForcedAlignerError

logger = logging.getLogger(__name__)
_LOCK = threading.RLock()
_PROCESSOR: tuple[Path, Any] | None = None
_MODEL: Any = None
CPU_MAX_AUDIO_S = 60.0
CPU_MAX_INPUT_TOKENS = 4096


def _prepared_path() -> Path:
    resolution = assets.inspect_forced_aligner_model()
    if resolution.state == "verified" and resolution.path is not None:
        return resolution.path
    return assets.prepare_forced_aligner_model()


def _processor(path: Path) -> Any:
    global _PROCESSOR
    if _PROCESSOR is None:
        assets.verify_forced_aligner_model(path)
        _PROCESSOR = path, load_native_processor(path)
    return _PROCESSOR[1]


def _runtime(path: Path) -> Any:
    import torch

    global _MODEL
    if _MODEL is None:
        assets.verify_forced_aligner_model(path)
        _MODEL = load_native_model(path)
        logger.info(
            "Forced aligner loaded on CPU fp32 (%s intra-op threads)",
            torch.get_num_threads(),
        )
    return _MODEL


def _validate_request(audio: np.ndarray, text: str, language: str) -> float:
    if (
        not isinstance(audio, np.ndarray)
        or audio.ndim != 1
        or audio.dtype != np.float32
    ):
        raise ForcedAlignerError("Alignment audio must be mono 16 kHz float32 PCM")
    if not len(audio) or not np.isfinite(audio).all():
        raise ForcedAlignerError("Alignment audio must be nonempty and finite")
    if not isinstance(text, str) or not text.strip():
        raise ForcedAlignerError("Alignment transcript must be nonempty text")
    if (
        not isinstance(language, str)
        or not language.strip()
        or language.lower() == "auto"
    ):
        raise ForcedAlignerError("Alignment requires a supported explicit language")
    duration = len(audio) / 16000.0
    if duration > CPU_MAX_AUDIO_S:
        raise ForcedAlignerError("Alignment audio exceeds the 60 second safety bound")
    return duration


def _prepare_inputs(
    processor: Any, audio: np.ndarray, text: str, language: str
) -> tuple[Any, list[str]]:
    inputs, batches = processor.prepare_forced_aligner_inputs(
        audio=audio,
        transcript=text,
        language=language,
        chat_template=ALIGNMENT_TEMPLATE,
        return_tensors="pt",
        sampling_rate=16000,
    )
    if len(batches) != 1 or not batches[0]:
        raise ForcedAlignerError("Alignment transcript has no supported lexical tokens")
    ids = inputs["input_ids"]
    if ids.ndim != 2 or ids.shape[0] != 1 or ids.shape[1] > CPU_MAX_INPUT_TOKENS:
        raise ForcedAlignerError("Alignment input exceeds the 4096 token safety bound")
    if int(
        (ids == processor.tokenizer.convert_tokens_to_ids("<timestamp>")).sum()
    ) != 2 * len(batches[0]):
        raise ForcedAlignerError("Alignment prompt timestamp marker count differs")
    return inputs, batches[0]


def raw_alignment(
    logits: Any, ids: Any, words: list[str], config: Any, duration: float
) -> list[dict[str, Any]]:
    """Decode raw predictions only; never invoke native timestamp interpolation."""
    import torch

    if (
        logits.ndim != 3
        or logits.shape[:2] != ids.shape
        or logits.shape[2] != config.num_labels
    ):
        raise ForcedAlignerError("Alignment returned malformed logits")
    if not bool(torch.isfinite(logits).all()):
        raise ForcedAlignerError("Alignment returned nonfinite logits")
    raw = logits.argmax(dim=-1)[0][ids[0] == config.timestamp_token_id]
    if raw.numel() != 2 * len(words):
        raise ForcedAlignerError("Alignment output timestamp count differs")
    boundaries = (
        raw.detach().cpu().numpy().astype(np.float64)
        * config.timestamp_segment_time
        / 1000.0
    )
    entries = [
        {"text": word, "start_time": float(start), "end_time": float(end)}
        for word, start, end in zip(words, boundaries[::2], boundaries[1::2])
    ]
    try:
        timings = retained_timings(entries, duration)
    except ValueError as exc:
        raise ForcedAlignerError(str(exc)) from exc
    return [
        {
            **entry,
            "start_time": timing[0] if timing else None,
            "end_time": timing[1] if timing else None,
        }
        for entry, timing in zip(entries, timings)
    ]


def _align(
    path: Path,
    audio: np.ndarray,
    text: str,
    language: str,
) -> list[dict[str, Any]]:
    import torch

    duration = len(audio) / 16000.0
    processor = _processor(path)
    inputs, words = _prepare_inputs(processor, audio, text, language)
    model = _runtime(path)
    inputs = inputs.to(device="cpu", dtype=torch.float32)
    with (
        torch.inference_mode(),
        torch.autocast(device_type="cpu", enabled=False),
    ):
        output = model(**inputs, use_cache=False)
        return raw_alignment(
            output.logits, inputs["input_ids"], words, model.config, duration
        )


def align_audio(audio: np.ndarray, *, text: str, language: str) -> list[dict[str, Any]]:
    """Return every lexical token with observed audio-relative seconds or nulls.

    Uses one CPU fp32 model and the worker's existing Torch intra-op thread count.

    Raises:
        ForcedAlignerError: Inputs, assets, loading or all predicted spans are invalid.
    """
    _validate_request(audio, text, language)
    try:
        # Preparation may take the file lock, so it must precede the runtime lock.
        path = _prepared_path()
        with assets.control.model_lock(), _LOCK:
            # Deletion between preparation and this lock is an explicit failure.
            resolution = assets.inspect_forced_aligner_model()
            if resolution.state != "verified" or resolution.path != path:
                raise ForcedAlignerError("Forced aligner assets changed before loading")
            return _align(path, audio, text, language)
    except ForcedAlignerError:
        raise
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        RuntimeError,
        ImportError,
    ) as exc:
        if is_task_interruption(exc):
            raise
        raise ForcedAlignerError("Native forced alignment failed") from exc


def release_forced_aligner_model_cache() -> None:
    """Drop only this process's aligner references, without changing priority models."""
    global _PROCESSOR, _MODEL
    with _LOCK:
        _MODEL = None
        _PROCESSOR = None
