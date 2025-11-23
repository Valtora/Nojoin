import os
import logging
import whisper
from typing import Optional

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
        
        # Get the download root (default cache directory)
        download_root = os.path.expanduser("~/.cache/whisper")
        
        return os.path.join(download_root, model_filename)
    except Exception as e:
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
        
    except Exception as e:
        logger.error(f"Error checking if model {model_size} is downloaded: {e}")
        return False


def get_whisper_model_size_mb(model_size: str) -> Optional[float]:
    """Get the approximate size of a Whisper model in MB."""
    # Approximate parameter size in millions based on Whisper documentation
    model_sizes = {
        "tiny": 39,
        "base": 74,
        "small": 244,
        "medium": 769,
        "large": 1550,
        "turbo": 809  # Whisper Turbo model size
    }
    return model_sizes.get(model_size)


def check_default_model_availability() -> tuple[bool, str]:
    """
    Check if the default Whisper model is available locally.
    
    Returns:
        tuple: (is_available, model_size)
    """
    from ..utils.config_manager import config_manager
    
    default_model = config_manager.get("whisper_model_size", "turbo")
    is_available = is_whisper_model_downloaded(default_model)
    
    logger.info(f"Default model '{default_model}' availability: {is_available}")
    return is_available, default_model


def should_prompt_for_first_run_download() -> bool:
    """
    Determine if we should prompt the user to download the default model on first run.
    
    Returns:
        bool: True if we should prompt, False otherwise
    """
    is_available, model_size = check_default_model_availability()
    
    # Only prompt if the default model is not available
    if not is_available:
        logger.info(f"First-run model download prompt needed for model: {model_size}")
        return True
    
    logger.info("Default model is available, no first-run prompt needed")
    return False 