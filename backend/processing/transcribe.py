# nojoin/processing/transcribe.py
# Thin dispatcher: selects a pluggable transcription engine. The Whisper engine
# logic lives in backend/processing/engines/whisper_engine.py.

import logging

from ..utils.config_manager import config_manager

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


def transcribe_audio(audio_path: str, config: dict = None) -> dict | None:
    """Transcribe an audio file with the engine selected in config.

    Reads config['transcription_backend'] (default 'whisper'). Public signature
    preserved for backend/worker/tasks.py. Returns the canonical transcription
    dict, or None on failure / unknown engine.
    """
    get_config = config.get if config else config_manager.get
    backend = get_config("transcription_backend", "whisper")
    try:
        engine = _get_engine(backend)
    except (ValueError, ImportError) as e:
        logger.error(f"Transcription backend '{backend}' unavailable: {e}")
        return None
    return engine.transcribe(audio_path, config or {})


def release_model_cache() -> None:
    """Release cached models of every instantiated engine."""
    for engine in _ENGINE_REGISTRY.values():
        try:
            engine.release()
        except Exception as e:
            logger.warning(f"Error releasing engine '{engine.name}': {e}")
