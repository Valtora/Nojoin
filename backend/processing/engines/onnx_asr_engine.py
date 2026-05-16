# nojoin/processing/engines/onnx_asr_engine.py
# Shared base for onnx-asr-backed transcription engines (Parakeet, Canary).
# Heavy imports (onnx_asr, soundfile) live inside methods, never at module top.

import logging

from .base import TranscriptionEngine

logger = logging.getLogger(__name__)

# Gap (seconds) between consecutive words that starts a new segment.
SEGMENT_PAUSE_THRESHOLD_S = 0.8


def map_onnx_asr_result(text: str, tokens: list[str] | None, timestamps: list[float] | None,
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


class OnnxAsrEngine(TranscriptionEngine):
    """Shared transcription engine backed by onnx-asr.

    Subclasses parameterise the model via class attributes (name, config_key,
    default_model_id, onnx_id_map). The onnx-asr recognize() path and the
    timestamped-result mapper are architecture-agnostic and shared here.
    """

    # Subclass sets these.
    name: str = ""
    # The config key holding the model id.
    config_key: str = ""
    default_model_id: str = ""
    # Optional Nojoin-id -> onnx-asr-id map. Empty when ids already match.
    onnx_id_map: dict[str, str] = {}

    def __init__(self) -> None:
        # Cache for loaded models, keyed by onnx-asr model id.
        self._model_cache: dict = {}

    def _to_onnx_asr_id(self, nojoin_id: str) -> str:
        """Resolve a Nojoin model id to its onnx-asr equivalent."""
        return self.onnx_id_map.get(nojoin_id, nojoin_id)

    def _get_model(self, config: dict):
        """Load (once, lazily) and return the onnx-asr model for the given config."""
        nojoin_id = config.get(self.config_key, self.default_model_id) if config else self.default_model_id
        onnx_id = self._to_onnx_asr_id(nojoin_id)
        if onnx_id not in self._model_cache:
            import onnx_asr
            logger.info(f"Loading {self.name} model: {onnx_id}")
            self._model_cache[onnx_id] = onnx_asr.load_model(
                onnx_id,
                quantization="int8",
                providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
            )
            logger.info(f"{self.name} model {onnx_id} loaded successfully.")
        return self._model_cache[onnx_id]

    def transcribe(self, audio_path: str, config: dict) -> dict | None:
        """Transcribe the given audio file using onnx-asr.

        Args:
            audio_path: Path to a 16kHz mono WAV file.
            config: Configuration dictionary (reads the engine's config_key).

        Returns:
            The canonical transcription dict, or None on failure / missing file.
        """
        import os

        if not os.path.exists(audio_path):
            logger.error(f"Audio file not found for transcription: {audio_path}")
            return None

        try:
            model = self._get_model(config or {})

            logger.info(f"Starting {self.name} transcription for {audio_path}")
            result = model.with_timestamps().recognize(audio_path)

            # Audio duration is optional; failure to read it must not abort.
            audio_duration: float | None = None
            try:
                import soundfile
                audio_duration = soundfile.info(audio_path).duration
            except Exception as e:
                logger.warning(f"Could not read audio duration for {audio_path}: {e}")

            logger.info(f"{self.name} transcription completed for {audio_path}")
            return map_onnx_asr_result(result.text, result.tokens, result.timestamps, audio_duration)

        except Exception as e:
            logger.error(f"Error during {self.name} transcription for {audio_path}: {e}", exc_info=True)
            return None

    def release(self) -> None:
        """Release all loaded models from memory."""
        if self._model_cache:
            logger.info(f"Releasing {list(self._model_cache.keys())} from {self.name} model cache.")
            self._model_cache.clear()
