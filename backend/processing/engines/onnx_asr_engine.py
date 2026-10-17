# Shared base for onnx-asr-backed transcription engines (Parakeet, Canary).
# Heavy imports (onnx_asr, soundfile) live inside methods, never at module top.

import bisect
import gc
import logging
import os
import traceback
from collections.abc import Sequence
from typing import NamedTuple, Protocol

from ...utils.languages import resolve_transcription_language_code
from ...utils.onnx_asr_cache import onnx_asr_model
from ..onnx_providers import gpu_is_present, sessions_use_cuda, verify_gpu_providers
from ..onnx_threads import onnx_session_options
from ..processing_tuning import (
    ASR_WORD_END_PADDING_KEY,
    TUNING_SPECS,
    resolve_tuning,
)
from .base import TranscriptionEngine
from .errors import (
    TranscriptionError,
    is_out_of_memory,
    is_task_interruption,
    transcription_error_from,
)

logger = logging.getLogger(__name__)

os.environ.setdefault("ORT_LOG_SEVERITY_LEVEL", "1")

# Gap (seconds) between consecutive words that starts a new segment.
SEGMENT_PAUSE_THRESHOLD_S = 0.8

# The longest (seconds) a word lasts. onnx-asr returns only token start times,
# so a word ends at the earliest of the next word's start, the end of the
# window, the first silence after it starts, and its start plus this. A user's
# settings may override it (processing_tuning.py).
WORD_END_PAD_S = TUNING_SPECS[ASR_WORD_END_PADDING_KEY].default

# Shortest run (seconds) of exact zeros that counts as silence when ending a
# word. VAD writes non-speech as exact zeros, so in the final pass these runs
# are its muted gaps; speech does not hold exact zeros that long, and unmuted
# audio, such as the live lane's clips, usually has no such run. 0.1 s and
# 0.5 s end words almost exactly as 0.2 s does.
SILENCE_MIN_RUN_S = 0.2

# Longest audio (seconds) fed to onnx-asr in a single recognize() call, on any
# device. onnx-asr has no long-form support, and both models lose speech long
# before a window runs out of memory (#445). On House of Lords recordings,
# Canary lost nearly half its words at the old 120 s GPU window and fell into a
# loop at the old 240 s CPU one, and Parakeet dropped about four times as many
# words at 60 s as at 30 s. Both held at 30 s on either device. Shorter windows
# are also faster, since attention grows with the square of the window.
MAX_CHUNK_DURATION_S = 30.0

# Peak level below which a window holds nothing to transcribe. VAD writes
# non-speech as exact zeros, and Canary invents text for a silent window
# ("San Francisco has developed a large tourist infrastructure...").
SILENT_WINDOW_PEAK = 1e-4


class Recognition(Protocol):
    """The parts of an onnx-asr timestamped result the mapper reads."""

    @property
    def text(self) -> str: ...

    @property
    def tokens(self) -> Sequence[str] | None: ...

    @property
    def timestamps(self) -> Sequence[float] | None: ...


class _Recognition(NamedTuple):
    text: str
    tokens: Sequence[str] | None
    timestamps: Sequence[float] | None


def map_onnx_asr_result(
    text: str,
    tokens: list[str] | None,
    timestamps: list[float] | None,
    audio_duration: float | None = None,
    language: str | None = None,
) -> dict:
    """Map an onnx-asr timestamped result with the default word length cap.

    Args:
        text: The recognized transcript text (already space-reconstructed).
        tokens: Parallel list of subword tokens, or None.
        timestamps: Parallel list of per-token start times in seconds, or None.
        audio_duration: Total audio duration in seconds, optional.

    Returns:
        A dict with the canonical schema: text, language, segments.
    """
    return map_onnx_asr_recognition(
        _Recognition(text, tokens, timestamps), audio_duration, language
    )


def map_onnx_asr_recognition(
    recognized: Recognition,
    audio_duration: float | None = None,
    language: str | None = None,
    word_end_pad_s: float = WORD_END_PAD_S,
    silence_starts: Sequence[float] = (),
) -> dict:
    """Map an onnx-asr timestamped result into the canonical transcription schema.

    onnx-asr gives each token a start time only, so a word ends at the earliest
    of the next word's start, audio_duration, the first of silence_starts after
    the word starts, and its start plus word_end_pad_s. Without token timings
    (Canary) the text becomes one segment spanning the audio, and none of this
    applies.

    Args:
        recognized: The recognize() result: text, plus parallel subword tokens
            and per-token start times in seconds (either may be None).
        audio_duration: Total audio duration in seconds, optional.
        language: The forced language code, if any.
        word_end_pad_s: The longest a word lasts.
        silence_starts: Ascending times (seconds) at which a silence begins.

    Returns:
        A dict with the canonical schema: text, language, segments.
    """
    text, tokens, timestamps = (
        recognized.text,
        recognized.tokens,
        recognized.timestamps,
    )
    # Fallback: no usable token-level timing data.
    if not tokens or not timestamps or len(tokens) != len(timestamps):
        return {
            "text": text,
            "language": language,
            "segments": (
                [{"start": 0.0, "end": audio_duration or 0.0, "text": text}]
                if text
                else []
            ),
        }

    # Build words by grouping subword tokens. A new word begins at index 0 and at
    # any token whose string starts with a space.
    words: list[dict] = []
    for index, token in enumerate(tokens):
        if index == 0 or token.startswith(" "):
            words.append({"start": timestamps[index], "word": token})
        else:
            words[-1]["word"] += token

    # A word stretched over the pause after it can reach into the next
    # speaker's turn and be matched to them.
    for index, word in enumerate(words):
        start = word["start"]
        bounds = [start + word_end_pad_s]
        if index + 1 < len(words):
            bounds.append(words[index + 1]["start"])
        if audio_duration is not None:
            bounds.append(audio_duration)
        pause = bisect.bisect_right(silence_starts, start)
        if pause < len(silence_starts):
            bounds.append(silence_starts[pause])
        word["end"] = max(start, min(bounds))

    # Ensure every word string carries a single leading space for downstream
    # text reconstruction.
    for word in words:
        if not word["word"].startswith(" "):
            word["word"] = " " + word["word"]

    # Fallback: grouping yielded no words but text exists.
    if not words:
        return {
            "text": text,
            "language": language,
            "segments": (
                [{"start": 0.0, "end": audio_duration or 0.0, "text": text}]
                if text
                else []
            ),
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
        result_segments.append(
            {
                "start": group[0]["start"],
                "end": group[-1]["end"],
                "text": "".join(word["word"] for word in group),
                "words": [
                    {"start": w["start"], "end": w["end"], "word": w["word"]}
                    for w in group
                ],
            }
        )

    return {"text": text, "language": language, "segments": result_segments}


def _shift_segment(segment: dict, offset_s: float) -> dict:
    """Return a copy of a canonical segment with every time shifted by offset_s."""
    shifted = {
        "start": segment["start"] + offset_s,
        "end": segment["end"] + offset_s,
        "text": segment["text"],
    }
    if segment.get("words"):
        shifted["words"] = [
            {
                "start": w["start"] + offset_s,
                "end": w["end"] + offset_s,
                "word": w["word"],
            }
            for w in segment["words"]
        ]
    return shifted


def _read_mono(audio_path: str):
    """Read a WAV file as float32 samples, mixed down to mono."""
    import soundfile

    audio, sample_rate = soundfile.read(audio_path, dtype="float32", always_2d=False)
    if getattr(audio, "ndim", 1) > 1:
        audio = audio.mean(axis=1)
    return audio, sample_rate


def _silence_starts(
    audio, start_frame: int, end_frame: int, sample_rate: int
) -> list[float]:
    """Times, relative to start_frame, at which a silence begins in a window.

    A silence is a run of exact zeros at least SILENCE_MIN_RUN_S long that
    begins inside [start_frame, end_frame). The run may continue past the
    window, so one that a window cut shortens still counts.
    """
    import numpy as np

    run = max(1, int(SILENCE_MIN_RUN_S * sample_rate))
    span = np.asarray(audio[start_frame : min(end_frame + run, len(audio))])
    zero = np.concatenate(([False], span == 0, [False]))
    edges = np.flatnonzero(np.diff(zero.astype(np.int8)))
    starts, stops = edges[0::2], edges[1::2]
    keep = (stops - starts >= run) & (starts < end_frame - start_frame)
    return [float(frame) / sample_rate for frame in starts[keep]]


def _chunk_boundaries(audio, sample_rate: int) -> list[tuple[int, int]]:
    """Split [0, len(audio)) into windows of MAX_CHUNK_DURATION_S at most.

    Every window but a lone one lasts at least half that, and closes at the
    first of the quietest 0.2 s spans after its midpoint, so a cut never
    falls inside a word and the next window opens on the pause. A long pause
    is never cut into slivers of silence, and the last cut leaves the final
    window at least half as well. Returns (start_frame, end_frame) pairs
    covering the whole signal.
    """
    total_frames = len(audio)
    longest = int(MAX_CHUNK_DURATION_S * sample_rate)
    shortest = longest // 2
    probe = max(1, int(0.2 * sample_rate))

    boundaries: list[tuple[int, int]] = []
    start = 0
    while total_frames - start > longest:
        low = start + shortest
        high = min(start + longest, total_frames - shortest) - probe
        cut = start + longest
        if high > low:
            quietest = None
            for pos in range(low, high + 1, max(1, probe // 2)):
                energy = float(abs(audio[pos : pos + probe]).mean())
                if quietest is None or energy < quietest:
                    quietest = energy
                    cut = pos
        boundaries.append((start, cut))
        start = cut
    boundaries.append((start, total_frames))
    return boundaries


def onnx_asr_quantization(onnx_asr_id: str, on_gpu: bool) -> str | None:
    """The weight precision the engine loads for a model: None is fp32.

    A GPU always loads fp32. int8 is a CPU optimisation and defeats CUDA:
    onnxruntime has no CUDA kernels for most quantized ops, so it claims the
    session and then hands nearly every node back to CPU, inserting a memcpy
    at each handoff (1046 of them for canary-1b). The fp32 weights cut that to
    66 and keep the graph on the card. On a CPU each model has its own
    precision (onnx_asr_cache.ONNX_ASR_MODELS), and one outside that table
    loads int8. Model preparation uses this too, so it fetches the files the
    engine will open on the same lane.
    """
    if on_gpu:
        return None
    model = onnx_asr_model(onnx_asr_id)
    return model.cpu_quantization if model else "int8"


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
    supports_forced_language: bool = False

    def __init__(self) -> None:
        # Cache for loaded models, keyed by onnx-asr model id.
        self._model_cache: dict = {}
        # Whether every session of a cached model runs on CUDA, by onnx-asr id.
        self._model_on_gpu: dict[str, bool] = {}

    def _to_onnx_asr_id(self, nojoin_id: str) -> str:
        """Resolve a Nojoin model id to its onnx-asr equivalent."""
        return self.onnx_id_map.get(nojoin_id, nojoin_id)

    def _onnx_id_for(self, config: dict) -> str:
        nojoin_id = (
            config.get(self.config_key, self.default_model_id)
            if config
            else self.default_model_id
        )
        return self._to_onnx_asr_id(nojoin_id)

    def _runs_on_gpu(self, config: dict) -> bool:
        """Whether the model for ``config`` runs on CUDA.

        Once loaded, that is what its sessions report: onnxruntime silently falls
        back to CPU when the CUDA provider cannot load. Before then it is what
        loading asks for, which is CUDA exactly when a GPU is present.
        """
        return self._model_on_gpu.get(self._onnx_id_for(config), gpu_is_present())

    def _get_model(self, config: dict):
        """Load (once, lazily) and return the onnx-asr model for the given config."""
        onnx_id = self._onnx_id_for(config)
        if onnx_id not in self._model_cache:
            import onnx_asr

            on_gpu = gpu_is_present()
            # Ask for CUDA only where a GPU is actually attached. Requesting it
            # without one does not degrade gracefully: onnxruntime-gpu loads its
            # CUDA provider library, finds no device, and takes the process down
            # with SIGSEGV. That is a native fault rather than an exception, so no
            # handler here can catch it and fall back.
            #
            # The GPU lane normally has a device, but the CPU-only deployment in
            # docs/DEPLOYMENT.md drops the compose `deploy` block while keeping the
            # same onnxruntime-gpu image, which is exactly the crashing shape.
            providers = ["CPUExecutionProvider"]
            if on_gpu:
                providers.insert(0, "CUDAExecutionProvider")
            quantization = onnx_asr_quantization(onnx_id, on_gpu)
            logger.info(
                f"Loading {self.name} model: {onnx_id} "
                f"(quantization={quantization or 'none'})"
            )
            model = onnx_asr.load_model(
                onnx_id,
                quantization=quantization,
                providers=providers,
                sess_options=onnx_session_options(),
            )
            # onnxruntime silently drops a provider it cannot load, so confirm the
            # session actually landed on the GPU instead of assuming it did.
            verify_gpu_providers(
                model, component=f"{self.name} model {onnx_id}", requested=providers
            )
            self._model_on_gpu[onnx_id] = sessions_use_cuda(model, requested=providers)
            self._model_cache[onnx_id] = model
            logger.info(f"{self.name} model {onnx_id} loaded successfully.")
        return self._model_cache[onnx_id]

    def transcribe(self, audio_path: str, config: dict) -> dict:
        """Transcribe the given audio file using onnx-asr.

        Audio longer than MAX_CHUNK_DURATION_S is transcribed window by window
        (onnx-asr has no long-form support); shorter audio goes through a single
        recognize() call.

        Args:
            audio_path: Path to a 16kHz mono WAV file.
            config: Configuration dictionary (reads the engine's config_key).

        Returns:
            The canonical transcription dict; empty text means no speech.

        Raises:
            TranscriptionError: The file is missing or onnx-asr failed.
        """
        import os

        if not os.path.exists(audio_path):
            logger.error(f"Audio file not found for transcription: {audio_path}")
            raise TranscriptionError(
                f"Transcription failed ({self.name}): the audio file is missing.",
                engine=self.name,
            )

        model = recognizer = None
        try:
            model = self._get_model(config or {})
            recognizer = model.with_timestamps()
            language_code = (
                resolve_transcription_language_code(config, self.name)
                if self.supports_forced_language
                else None
            )

            # Audio duration is optional; failure to read it must not abort, but
            # without it the audio cannot be safely chunked (single-pass only).
            audio_duration: float | None = None
            try:
                import soundfile

                audio_duration = soundfile.info(audio_path).duration
            except Exception as e:  # noqa: BLE001 -- boundary: the duration only enables chunking; without it the audio is transcribed in one pass
                logger.warning(f"Could not read audio duration for {audio_path}: {e}")

            logger.info(f"Starting {self.name} transcription for {audio_path}")
            word_end_pad_s = resolve_tuning(config, ASR_WORD_END_PADDING_KEY)

            if audio_duration is not None and audio_duration > MAX_CHUNK_DURATION_S:
                result = self._transcribe_chunked(
                    recognizer,
                    audio_path,
                    language_code=language_code,
                    word_end_pad_s=word_end_pad_s,
                )
            else:
                recognize_kwargs = {"language": language_code} if language_code else {}
                recognized = recognizer.recognize(audio_path, **recognize_kwargs)
                silence: list[float] = []
                # Only token timings have ends to bound (Canary has none).
                if recognized.timestamps:
                    silence = self._file_silence_starts(audio_path)
                result = map_onnx_asr_recognition(
                    recognized, audio_duration, language_code, word_end_pad_s, silence
                )

            logger.info(f"{self.name} transcription completed for {audio_path}")
            return result

        except Exception as e:
            if is_task_interruption(e):
                raise
            logger.error(
                f"Error during {self.name} transcription for {audio_path}: {e}",
                exc_info=True,
            )
            failure = transcription_error_from(
                e, engine=self.name, on_gpu=self._runs_on_gpu(config or {})
            )
            if is_out_of_memory(e):
                # onnxruntime's arena grows to the largest window and never
                # shrinks, so a session that ran out of memory would run out
                # again on the next call. Drop every reference to it (the cache,
                # this frame's locals, and the locals of the frames the
                # traceback keeps alive) and collect, so its memory is freed
                # before the next call reloads it.
                self.release()
                model = recognizer = None
                traceback.clear_frames(e.__traceback__)
                gc.collect()
            raise failure from e

    def _file_silence_starts(self, audio_path: str) -> list[float]:
        """The silences in a whole file, or none when it cannot be read.

        The words are already recognized, so a read failure only loses the
        silence bound on their ends, not the transcript.
        """
        try:
            audio, sample_rate = _read_mono(audio_path)
        except (OSError, RuntimeError, ValueError) as e:
            logger.warning(
                f"{self.name}: could not read {audio_path} for silences: {e}"
            )
            return []
        return _silence_starts(audio, 0, len(audio), sample_rate)

    def _transcribe_chunked(
        self,
        recognizer,
        audio_path: str,
        *,
        language_code: str | None = None,
        word_end_pad_s: float = WORD_END_PAD_S,
    ) -> dict:
        """Transcribe long audio as a sequence of windows, then merge.

        Windows follow _chunk_boundaries, and one with no signal in it is not
        transcribed. Per-window segments are shifted into absolute time and
        concatenated.
        """
        import os
        import tempfile

        import soundfile

        audio, sample_rate = _read_mono(audio_path)
        boundaries = _chunk_boundaries(audio, sample_rate)
        audio_duration = len(audio) / sample_rate
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
            if float(abs(chunk).max()) < SILENT_WINDOW_PEAK:
                logger.info(
                    f"{self.name}: window {index + 1}/{len(boundaries)} is silent; "
                    f"skipped ({offset_s:.0f}s +{chunk_duration:.0f}s)."
                )
                continue

            handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            chunk_path = handle.name
            handle.close()
            try:
                soundfile.write(chunk_path, chunk, sample_rate)
                recognize_kwargs = {"language": language_code} if language_code else {}
                recognized = recognizer.recognize(chunk_path, **recognize_kwargs)
            finally:
                try:
                    os.remove(chunk_path)
                except OSError:
                    pass

            chunk_result = map_onnx_asr_recognition(
                recognized,
                chunk_duration,
                language_code,
                word_end_pad_s,
                _silence_starts(audio, start_frame, end_frame, sample_rate),
            )
            if chunk_result["text"]:
                texts.append(chunk_result["text"])
            segments.extend(
                _shift_segment(s, offset_s) for s in chunk_result["segments"]
            )
            logger.info(
                f"{self.name}: window {index + 1}/{len(boundaries)} done "
                f"({offset_s:.0f}s +{chunk_duration:.0f}s)."
            )

        return {
            "text": " ".join(texts),
            "language": language_code,
            "segments": segments,
        }

    def release(self) -> None:
        """Release all loaded models from memory."""
        if self._model_cache:
            logger.info(
                f"Releasing {list(self._model_cache.keys())} from {self.name} model cache."
            )
            self._model_cache.clear()
        self._model_on_gpu.clear()
