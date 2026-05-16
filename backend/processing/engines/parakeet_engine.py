# nojoin/processing/engines/parakeet_engine.py
# Parakeet transcription engine backed by onnx-asr. Heavy imports (onnx_asr,
# onnxruntime, soundfile) live inside methods, never at module top.

import logging

from .base import TranscriptionEngine

logger = logging.getLogger(__name__)

# Maps Nojoin model ids to onnx-asr model ids.
_ONNX_ASR_MODEL_IDS = {"parakeet-tdt-0.6b-v3": "nemo-parakeet-tdt-0.6b-v3"}

# Gap (seconds) between consecutive words that starts a new segment.
SEGMENT_PAUSE_THRESHOLD_S = 0.8


def _to_onnx_asr_id(nojoin_id: str) -> str:
    """Resolve a Nojoin model id to its onnx-asr equivalent."""
    return _ONNX_ASR_MODEL_IDS.get(nojoin_id, nojoin_id)


def map_parakeet_result(text: str, tokens: list[str] | None, timestamps: list[float] | None,
                        audio_duration: float | None = None) -> dict:
    """Map an onnx-asr timestamped result into the canonical transcription schema.

    Args:
        text: The recognized transcript text (already space-reconstructed).
        tokens: Parallel list of subword tokens, or None.
        timestamps: Parallel list of per-token start times in seconds, or None.
        audio_duration: Total audio duration in seconds, optional.

    Returns:
        A dict with the canonical schema: text, language (always None), segments.
    """
    # Fallback: no usable token-level timing data.
    if not tokens or not timestamps or len(tokens) != len(timestamps):
        return {
            "text": text,
            "language": None,
            "segments": ([{"start": 0.0, "end": audio_duration or 0.0, "text": text}] if text else []),
        }

    # Build words by grouping subword tokens. A new word begins at index 0 and at
    # any token whose string starts with a space.
    words: list[dict] = []
    for index, token in enumerate(tokens):
        if index == 0 or token.startswith(" "):
            words.append({"start": timestamps[index], "word": token})
        else:
            words[-1]["word"] += token

    # Assign word end times: next word's start, or audio end for the last word.
    # When the gap to the next word exceeds the pause threshold, the word ends
    # shortly after its start so the silent gap is not absorbed into the word
    # (this keeps the segment-split logic below functional).
    for index, word in enumerate(words):
        if index + 1 < len(words):
            next_start = words[index + 1]["start"]
            if next_start - word["start"] > SEGMENT_PAUSE_THRESHOLD_S:
                word["end"] = word["start"] + 0.2
            else:
                word["end"] = next_start
        elif audio_duration is not None and audio_duration > word["start"]:
            word["end"] = audio_duration
        else:
            word["end"] = word["start"] + 0.2

    # Ensure every word string carries a single leading space for downstream
    # text reconstruction.
    for word in words:
        if not word["word"].startswith(" "):
            word["word"] = " " + word["word"]

    # Fallback: grouping yielded no words but text exists.
    if not words:
        return {
            "text": text,
            "language": None,
            "segments": ([{"start": 0.0, "end": audio_duration or 0.0, "text": text}] if text else []),
        }

    # Group words into segments separated by pauses.
    segments: list[dict] = []
    current: list[dict] = [words[0]]
    for word in words[1:]:
        if word["start"] - current[-1]["end"] > SEGMENT_PAUSE_THRESHOLD_S:
            segments.append(current)
            current = [word]
        else:
            current.append(word)
    segments.append(current)

    result_segments: list[dict] = []
    for group in segments:
        result_segments.append({
            "start": group[0]["start"],
            "end": group[-1]["end"],
            "text": "".join(word["word"] for word in group),
            "words": [{"start": w["start"], "end": w["end"], "word": w["word"]} for w in group],
        })

    return {"text": text, "language": None, "segments": result_segments}


class ParakeetEngine(TranscriptionEngine):
    """Parakeet transcription engine backed by onnx-asr."""

    name = "parakeet"

    def __init__(self) -> None:
        # Cache for loaded models, keyed by onnx-asr model id.
        self._model_cache: dict = {}

    def _get_model(self, config: dict):
        """Load (once, lazily) and return the onnx-asr model for the given config."""
        nojoin_id = config.get("parakeet_model", "parakeet-tdt-0.6b-v3") if config else "parakeet-tdt-0.6b-v3"
        onnx_id = _to_onnx_asr_id(nojoin_id)
        if onnx_id not in self._model_cache:
            import onnx_asr
            logger.info(f"Loading Parakeet model: {onnx_id}")
            self._model_cache[onnx_id] = onnx_asr.load_model(
                onnx_id,
                quantization="int8",
                providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
            )
            logger.info(f"Parakeet model {onnx_id} loaded successfully.")
        return self._model_cache[onnx_id]

    def transcribe(self, audio_path: str, config: dict) -> dict | None:
        """Transcribe the given audio file using onnx-asr Parakeet.

        Args:
            audio_path: Path to a 16kHz mono WAV file.
            config: Configuration dictionary (reads parakeet_model).

        Returns:
            The canonical transcription dict, or None on failure / missing file.
        """
        import os

        if not os.path.exists(audio_path):
            logger.error(f"Audio file not found for transcription: {audio_path}")
            return None

        try:
            model = self._get_model(config or {})

            logger.info(f"Starting Parakeet transcription for {audio_path}")
            result = model.with_timestamps().recognize(audio_path)

            # Audio duration is optional; failure to read it must not abort.
            audio_duration: float | None = None
            try:
                import soundfile
                audio_duration = soundfile.info(audio_path).duration
            except Exception as e:
                logger.warning(f"Could not read audio duration for {audio_path}: {e}")

            logger.info(f"Parakeet transcription completed for {audio_path}")
            return map_parakeet_result(result.text, result.tokens, result.timestamps, audio_duration)

        except Exception as e:
            logger.error(f"Error during Parakeet transcription for {audio_path}: {e}", exc_info=True)
            return None

    def release(self) -> None:
        """Release all loaded Parakeet models from memory."""
        if self._model_cache:
            logger.info(f"Releasing {list(self._model_cache.keys())} from Parakeet model cache.")
            self._model_cache.clear()
