# nojoin/processing/transcribe.py

import whisper
import logging
import os
import torch
import tqdm
import threading

from ..utils.config_manager import config_manager
# from ..utils.progress_manager import get_progress_manager

logger = logging.getLogger(__name__)

# Cache for loaded models to avoid reloading
_model_cache = {}

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
    if not hasattr(_thread_local, 'listeners'):
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

def transcribe_audio(audio_path: str) -> dict | None:
    """Transcribes the given audio file using OpenAI Whisper.

    Args:
        audio_path: Path to the audio file (e.g., MP3).

    Returns:
        A dictionary containing the transcription result (including text, segments, language)
        or None if transcription fails.
    """
    if not os.path.exists(audio_path):
        logger.error(f"Audio file not found for transcription: {audio_path}")
        return None

    model_size = config_manager.get("whisper_model_size", "base")
    device = config_manager.get("processing_device", "cpu")

    logger.info(f"Starting transcription for {audio_path} using model: {model_size}, device: {device}")

    # Ensure ffmpeg is in PATH
    from backend.utils.audio import ensure_ffmpeg_in_path
    ensure_ffmpeg_in_path()

    try:
        # Load model (use cache)
        if model_size not in _model_cache:
            logger.info(f"Loading Whisper model: {model_size}")
            _model_cache[model_size] = whisper.load_model(model_size, device=device)
            logger.info(f"Whisper model {model_size} loaded successfully.")
        model = _model_cache[model_size]

        # Perform transcription

        use_fp16 = device == "cuda" 
        
        # Check environment variable for word timestamps
        # If not explicitly set, auto-detect environment:
        # - Linux/Docker: Default to True (Triton works)
        # - Windows: Default to False (Triton crashes)
        env_var = os.environ.get("WHISPER_ENABLE_WORD_TIMESTAMPS")
        
        if env_var is not None:
            enable_word_timestamps = env_var.lower() == "true"
            logger.info(f"Word timestamps set via env var: {enable_word_timestamps}")
        else:
            # Auto-detect
            import platform
            is_windows = platform.system().lower() == "windows"
            enable_word_timestamps = not is_windows
            logger.info(f"Word timestamps auto-detected (Windows={is_windows}): {enable_word_timestamps}")

        # condition_on_previous_text=False helps prevent hallucinations (e.g. "Thank you")
        # especially when there are silence gaps or non-speech segments.
        result = model.transcribe(
            audio_path, 
            fp16=use_fp16, 
            word_timestamps=enable_word_timestamps,
            condition_on_previous_text=False
        )

        logger.info(f"Transcription completed for {audio_path}. Detected language: {result.get('language')}")
        # logger.debug(f"Transcription result: {result}") # Can be very verbose

        return result

    except Exception as e:
        logger.error(f"Error during Whisper transcription for {audio_path}: {e}", exc_info=True)

        if model_size in _model_cache and isinstance(e, RuntimeError): # e.g., CUDA out of memory
             logger.warning(f"Clearing model cache for {model_size} due to error.")
             del _model_cache[model_size]
             if device == "cuda":
                 torch.cuda.empty_cache()
        return None

def transcribe_audio_with_progress(audio_path: str, progress_callback=None, cancel_check=None) -> dict | None:
    """Transcribes the given audio file using OpenAI Whisper, emitting progress via callback. Supports cancellation."""
    if not os.path.exists(audio_path):
        logger.error(f"Audio file not found for transcription: {audio_path}")
        return None

    model_size = config_manager.get("whisper_model_size", "base")
    device = config_manager.get("processing_device", "cpu")

    logger.info(f"Starting transcription for {audio_path} using model: {model_size}, device: {device}")

    # Ensure ffmpeg is in PATH
    from backend.utils.audio import ensure_ffmpeg_in_path
    ensure_ffmpeg_in_path()

    try:
        # Load model (use cache)
        if model_size not in _model_cache:
            logger.info(f"Loading Whisper model: {model_size}")
            # Check if model exists locally, if not it will be downloaded by whisper.load_model
            from ..utils.model_utils import is_whisper_model_downloaded
            if not is_whisper_model_downloaded(model_size):
                logger.info(f"Whisper model {model_size} not found locally - will be downloaded")
            _model_cache[model_size] = whisper.load_model(model_size, device=device)
            logger.info(f"Whisper model {model_size} loaded successfully.")
        model = _model_cache[model_size]

        # Use unified progress system for transcription
        # progress_manager = get_progress_manager()
        
        # with progress_manager.create_transcription_context(progress_callback) as context:
        if True: # Placeholder to keep indentation
            try:
                use_fp16 = device == "cuda"
                
                # Check for cancellation before starting
                if cancel_check and cancel_check():
                    logger.info("Transcription cancelled before starting model.transcribe.")
                    return None
                    
                # Perform transcription with progress tracking
                
                # Check environment variable for word timestamps
                env_var = os.environ.get("WHISPER_ENABLE_WORD_TIMESTAMPS")
                if env_var is not None:
                    enable_word_timestamps = env_var.lower() == "true"
                else:
                    import platform
                    enable_word_timestamps = platform.system().lower() != "windows"
                
                result = model.transcribe(audio_path, fp16=use_fp16, verbose=None, word_timestamps=enable_word_timestamps)
                
                # Check for cancellation after transcribe
                if cancel_check and cancel_check():
                    logger.info("Transcription cancelled after model.transcribe.")
                    return None
                    
                # Ensure 100% completion is reported
                # context.emit_progress(100, 100) # context is not defined here
                if progress_callback:
                    progress_callback(100)
                    
                logger.info(f"Transcription completed for {audio_path}. Detected language: {result.get('language')}")
                return result
                
            except Exception as e:
                logger.error(f"Error during transcription: {e}", exc_info=True)
                raise
                
    except Exception as e:
        logger.error(f"Error during Whisper transcription for {audio_path}: {e}", exc_info=True)
        if model_size in _model_cache and isinstance(e, RuntimeError):
            logger.warning(f"Clearing model cache for {model_size} due to error.")
            del _model_cache[model_size]
            if device == "cuda":
                torch.cuda.empty_cache()
        return None

