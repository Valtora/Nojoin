# nojoin/processing/engines/onnx_asr_engine.py
# Shared base for onnx-asr-backed transcription engines (Parakeet, Canary).
# Heavy imports (onnx_asr, soundfile) live inside methods, never at module top.

import logging

from .base import TranscriptionEngine

logger = logging.getLogger(__name__)

# Gap (seconds) between consecutive words that starts a new segment.
SEGMENT_PAUSE_THRESHOLD_S = 0.8

# Longest audio (seconds) fed to onnx-asr in a single recognize() call. onnx-asr
# has no long-form support: the exported Parakeet/Canary FastConformer attention
# does not handle long sequences — past ~5 min recognize() raises an onnxruntime
# shape error, and on long meetings the O(n^2) attention tensor exhausts memory
# and the call never returns. Longer audio is transcribed in windows capped here.
MAX_CHUNK_DURATION_S = 240.0

# Half-width (seconds) of the search window used to snap a chunk boundary onto
# the quietest nearby point, so a window never cuts through a spoken word.
CHUNK_SNAP_RADIUS_S = 18.0


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


def _shift_segment(segment: dict, offset_s: float) -> dict:
    """Return a copy of a canonical segment with every time shifted by offset_s."""
    shifted = {
        "start": segment["start"] + offset_s,
        "end": segment["end"] + offset_s,
        "text": segment["text"],
    }
    if segment.get("words"):
        shifted["words"] = [
            {"start": w["start"] + offset_s, "end": w["end"] + offset_s, "word": w["word"]}
            for w in segment["words"]
        ]
    return shifted


def _chunk_boundaries(audio, sample_rate: int) -> list[tuple[int, int]]:
    """Split [0, len(audio)) into windows of at most MAX_CHUNK_DURATION_S.

    Each internal boundary is snapped, within CHUNK_SNAP_RADIUS_S of the ideal
    cut, onto the quietest 0.2 s of audio so a window never cuts mid-word.
    Returns a list of (start_frame, end_frame) pairs covering the whole signal.
    """
    total_frames = len(audio)
    target = int(MAX_CHUNK_DURATION_S * sample_rate)
    radius = int(CHUNK_SNAP_RADIUS_S * sample_rate)
    probe = max(1, int(0.2 * sample_rate))

    boundaries: list[tuple[int, int]] = []
    start = 0
    while total_frames - start > target:
        ideal = start + target
        low = max(ideal - radius, start + probe)
        high = min(ideal + radius, total_frames - probe)
        cut = ideal
        if high > low:
            quietest = None
            for pos in range(low, high, max(1, probe // 2)):
                energy = float(abs(audio[pos:pos + probe]).mean())
                if quietest is None or energy < quietest:
                    quietest = energy
                    cut = pos
        boundaries.append((start, cut))
        start = cut
    boundaries.append((start, total_frames))
    return boundaries


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

        Audio longer than MAX_CHUNK_DURATION_S is transcribed window-by-window
        (onnx-asr has no long-form support); shorter audio goes through a single
        recognize() call.

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
            recognizer = model.with_timestamps()

            # Audio duration is optional; failure to read it must not abort, but
            # without it the audio cannot be safely chunked (single-pass only).
            audio_duration: float | None = None
            try:
                import soundfile
                audio_duration = soundfile.info(audio_path).duration
            except Exception as e:
                logger.warning(f"Could not read audio duration for {audio_path}: {e}")

            logger.info(f"Starting {self.name} transcription for {audio_path}")

            if audio_duration is not None and audio_duration > MAX_CHUNK_DURATION_S:
                result = self._transcribe_chunked(recognizer, audio_path, audio_duration)
            else:
                recognized = recognizer.recognize(audio_path)
                result = map_onnx_asr_result(
                    recognized.text, recognized.tokens, recognized.timestamps, audio_duration
                )

            logger.info(f"{self.name} transcription completed for {audio_path}")
            return result

        except Exception as e:
            logger.error(f"Error during {self.name} transcription for {audio_path}: {e}", exc_info=True)
            return None

    def _transcribe_chunked(self, recognizer, audio_path: str, audio_duration: float) -> dict:
        """Transcribe long audio as a sequence of windows, then merge.

        Each window is at most MAX_CHUNK_DURATION_S long and its boundaries snap
        onto quiet points so a spoken word is never split. Per-window segments
        are shifted into absolute time and concatenated.
        """
        import os
        import tempfile

        import soundfile

        audio, sample_rate = soundfile.read(audio_path, dtype="float32", always_2d=False)
        if getattr(audio, "ndim", 1) > 1:
            audio = audio.mean(axis=1)

        boundaries = _chunk_boundaries(audio, sample_rate)
        logger.info(
            f"{self.name}: {audio_duration:.0f}s audio exceeds the "
            f"{MAX_CHUNK_DURATION_S:.0f}s single-pass limit; transcribing in "
            f"{len(boundaries)} windows."
        )

        texts: list[str] = []
        segments: list[dict] = []
        for index, (start_frame, end_frame) in enumerate(boundaries):
            offset_s = start_frame / sample_rate
            chunk = audio[start_frame:end_frame]
            chunk_duration = len(chunk) / sample_rate

            handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            chunk_path = handle.name
            handle.close()
            try:
                soundfile.write(chunk_path, chunk, sample_rate)
                recognized = recognizer.recognize(chunk_path)
            finally:
                try:
                    os.remove(chunk_path)
                except OSError:
                    pass

            chunk_result = map_onnx_asr_result(
                recognized.text, recognized.tokens, recognized.timestamps, chunk_duration
            )
            if chunk_result["text"]:
                texts.append(chunk_result["text"])
            segments.extend(_shift_segment(s, offset_s) for s in chunk_result["segments"])
            logger.info(
                f"{self.name}: window {index + 1}/{len(boundaries)} done "
                f"({offset_s:.0f}s +{chunk_duration:.0f}s)."
            )

        return {"text": " ".join(texts), "language": None, "segments": segments}

    def release(self) -> None:
        """Release all loaded models from memory."""
        if self._model_cache:
            logger.info(f"Releasing {list(self._model_cache.keys())} from {self.name} model cache.")
            self._model_cache.clear()
