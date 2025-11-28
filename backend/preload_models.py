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

def download_models(progress_callback=None, hf_token=None, whisper_model_size=None):
    """
    Downloads necessary models.
    progress_callback: function(status_message, percent_complete)
    """
    def report(msg, percent):
        logger.info(msg)
        if progress_callback:
            progress_callback(msg, percent)

    report("Starting model download...", 0)

    # 1. Load Configuration
    if not hf_token:
        hf_token = config_manager.get("hf_token")
    
    if not whisper_model_size:
        whisper_model_size = config_manager.get("whisper_model_size", "base")
    
    device = config_manager.get("processing_device", "auto")
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    
    if not hf_token:
        logger.warning("HF_TOKEN not found. Pyannote model download might fail if not already cached.")
    else:
        report("Logging in to Hugging Face...", 10)
        huggingface_hub.login(token=hf_token)

    # 2. Preload Silero VAD
    report("Downloading Silero VAD model...", 20)
    try:
        silero_vad.load_silero_vad()
        report("Silero VAD model loaded.", 30)
    except Exception as e:
        logger.error(f"Failed to load Silero VAD: {e}")
        # Don't fail completely, VAD might be optional or retriable

    # 3. Preload Whisper Model
    report(f"Downloading Whisper model ({whisper_model_size})...", 40)
    try:
        # This can take a while, unfortunately whisper.load_model doesn't have a callback
        # We rely on the fact that it caches.
        whisper.load_model(whisper_model_size)
        report(f"Whisper model ({whisper_model_size}) loaded.", 70)
    except Exception as e:
        logger.error(f"Failed to load Whisper model: {e}")
        raise e

    # 4. Preload Pyannote Diarization Model
    pipeline_name = "pyannote/speaker-diarization-community-1"
    report(f"Downloading Pyannote pipeline ({pipeline_name})...", 80)
    
    if hf_token:
        try:
            # We just need to download it, not necessarily run it.
            # from_pretrained will download and cache it.
            Pipeline.from_pretrained(pipeline_name, token=hf_token)
            report(f"Pyannote pipeline ({pipeline_name}) loaded.", 95)
        except Exception as e:
            logger.error(f"Failed to load Pyannote pipeline: {e}")
            raise e
    else:
        logger.warning("Skipping Pyannote load due to missing HF token.")
        report("Skipping Pyannote (no token).", 90)

    report("Model download complete.", 100)

def preload_models():
    download_models()

if __name__ == "__main__":
    preload_models()
