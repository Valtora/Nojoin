# nojoin/processing/transcribe.py

import whisper
import logging
import os
import torch
import tqdm
import threading

from ..utils.config_manager import config_manager

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

    try:
        # Load model (use cache)
        if model_size not in _model_cache:
            logger.info(f"Loading Whisper model: {model_size}")
            _model_cache[model_size] = whisper.load_model(model_size, device=device)
            logger.info(f"Whisper model {model_size} loaded successfully.")
        model = _model_cache[model_size]

        # Perform transcription
        # Use fp16=False if device is CPU, True potentially faster on CUDA but check compatibility
        use_fp16 = device == "cuda" 
        result = model.transcribe(audio_path, fp16=use_fp16)

        logger.info(f"Transcription completed for {audio_path}. Detected language: {result.get('language')}")
        # logger.debug(f"Transcription result: {result}") # Can be very verbose

        # TODO: Save transcript to file? Or handle in pipeline manager?
        # For now, just return the result dictionary.

        return result

    except Exception as e:
        logger.error(f"Error during Whisper transcription for {audio_path}: {e}", exc_info=True)
        # Clear model cache entry if loading failed?
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

        # Use robust progress listener and patch global tqdm
        import tqdm
        orig_tqdm = tqdm.tqdm
        tqdm.tqdm = _CustomProgressBar
        try:
            listener = ProgressListener(progress_callback)
            with create_progress_listener_handle(listener):
                use_fp16 = device == "cuda"
                # Check for cancellation before starting
                if cancel_check and cancel_check():
                    logger.info("Transcription cancelled before starting model.transcribe.")
                    return None
                result = model.transcribe(audio_path, fp16=use_fp16, verbose=None)
                # Check for cancellation after transcribe
                if cancel_check and cancel_check():
                    logger.info("Transcription cancelled after model.transcribe.")
                    return None
            if progress_callback:
                progress_callback(100)
            logger.info(f"Transcription completed for {audio_path}. Detected language: {result.get('language')}")
            return result
        finally:
            tqdm.tqdm = orig_tqdm
    except Exception as e:
        logger.error(f"Error during Whisper transcription for {audio_path}: {e}", exc_info=True)
        if model_size in _model_cache and isinstance(e, RuntimeError):
            logger.warning(f"Clearing model cache for {model_size} due to error.")
            del _model_cache[model_size]
            if device == "cuda":
                torch.cuda.empty_cache()
        return None

# Example Usage:
# if __name__ == '__main__':
#     from ..utils.logging_config import setup_logging
#     setup_logging(logging.DEBUG)
#     # Create a dummy mp3 file path for testing
#     dummy_audio = "path/to/your/test_audio.mp3" 
#     if os.path.exists(dummy_audio):
#         transcription = transcribe_audio(dummy_audio)
#         if transcription:
#             print(f"Transcription successful:")
#             print(transcription['text'])
#         else:
#             print("Transcription failed.")
#     else:
#         print(f"Test audio file not found: {dummy_audio}") 