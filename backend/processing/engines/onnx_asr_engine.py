# Shared base for onnx-asr-backed transcription engines (Parakeet, Canary).
# Heavy imports (onnx_asr, soundfile) live inside methods, never at module top.

import gc
import logging
import os
import re
import traceback
from collections.abc import Sequence
from typing import NamedTuple, Protocol

from ...utils.languages import resolve_transcription_language_code
from ...utils.onnx_asr_cache import onnx_asr_model
from ...utils.word_times import exceeds
from ..onnx_providers import gpu_is_present, sessions_use_cuda, verify_gpu_providers
from ..onnx_threads import onnx_session_options
from ..processing_tuning import (
    ASR_WORD_LENGTH_CAP_KEY,
    TUNING_SPECS,
    resolve_tuning,
)
from . import onnx_asr_backstop as backstop
from .base import VAD_MUTED_INPUT, TranscriptionEngine
from .errors import (
    TranscriptionError,
    is_out_of_memory,
    is_task_interruption,
    transcription_error_from,
)

logger = logging.getLogger(__name__)

os.environ.setdefault("ORT_LOG_SEVERITY_LEVEL", "1")

# Gap (seconds) between consecutive words that starts a new segment. A gap
# equal to it, as two starts the cap plus 0.8 s apart leave, does not.
SEGMENT_PAUSE_THRESHOLD_S = 0.8

# The longest (seconds) a word lasts. onnx-asr returns only token start times,
# so a word ends at the earliest of the next word's start, the end of the
# window, and its start plus this. A user's settings may override it
# (processing_tuning.py).
WORD_LENGTH_CAP_S = TUNING_SPECS[ASR_WORD_LENGTH_CAP_KEY].default

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

# Longest call (seconds) that decodes a window's skipped speech again
# (onnx_asr_backstop). A longer hole is split like a window, so the retry
# cannot itself run long enough to skip speech.
HOLE_REDECODE_WINDOW_S = 20.0

# The spacing rule onnx-asr applies to the text it decodes from its tokens.
_DECODE_SPACE_PATTERN = re.compile(r"\A\s|\s\B|(\s)\b")


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


class _Recognizer(Protocol):
    """An onnx-asr model with timestamps: what with_timestamps() returns."""

    def recognize(self, path: str, **kwargs: str) -> Recognition: ...


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
    word_length_cap_s: float = WORD_LENGTH_CAP_S,
) -> dict:
    """Map an onnx-asr timestamped result into the canonical transcription schema.

    onnx-asr gives each token a start time only, so a word ends at the earliest
    of the next word's start, audio_duration and its start plus
    word_length_cap_s, but never before it starts. Without token timings
    (Canary) the text becomes one segment spanning the audio.

    Args:
        recognized: The recognize() result: text, plus parallel subword tokens
            and per-token start times in seconds (either may be None).
        audio_duration: Total audio duration in seconds, optional.
        language: The forced language code, if any.
        word_length_cap_s: The longest a word lasts.

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
        bounds = [start + word_length_cap_s]
        if index + 1 < len(words):
            bounds.append(words[index + 1]["start"])
        if audio_duration is not None:
            bounds.append(audio_duration)
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
        if exceeds(word["start"] - current[-1]["end"], SEGMENT_PAUSE_THRESHOLD_S):
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


def _join_tokens(tokens: Sequence[str]) -> str:
    """The text of decoded tokens, spaced exactly as onnx-asr spaces its own."""
    return _DECODE_SPACE_PATTERN.sub(
        lambda match: " " if match.group(1) else "", "".join(tokens)
    )


def _words_of(result: dict) -> list[dict]:
    """Every timed word of a canonical result, in segment order."""
    return [word for segment in result["segments"] for word in segment.get("words", [])]


class _Decoder(NamedTuple):
    """How one transcription decodes a stretch of audio into a canonical result."""

    recognizer: _Recognizer
    sample_rate: int
    language_code: str | None
    word_length_cap_s: float

    def recognize(self, samples) -> Recognition:
        """recognize() the samples, written to a temporary WAV for the call."""
        import tempfile

        import soundfile

        handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        path = handle.name
        handle.close()
        try:
            soundfile.write(path, samples, self.sample_rate)
            kwargs = {"language": self.language_code} if self.language_code else {}
            return self.recognizer.recognize(path, **kwargs)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def to_result(self, recognized: Recognition, frames: int) -> dict:
        """Map a result for ``frames`` samples of audio into the canonical schema."""
        return map_onnx_asr_recognition(
            recognized,
            frames / self.sample_rate,
            self.language_code,
            self.word_length_cap_s,
        )


class _Redecoded(NamedTuple):
    """A window after its holes were decoded again."""

    result: dict
    # Calls of the first retry.
    calls: int
    second: backstop.SecondTry
    # Holes (frames) that still hold no word once every retry's words are in.
    wordless: list[tuple[int, int]]


class _Heard(NamedTuple):
    """One retry call's result, and where its piece starts in the window."""

    offset_s: float
    frames: int
    recognized: Recognition


def _decode_again(
    decoder: _Decoder, chunk, first: int, end: int
) -> tuple[int, list[_Heard]]:
    """Decode ``chunk[first:end]`` in fresh calls of HOLE_REDECODE_WINDOW_S at most.

    A longer stretch is split like a window. A call that fails adds nothing,
    unless it ran out of memory or the task was stopped.

    Returns:
        How many calls it took, and what each call that did not fail heard.
    """
    calls = 0
    heard: list[_Heard] = []
    for begin, stop in _chunk_boundaries(
        chunk[first:end], decoder.sample_rate, HOLE_REDECODE_WINDOW_S
    ):
        piece = chunk[first + begin : first + stop]
        calls += 1
        offset_s = (first + begin) / decoder.sample_rate
        try:
            recognized = decoder.recognize(piece)
        except Exception as error:
            # Running out of memory keeps transcribe()'s handling, and a
            # task interruption must reach Celery.
            if is_out_of_memory(error) or is_task_interruption(error):
                raise
            logger.warning(
                f"A retry of {len(piece) / decoder.sample_rate:.1f}s at "
                f"{offset_s:.1f}s into a window failed; the window keeps "
                f"the words found there so far: {error}",
                exc_info=True,
            )
            continue
        heard.append(_Heard(offset_s, len(piece), recognized))
    return calls, heard


def _try_again_from_word_before(
    decoder: _Decoder,
    chunk,
    timings: Sequence[backstop.WordTiming],
    wordless: list[tuple[int, int]],
) -> tuple[list[backstop.WordTiming], list[dict], backstop.SecondTry]:
    """The second and last try at the holes the first retry left wordless.

    Each hole is decoded once more from backstop.second_try_start(), found
    from ``timings`` (the window's and its first retry's), so the call opens
    on the word before the hole. Words starting before the hole's first kept
    sample are that word decoded again, and are dropped. A hole with no word
    before it is not tried. This is never called on its own output: a hole
    gets two retries at most (onnx_asr_backstop).

    Returns:
        The kept words' timings and words, in window time, and the counts.
    """
    rate = decoder.sample_rate
    kept_timings: list[backstop.WordTiming] = []
    kept: list[dict] = []
    tried: list[tuple[int, int]] = []
    no_previous_word: list[tuple[int, int]] = []
    calls = found_in = 0
    for begin, end in wordless:
        start = backstop.second_try_start(timings, begin, rate)
        if start is None:
            no_previous_word.append((begin, end))
            continue
        tried.append((begin, end))
        hole_calls, heard = _decode_again(decoder, chunk, start, end)
        calls += hole_calls
        found = len(kept)
        for offset_s, frames, recognized in heard:
            kept_timings.extend(
                backstop.WordTiming(word.start + offset_s, word.last_token + offset_s)
                for word in backstop.word_timings(recognized)
                if round((word.start + offset_s) * rate) >= begin
            )
            kept.extend(
                {"start": word["start"] + offset_s, "word": word["word"]}
                for word in _words_of(decoder.to_result(recognized, frames))
                if round((word["start"] + offset_s) * rate) >= begin
            )
        if len(kept) > found:
            found_in += 1
    second = backstop.SecondTry(tried, calls, found_in, len(kept), no_previous_word)
    return kept_timings, kept, second


def _redecode_holes(
    decoder: _Decoder,
    chunk,
    chunk_result: dict,
    timings: Sequence[backstop.WordTiming],
    holes: list[tuple[int, int]],
) -> _Redecoded:
    """Decode a window's holes again, each in fresh calls, and merge their words.

    A fresh recognize() call restarts the decoder, which recovers speech the
    window's own call skipped. A window is too short to hold a hole that
    splits in more than two, and a hole starts and ends on kept audio, so no
    piece is silent. A hole still wordless after it gets one second try
    (_try_again_from_word_before). The merged words are mapped again over the
    window, so word ends and segments follow the mapper's usual rules. A retry
    result without token timings adds nothing.

    ``timings`` are the window's own word timings; with the retries' added,
    they find the holes that are still wordless. Those the second try leaves
    are only counted.

    Returns:
        The window's merged result, the calls of its first retry, what its
        second tries did, and the holes still wordless after both.
    """
    words = [
        {"start": word["start"], "word": word["word"]}
        for word in _words_of(chunk_result)
    ]
    timings = list(timings)
    calls = 0
    for first, end in holes:
        hole_calls, heard = _decode_again(decoder, chunk, first, end)
        calls += hole_calls
        for offset_s, frames, recognized in heard:
            timings.extend(
                backstop.WordTiming(word.start + offset_s, word.last_token + offset_s)
                for word in backstop.word_timings(recognized)
            )
            words.extend(
                {"start": word["start"] + offset_s, "word": word["word"]}
                for word in _words_of(decoder.to_result(recognized, frames))
            )
    wordless = backstop.survey(chunk, decoder.sample_rate, sorted(timings)).holes
    kept_timings, kept, second = _try_again_from_word_before(
        decoder, chunk, timings, wordless
    )
    if second.tried:
        timings.extend(kept_timings)
        words.extend(kept)
        wordless = backstop.survey(chunk, decoder.sample_rate, sorted(timings)).holes
    words.sort(key=lambda word: word["start"])
    merged = _Recognition(
        _join_tokens([word["word"] for word in words]),
        [word["word"] for word in words],
        [word["start"] for word in words],
    )
    return _Redecoded(decoder.to_result(merged, len(chunk)), calls, second, wordless)


def _chunk_boundaries(
    audio, sample_rate: int, longest_s: float | None = None
) -> list[tuple[int, int]]:
    """Split [0, len(audio)) into windows of ``longest_s`` at most.

    ``longest_s`` defaults to MAX_CHUNK_DURATION_S. Every window but a lone
    one lasts at least half of it, and closes at the
    first of the quietest 0.2 s spans after its midpoint, so a cut never
    falls inside a word and the next window opens on the pause. A long pause
    is never cut into slivers of silence, and the last cut leaves the final
    window at least half as well. When less than ``longest`` plus 0.2 s is
    left, two windows of half cannot both fit, and the last cut is the first
    of the quietest spans that keep both within ``longest``. Returns
    (start_frame, end_frame) pairs covering the whole signal.
    """
    total_frames = len(audio)
    longest = int((longest_s or MAX_CHUNK_DURATION_S) * sample_rate)
    shortest = longest // 2
    probe = max(1, int(0.2 * sample_rate))

    boundaries: list[tuple[int, int]] = []
    start = 0
    while total_frames - start > longest:
        low = start + shortest
        high = min(start + longest, total_frames - shortest) - probe
        if high <= low:
            low, high = total_frames - longest, start + longest - probe
        cut = start + longest
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
    # Whether a window's skipped speech is decoded again on VAD-muted input
    # (onnx_asr_backstop). Finding it takes token timings, which onnx-asr
    # returns for Parakeet and never for Canary.
    recovers_skipped_speech: bool = False

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
        recognize() call. When the engine recovers skipped speech and the
        config marks the audio VAD-muted (VAD_MUTED_INPUT), every input goes
        through the window loop, a short one as a single window, and each
        window's skipped speech is decoded once more (_redecode_holes).

        Args:
            audio_path: Path to a 16kHz mono WAV file.
            config: Configuration dictionary (reads the engine's config_key and
                VAD_MUTED_INPUT).

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
            word_length_cap_s = resolve_tuning(config, ASR_WORD_LENGTH_CAP_KEY)

            recover_skipped = self.recovers_skipped_speech and bool(
                (config or {}).get(VAD_MUTED_INPUT)
            )
            if audio_duration is not None and (
                audio_duration > MAX_CHUNK_DURATION_S or recover_skipped
            ):
                result = self._transcribe_chunked(
                    recognizer,
                    audio_path,
                    language_code=language_code,
                    word_length_cap_s=word_length_cap_s,
                    recover_skipped=recover_skipped,
                )
            else:
                recognize_kwargs = {"language": language_code} if language_code else {}
                recognized = recognizer.recognize(audio_path, **recognize_kwargs)
                result = map_onnx_asr_recognition(
                    recognized, audio_duration, language_code, word_length_cap_s
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

    def _transcribe_chunked(
        self,
        recognizer,
        audio_path: str,
        *,
        language_code: str | None = None,
        word_length_cap_s: float = WORD_LENGTH_CAP_S,
        recover_skipped: bool = False,
    ) -> dict:
        """Transcribe long audio as a sequence of windows, then merge.

        Windows follow _chunk_boundaries, and one with no signal in it is not
        transcribed. Per-window segments are shifted into absolute time and
        concatenated. With ``recover_skipped``, which the caller sets only for
        VAD-muted audio, each window's holes (onnx_asr_backstop.survey) are
        decoded once more and their words merged in; a retry is never retried.
        """
        import soundfile

        audio, sample_rate = soundfile.read(
            audio_path, dtype="float32", always_2d=False
        )
        if getattr(audio, "ndim", 1) > 1:
            audio = audio.mean(axis=1)

        boundaries = _chunk_boundaries(audio, sample_rate)
        decoder = _Decoder(recognizer, sample_rate, language_code, word_length_cap_s)
        audio_duration = len(audio) / sample_rate
        logger.info(
            f"{self.name}: transcribing {audio_duration:.0f}s audio in "
            f"{len(boundaries)} windows of at most {MAX_CHUNK_DURATION_S:.0f}s."
        )

        tally = backstop.Tally()
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

            recognized = decoder.recognize(chunk)
            chunk_result = decoder.to_result(recognized, len(chunk))
            # A result whose words cannot be placed keeps its text: missing
            # timing is not missing speech.
            placed = bool(_words_of(chunk_result)) or not chunk_result["text"]
            if recover_skipped and placed:
                timings = backstop.word_timings(recognized)
                found = backstop.survey(chunk, sample_rate, timings)
                tally.add_survey(found)
                if found.holes:
                    redone = _redecode_holes(
                        decoder, chunk, chunk_result, timings, found.holes
                    )
                    chunk_result = redone.result
                    tally.add_retry(
                        chunk, found.holes, redone.calls, redone.second, redone.wordless
                    )
                    logger.debug(
                        f"{self.name}: window {index + 1}/{len(boundaries)}: "
                        f"{len(found.holes)} holes decoded again in {redone.calls} "
                        f"calls, {len(redone.second.tried)} once more in "
                        f"{redone.second.calls}, {len(redone.wordless)} still "
                        f"wordless."
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

        if recover_skipped:
            # One line per recording; the counts ride on the record as well.
            logger.info(
                f"{self.name}: {tally.summary(sample_rate)}.",
                extra={"backstop_tally": tally, "sample_rate": sample_rate},
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
