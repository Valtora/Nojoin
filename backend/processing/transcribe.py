# nojoin/processing/transcribe.py
# Thin dispatcher: selects a pluggable transcription engine. The Whisper engine
# logic lives in backend/processing/engines/whisper_engine.py.

import logging

from ..utils.config_manager import config_manager
from .engines.errors import TranscriptionError, transcription_error_from

logger = logging.getLogger(__name__)

_ENGINE_REGISTRY = {}  # name -> TranscriptionEngine instance


def _get_engine(name: str):
    """Return (creating once, lazily) the engine instance for the given name.

    Heavy engine modules are imported lazily so this dispatcher carries no
    heavy module-level imports.
    """
    if name in _ENGINE_REGISTRY:
        return _ENGINE_REGISTRY[name]
    if name == "whisper":
        from .engines.whisper_engine import WhisperEngine

        engine = WhisperEngine()
    elif name == "parakeet":
        from .engines.parakeet_engine import ParakeetEngine

        engine = ParakeetEngine()
    elif name == "canary":
        from .engines.canary_engine import CanaryEngine

        engine = CanaryEngine()
    else:
        raise ValueError(f"Unknown transcription backend: {name}")
    _ENGINE_REGISTRY[name] = engine
    return engine


def transcribe_audio(audio_path: str, config: dict | None = None) -> dict:
    """Transcribe an audio file with the engine selected in config.

    Reads config['transcription_backend'] (default 'whisper'). Returns the
    canonical transcription dict; empty text means the audio held no speech.

    Raises:
        TranscriptionError: The backend is unknown or unavailable, or the engine
            failed. Anything else an engine lets escape is wrapped too, so this
            is the only exception callers need to treat as a lost transcription.
    """
    get_config = config.get if config else config_manager.get
    backend = get_config("transcription_backend", "whisper")
    try:
        engine = _get_engine(backend)
    except (ValueError, ImportError) as e:
        logger.error(f"Transcription backend '{backend}' unavailable: {e}")
        raise TranscriptionError(
            f"Transcription failed: the '{backend}' transcription backend is "
            f"unavailable ({e}).",
            engine=str(backend),
        ) from e
    try:
        return engine.transcribe(audio_path, config or {})
    except TranscriptionError:
        raise
    except Exception as e:
        logger.error(f"Transcription backend '{backend}' failed: {e}", exc_info=True)
        # Engines catch their own model errors and attribute out-of-memory to
        # the device they ran on. What escapes them never ran on a GPU.
        raise transcription_error_from(e, engine=str(backend), on_gpu=False) from e


def release_model_cache() -> None:
    """Release cached models of every instantiated engine."""
    for engine in _ENGINE_REGISTRY.values():
        try:
            engine.release()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Error releasing engine '{engine.name}': {e}")
