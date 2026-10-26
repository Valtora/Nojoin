"""Attach forced word alignment to successful ASR without losing its wording."""

import hashlib
import logging
from math import isfinite
from typing import TYPE_CHECKING, Any

from backend.processing.alignment_spans import (
    AlignmentFailure,
    AlignmentSuccess,
    map_alignment_spans,
)
from backend.utils.languages import resolve_transcription_language_code

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)
ALIGNMENT_SAMPLE_RATE = 16_000


def align_text(
    audio: "np.ndarray",
    *,
    text: str,
    language: str,
    offset_ms: int = 0,
) -> AlignmentSuccess | AlignmentFailure:
    """Return exact source spans and absolute milliseconds, or a typed failure."""
    from backend.processing.forced_aligner_model import align_audio
    from backend.utils.forced_aligner_manifest import ForcedAlignerError

    try:
        entries = align_audio(audio, text=text, language=language)
        return map_alignment_spans(
            text, entries, len(audio) / ALIGNMENT_SAMPLE_RATE, offset_ms
        )
    except (ForcedAlignerError, ValueError) as exc:
        return AlignmentFailure(text, str(exc))


def _read_segment(audio_file: Any, segment: dict[str, Any]) -> tuple["np.ndarray", int]:
    start, end = float(segment["start"]), float(segment["end"])
    duration = len(audio_file) / audio_file.samplerate
    if (
        not all(isfinite(value) for value in (start, end))
        or not 0 <= start < end <= duration + 0.001
    ):
        raise ValueError("The ASR segment has no usable audio bounds for alignment.")
    if audio_file.samplerate != ALIGNMENT_SAMPLE_RATE or audio_file.channels != 1:
        raise ValueError(
            "Forced alignment requires the original 16 kHz mono ASR audio."
        )
    first, last = (
        round(start * audio_file.samplerate),
        round(end * audio_file.samplerate),
    )
    audio_file.seek(first)
    audio = audio_file.read(last - first, dtype="float32", always_2d=False)
    return audio, round(first / audio_file.samplerate * 1000)


def _aligned_segment(
    segment: dict[str, Any], audio_file: Any, language: str
) -> dict[str, Any]:
    text = str(segment.get("text") or "")
    try:
        audio, offset_ms = _read_segment(audio_file, segment)
        outcome = align_text(
            audio,
            text=text,
            language=language,
            offset_ms=offset_ms,
        )
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        outcome = AlignmentFailure(text, str(exc))
    if isinstance(outcome, AlignmentFailure):
        logger.warning("Kept ASR text without forced word timings: %s", outcome.reason)
        return {**segment, "alignment_error": outcome.reason}
    source_id = hashlib.sha256(f"{offset_ms}:{text}".encode()).hexdigest()
    words = [
        {
            "word": word.text,
            "start": word.start_ms / 1000 if word.start_ms is not None else None,
            "end": word.end_ms / 1000 if word.end_ms is not None else None,
            "source_start": word.source_start,
            "source_end": word.source_end,
            "source_id": source_id,
        }
        for word in outcome.words
    ]
    aligned = {**segment, "words": words}
    aligned.pop("alignment_error", None)
    return aligned


def align_transcription(result: dict, audio_path: str, config: dict) -> dict:
    """Align wordless segments before either live or final consumers see them.

    Native word payloads are kept. Unavailable alignment preserves complete ASR
    text and exposes ``alignment_error`` on the source segment.
    """
    import soundfile

    segments = result.get("segments") or []
    if not any(
        segment.get("text") and not segment.get("words") for segment in segments
    ):
        return result
    language = str(
        result.get("language") or resolve_transcription_language_code(config) or ""
    )
    try:
        with soundfile.SoundFile(audio_path) as audio_file:
            aligned = [
                _aligned_segment(segment, audio_file, language)
                if segment.get("text") and not segment.get("words")
                else segment
                for segment in segments
            ]
    except (OSError, RuntimeError) as exc:
        logger.warning("Kept ASR text: alignment audio could not be read: %s", exc)
        aligned = [
            {**segment, "alignment_error": f"Alignment audio could not be read: {exc}"}
            if segment.get("text") and not segment.get("words")
            else segment
            for segment in segments
        ]
    return {**result, "segments": aligned}
