import os
import sys
import logging
import torch
import whisper
import silero_vad
import huggingface_hub
from pyannote.audio import Pipeline

# Add project root to path to allow imports from backend
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.utils.config_manager import config_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def preload_models():
    logger.info("Starting model pre-loading...")

    # 1. Load Configuration
    hf_token = config_manager.get("hf_token")
    whisper_model_size = config_manager.get("whisper_model_size", "base")
    
    device = config_manager.get("processing_device", "auto")
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    
    if not hf_token:
        logger.warning("HF_TOKEN not found in config. Pyannote model download might fail if not already cached.")
    else:
        logger.info("HF_TOKEN found. Logging in to Hugging Face...")
        huggingface_hub.login(token=hf_token)

    # 2. Preload Silero VAD
    logger.info("Pre-loading Silero VAD model...")
    try:
        silero_vad.load_silero_vad()
        logger.info("Silero VAD model loaded.")
    except Exception as e:
        logger.error(f"Failed to load Silero VAD: {e}")

    # 3. Preload Whisper Model
    logger.info(f"Pre-loading Whisper model ({whisper_model_size})...")
    try:
        whisper.load_model(whisper_model_size)
        logger.info(f"Whisper model ({whisper_model_size}) loaded.")
    except Exception as e:
        logger.error(f"Failed to load Whisper model: {e}")

    # 4. Preload Pyannote Diarization Model
    # Using the community model as configured in diarize.py
    pipeline_name = "pyannote/speaker-diarization-community-1"
    logger.info(f"Pre-loading Pyannote pipeline ({pipeline_name})...")
    
    if hf_token:
        try:
            # We just need to download it, not necessarily run it.
            # from_pretrained will download and cache it.
            Pipeline.from_pretrained(pipeline_name, token=hf_token)
            logger.info(f"Pyannote pipeline ({pipeline_name}) loaded.")
        except Exception as e:
            logger.error(f"Failed to load Pyannote pipeline: {e}")
    else:
        logger.warning("Skipping Pyannote load due to missing HF token.")

    logger.info("Model pre-loading complete.")

if __name__ == "__main__":
    preload_models()
