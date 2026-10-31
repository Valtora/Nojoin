# Whisper transcription engine. Heavy imports (whisper, torch, tqdm) live here,
# not in the thin dispatcher backend/processing/transcribe.py.

import gc
import logging
import os
import threading
import traceback
import warnings
from typing import Literal

import numpy as np
import soundfile
import torch
import tqdm
import whisper

from ...utils.config_manager import config_manager
from ...utils.languages import resolve_transcription_language_code
from ...utils.model_cache_paths import whisper_cache_root
from .base import CAPTURE_LANGUAGE, LANGUAGE_DETECTION_AUDIO, TranscriptionEngine
from .errors import (
    TranscriptionError,
    is_task_interruption,
    transcription_error_from,
)

logger = logging.getLogger(__name__)

warnings.filterwarnings(
    "ignore",
    message=r".*Failed to launch Triton kernels.*",
    category=UserWarning,
)

# Cache for loaded models to avoid reloading
_model_cache = {}

# Whisper re-decodes a 30-second window by sampling at a raised temperature when
# its greedy output fails the compression-ratio or log-probability threshold.
# Unseeded, that sampling makes reprocessing the same recording give different
# text wherever a window falls back. Any fixed value works; it only has to stay
# the same between runs.
FALLBACK_SAMPLING_SEED = 0


def _transcribe_seeded(model, audio_path: str, device: str, **kwargs) -> dict:
    """Run model.transcribe with its fallback sampling seeded.

    fork_rng restores the global RNG state afterwards, so the seed reaches only
    this call and not whatever else the worker samples.
    """
    # Fork the CUDA generator only when there is one. Asking for the current
    # device with no driver raises, and the error then belongs to the model call.
    use_cuda = device == "cuda" and torch.cuda.is_available()
    devices = [torch.cuda.current_device()] if use_cuda else []
    with torch.random.fork_rng(devices=devices):
        torch.manual_seed(FALLBACK_SAMPLING_SEED)
        return model.transcribe(audio_path, **kwargs)


# Each silence in the detection window is cut to this many samples (100 ms)
# rather than closed up: Whisper hears speech with short pauses more surely than
# speech spliced end to end. On 13 English recordings it raised the top-language
# probability on 10, lowered it on none, and got every language right (#673).
DETECTION_GAP_SAMPLES = whisper.audio.SAMPLE_RATE // 10


def _cap_gaps(block: np.ndarray, run: int) -> tuple[np.ndarray, int]:
    """``block`` with every run of exact zeros cut to DETECTION_GAP_SAMPLES.

    ``run`` is the length of the zero run the previous block ended on, so a
    silence across two blocks is cut once. Returns the kept samples and the
    length of the zero run this block ends on.
    """
    index = np.arange(block.size)
    last_sound = np.maximum.accumulate(np.where(block != 0, index, -1))
    zeros_so_far = np.where(last_sound < 0, index + 1 + run, index - last_sound)
    keep = (block != 0) | (zeros_so_far <= DETECTION_GAP_SAMPLES)
    ends_silent = block.size > 0 and block[-1] == 0
    return block[keep], int(zeros_so_far[-1]) if ends_silent else 0


def _first_speech(muted_path: str) -> np.ndarray:
    """The first 30 s of a VAD-muted file with every silence cut to 100 ms.

    Every sample VAD dropped is an exact zero there, so what is left is the
    speech, in order, with short pauses. Reads only as far as it needs.
    """
    window = whisper.audio.N_SAMPLES
    speech: list[np.ndarray] = []
    found = run = 0
    for block in soundfile.blocks(muted_path, blocksize=window, dtype="float32"):
        kept, run = _cap_gaps(block, run)
        speech.append(kept)
        found += kept.size
        if found >= window:
            break
    return np.concatenate(speech)[:window]


def _detect_language_on_speech(model, config: dict | None) -> tuple[str, float] | None:
    """The language Whisper hears in the first 30 s of speech and its
    probability, or None (#671).

    Whisper detects an automatic language on the first 30 s of the file it
    transcribes. In the unmuted file those can be room noise, which on far-field
    meetings came out as Japanese for English speech; in the muted one they can
    be mostly zeros, which gave English only narrowly. So whenever VAD ran,
    detect on the first 30 s of its speech instead, whichever file the engine
    reads. With VAD off there is no speech map: return None and Whisper detects
    on its own input, as before. So does any failure here: detection only picks
    or checks the language, and must never fail a transcription that would have
    run. It runs with a language set too, so the recording page can say when
    the speech sounds like another one.
    """
    muted_path = (config or {}).get(LANGUAGE_DETECTION_AUDIO)
    if not muted_path or not model.is_multilingual:
        return None
    try:
        speech = _first_speech(muted_path)
        if not speech.any():
            # VAD counted speech the mute step then zeroed (the first and last
            # second are always muted): nothing to detect on.
            logger.info(
                "No VAD speech in %s to detect a language on; Whisper detects "
                "on its own input instead",
                muted_path,
            )
            return None
        mel = whisper.log_mel_spectrogram(
            whisper.pad_or_trim(speech), model.dims.n_mels
        )
        _, probs = model.detect_language(mel.to(model.device))
    except (ValueError, RuntimeError, OSError) as e:
        # soundfile's LibsndfileError is a RuntimeError: caught here, it never
        # reaches the caller's RuntimeError branch that drops the cached model.
        logger.warning(
            "Language detection on VAD speech in %s failed (%s); Whisper detects "
            "on its own input instead",
            muted_path,
            e,
        )
        return None
    language = max(probs, key=probs.get)
    logger.info(
        "Detected language %s (p=%.2f) on the first %.1f s of VAD speech in %s",
        language,
        probs[language],
        speech.size / whisper.audio.SAMPLE_RATE,
        muted_path,
    )
    return language, float(probs[language])


def _choose_language(
    model, config: dict | None, engine: str
) -> tuple[str | None, dict | None]:
    """The language to transcribe in, and what detection heard, if it ran.

    Detection runs even when a language is set, to check it (#671): the set
    language is still the one transcribed in, and on automatic the detected
    one is.
    """
    language = resolve_transcription_language_code(config, engine)
    capture_language = (config or {}).get(CAPTURE_LANGUAGE)
    if not language and capture_language:
        # The live capture's language is already known: no detection (#776).
        return str(capture_language), None
    try:
        detection = _detect_language_on_speech(model, config)
    except Exception as e:  # noqa: BLE001 -- boundary: with a language set, detection only checks it
        # On automatic it picks the language, so its own narrower catch stands
        # and anything else still fails the run, as it did before the check.
        if not language or is_task_interruption(e):
            raise
        logger.warning(
            "Language detection failed (%s); transcribing in the set language %s",
            e,
            language,
        )
        detection = None
    if detection is None:
        return language, None
    code, probability = detection
    record = {
        "detected": code,
        "detected_label": whisper.tokenizer.LANGUAGES.get(code, code).title(),
        "probability": round(probability, 3),
    }
    return language or code, record


# --- Whisper Progress Listener Infrastructure ---
class ProgressListener:
    def __init__(self, callback):
        self.callback = callback

    def on_progress(self, current, total):
        percent = int((current / total) * 100) if total else 0
        if self.callback:
            self.callback(min(percent, 100))

    def on_finished(self):
        if self.callback:
            self.callback(100)


class ProgressListenerHandle:
    def __init__(self, listener):
        self.listener = listener

    def __enter__(self):
        register_thread_local_progress_listener(self.listener)

    def __exit__(self, exc_type, exc_val, exc_tb):
        unregister_thread_local_progress_listener(self.listener)
        if exc_type is None:
            self.listener.on_finished()


class _CustomProgressBar(tqdm.tqdm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._current = self.n

    def update(self, n):
        super().update(n)
        self._current += n
        listeners = _get_thread_local_listeners()
        for listener in listeners:
            listener.on_progress(self._current, self.total)


_thread_local = threading.local()
_hooked = False


def _get_thread_local_listeners():
    if not hasattr(_thread_local, "listeners"):
        _thread_local.listeners = []
    return _thread_local.listeners


def init_progress_hook():
    global _hooked
    if _hooked:
        return
    import tqdm

    tqdm.tqdm = _CustomProgressBar
    _hooked = True


def register_thread_local_progress_listener(progress_listener):
    init_progress_hook()
    listeners = _get_thread_local_listeners()
    listeners.append(progress_listener)


def unregister_thread_local_progress_listener(progress_listener):
    listeners = _get_thread_local_listeners()
    if progress_listener in listeners:
        listeners.remove(progress_listener)


def create_progress_listener_handle(progress_listener):
    return ProgressListenerHandle(progress_listener)


def transcribe_audio_with_progress(
    audio_path: str, progress_callback=None, cancel_check=None
) -> dict | None:
    """Transcribes the given audio file using OpenAI Whisper, emitting progress via callback. Supports cancellation."""
    if not os.path.exists(audio_path):
        logger.error(f"Audio file not found for transcription: {audio_path}")
        return None

    model_size = config_manager.get("whisper_model_size", "base")
    device = config_manager.get("processing_device", "cpu")

    logger.info(
        f"Starting transcription for {audio_path} using model: {model_size}, device: {device}"
    )

    from backend.utils.audio import ensure_ffmpeg_in_path

    ensure_ffmpeg_in_path()

    try:
        if model_size not in _model_cache:
            logger.info(f"Loading Whisper model: {model_size}")
            # Check if model exists locally, if not it will be downloaded by whisper.load_model
            from ...utils.model_utils import is_whisper_model_downloaded

            if not is_whisper_model_downloaded(model_size):
                logger.info(
                    f"Whisper model {model_size} not found locally - will be downloaded"
                )
            _model_cache[model_size] = whisper.load_model(
                model_size, device=device, download_root=whisper_cache_root()
            )
            logger.info(f"Whisper model {model_size} loaded successfully.")
        model = _model_cache[model_size]

        try:
            use_fp16 = device == "cuda"

            if cancel_check and cancel_check():
                logger.info("Transcription cancelled before starting model.transcribe.")
                return None

            # Word timestamps default on, except on Windows;
            # WHISPER_ENABLE_WORD_TIMESTAMPS overrides.
            env_var = os.environ.get("WHISPER_ENABLE_WORD_TIMESTAMPS")
            if env_var is not None:
                enable_word_timestamps = env_var.lower() == "true"
            else:
                import platform

                enable_word_timestamps = platform.system().lower() != "windows"

            result = _transcribe_seeded(
                model,
                audio_path,
                device,
                fp16=use_fp16,
                verbose=None,
                word_timestamps=enable_word_timestamps,
            )

            if cancel_check and cancel_check():
                logger.info("Transcription cancelled after model.transcribe.")
                return None

            if progress_callback:
                progress_callback(100)

            logger.info(
                f"Transcription completed for {audio_path}. Detected language: {result.get('language')}"
            )
            return result

        except Exception as e:
            logger.error(f"Error during transcription: {e}", exc_info=True)
            raise

    except Exception as e:
        logger.error(
            f"Error during Whisper transcription for {audio_path}: {e}", exc_info=True
        )
        if model_size in _model_cache and isinstance(e, RuntimeError):
            logger.warning(f"Clearing model cache for {model_size} due to error.")
            del _model_cache[model_size]
            if device == "cuda":
                torch.cuda.empty_cache()
        return None


class WhisperEngine(TranscriptionEngine):
    """OpenAI Whisper transcription engine."""

    name = "whisper"

    def loaded_device(self, config: dict) -> Literal["cpu", "cuda"] | None:
        """Read the selected cached model's device, independently of configuration."""
        model = _model_cache.get(config.get("whisper_model_size", "base"))
        device = str(getattr(model, "device", "")).split(":", 1)[0]
        if device == "cuda":
            return "cuda"
        if device == "cpu":
            return "cpu"
        return None

    def transcribe(self, audio_path: str, config: dict) -> dict:
        """Transcribes the given audio file using OpenAI Whisper.

        Args:
            audio_path: Path to the audio file (e.g., MP3).
            config: Optional configuration dictionary to override defaults.

        Returns:
            A dictionary containing the transcription result (including text,
            segments, language); empty text means no speech.

        Raises:
            TranscriptionError: The file is missing or Whisper failed.
        """
        if not os.path.exists(audio_path):
            logger.error(f"Audio file not found for transcription: {audio_path}")
            raise TranscriptionError(
                f"Transcription failed ({self.name}): the audio file is missing.",
                engine=self.name,
            )

        get_config = config.get if config else config_manager.get
        model_size = get_config("whisper_model_size", "base")
        device = get_config("processing_device", "auto")

        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"

        logger.info(
            f"Starting transcription for {audio_path} using model: {model_size}, device: {device}"
        )

        from backend.utils.audio import ensure_ffmpeg_in_path

        ensure_ffmpeg_in_path()

        model = None
        try:
            if model_size not in _model_cache:
                logger.info(f"Loading Whisper model: {model_size}")

                # The root model status and preparation use, from one resolver.
                _model_cache[model_size] = whisper.load_model(
                    model_size, device=device, download_root=whisper_cache_root()
                )
                logger.info(f"Whisper model {model_size} loaded successfully.")
            model = _model_cache[model_size]

            use_fp16 = device == "cuda"

            # Word timestamps default on, except on Windows;
            # WHISPER_ENABLE_WORD_TIMESTAMPS overrides.
            env_var = os.environ.get("WHISPER_ENABLE_WORD_TIMESTAMPS")

            if env_var is not None:
                enable_word_timestamps = env_var.lower() == "true"
                logger.info(
                    f"Word timestamps set via env var: {enable_word_timestamps}"
                )
            else:
                import platform

                is_windows = platform.system().lower() == "windows"
                enable_word_timestamps = not is_windows
                logger.info(
                    f"Word timestamps auto-detected (Windows={is_windows}): {enable_word_timestamps}"
                )

            # condition_on_previous_text=False helps prevent hallucinations (e.g. "Thank you")
            # especially when there are silence gaps or non-speech segments.
            transcribe_kwargs = {
                "fp16": use_fp16,
                "word_timestamps": enable_word_timestamps,
                "condition_on_previous_text": False,
            }
            language_code, detection = _choose_language(model, config, self.name)
            if language_code:
                transcribe_kwargs["language"] = language_code

            result = _transcribe_seeded(model, audio_path, device, **transcribe_kwargs)
            if detection:
                # The model too, so "Reprocess in <language>" keeps it.
                result["language_detection"] = {**detection, "model": model_size}

            logger.info(
                f"Transcription completed for {audio_path}. Detected language: {result.get('language')}"
            )

            return result

        except Exception as e:
            if is_task_interruption(e):
                raise
            logger.error(
                f"Error during Whisper transcription for {audio_path}: {e}",
                exc_info=True,
            )

            if model_size in _model_cache and isinstance(
                e, RuntimeError
            ):  # e.g., CUDA out of memory
                logger.warning(f"Clearing model cache for {model_size} due to error.")
                del _model_cache[model_size]
                # The traceback's frames still hold the model; drop those
                # references too, or emptying the CUDA cache frees nothing.
                model = None
                traceback.clear_frames(e.__traceback__)
                gc.collect()
                if device == "cuda":
                    torch.cuda.empty_cache()
            raise transcription_error_from(
                e, engine=self.name, on_gpu=device == "cuda"
            ) from e

    def release(self) -> None:
        """Releases all loaded Whisper models from memory and clears CUDA cache."""
        global _model_cache
        if _model_cache:
            logger.info(f"Releasing {_model_cache.keys()} from Whisper model cache.")
            _model_cache.clear()

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
