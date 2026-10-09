import logging
import os
from typing import Optional

from .model_cache_paths import whisper_cache_root

logger = logging.getLogger(__name__)


def get_whisper_model_path(model_size: str) -> Optional[str]:
    """Get the local path where a Whisper model would be stored."""
    try:
        import whisper

        # Get the model URL from whisper's internal mapping
        if model_size not in whisper._MODELS:
            logger.error(f"Unknown model size: {model_size}")
            return None

        model_url = whisper._MODELS[model_size]
        # Extract the filename from the URL (last part after /)
        model_filename = model_url.split("/")[-1]

        return os.path.join(whisper_cache_root(), model_filename)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Error getting model path for {model_size}: {e}")
        return None


def is_whisper_model_downloaded(model_size: str) -> bool:
    """Check if a specific Whisper model is already downloaded locally."""
    try:
        model_path = get_whisper_model_path(model_size)
        if not model_path:
            return False

        # Check if the model file exists in the cache
        exists = os.path.exists(model_path)
        logger.debug(f"Model {model_size} file path: {model_path}, exists: {exists}")
        return exists

    except Exception as e:  # noqa: BLE001
        logger.error(f"Error checking if model {model_size} is downloaded: {e}")
        return False


# Approximate parameter size in millions based on Whisper documentation
WHISPER_MODEL_SIZES_MB = {
    "tiny": 39,
    "base": 74,
    "small": 244,
    "medium": 769,
    "large": 1550,
    "turbo": 809,  # Whisper Turbo model size
}


def get_whisper_model_size_mb(model_size: str) -> Optional[float]:
    """Get the approximate size of a Whisper model in MB."""
    return WHISPER_MODEL_SIZES_MB.get(model_size)
