import os
import sys
import logging
import torch
import whisper
import silero_vad
import huggingface_hub
from pyannote.audio import Pipeline
import urllib.request
import time

# Add project root to path to allow imports from backend
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Add safe globals for Pyannote
try:
    from pyannote.audio.core.task import Specifications, Problem, Resolution, Task
    torch.serialization.add_safe_globals([Specifications, Problem, Resolution, Task])
except ImportError:
    pass # Should not happen if pyannote is installed, but safe to ignore

from backend.utils.config_manager import config_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def _download_file(url, dest_path, progress_callback, description):
    try:
        logger.info(f"Downloading {url} to {dest_path}")
        with urllib.request.urlopen(url) as source, open(dest_path, "wb") as output:
            total_size = int(source.info().get("Content-Length"))
            downloaded = 0
            start_time = time.time()
            chunk_size = 1024 * 1024 # 1MB chunks
            
            last_report_time = 0
            
            while True:
                buffer = source.read(chunk_size)
                if not buffer:
                    break

                downloaded += len(buffer)
                output.write(buffer)

                current_time = time.time()
                # Report every 0.5 seconds
                if current_time - last_report_time > 0.5 or downloaded == total_size:
                    last_report_time = current_time
                    
                    # Calculate progress
                    percent = int(downloaded * 100 / total_size)
                    
                    # Calculate speed and ETA
                    elapsed_time = current_time - start_time
                    if elapsed_time > 0:
                        speed_bps = downloaded / elapsed_time
                        speed_mbps = speed_bps / (1024 * 1024)
                        remaining_bytes = total_size - downloaded
                        eta_seconds = remaining_bytes / speed_bps if speed_bps > 0 else 0
                        
                        speed_str = f"{speed_mbps:.2f} MB/s"
                        eta_str = f"{int(eta_seconds)}s"
                    else:
                        speed_str = "..."
                        eta_str = "..."

                    progress_callback(f"{description}", percent, speed_str, eta_str)
                    
    except Exception as e:
        logger.error(f"Download failed: {e}")
        raise e

def download_models(progress_callback=None, hf_token=None, whisper_model_size=None):
    """
    Downloads necessary models.
    progress_callback: function(status_message, percent_complete, speed=None, eta=None)
    """
    def report(msg, percent, speed=None, eta=None):
        logger.info(f"{msg} ({percent}%)")
        if progress_callback:
            try:
                progress_callback(msg, percent, speed, eta)
            except TypeError:
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
        try:
            huggingface_hub.login(token=hf_token)
        except Exception as e:
            logger.error(f"Hugging Face login failed: {e}")
            raise ValueError(f"Hugging Face login failed: {str(e)}. Please check your token.")

    # 2. Preload Silero VAD
    report("Downloading Silero VAD model...", 20)
    try:
        silero_vad.load_silero_vad()
        report("Silero VAD model loaded.", 30)
    except Exception as e:
        logger.error(f"Failed to load Silero VAD: {e}")
        # Don't fail completely, VAD might be optional or retriable

    # 3. Preload Whisper Model
    report(f"Checking Whisper model ({whisper_model_size})...", 35)
    try:
        # Determine download path
        download_root = os.getenv("XDG_CACHE_HOME", os.path.join(os.path.expanduser("~"), ".cache"))
        download_root = os.path.join(download_root, "whisper")
        os.makedirs(download_root, exist_ok=True)
        
        url = whisper._MODELS[whisper_model_size]
        filename = os.path.basename(url)
        filepath = os.path.join(download_root, filename)
        
        # Check if file exists and is valid (simple check)
        # whisper.load_model does checksum verification, but we want to avoid re-downloading if possible
        # or force download if we want to show progress?
        # If it exists, whisper.load_model will be fast.
        # If it doesn't, we download it manually to show progress.
        
        if not os.path.exists(filepath):
            report(f"Downloading Whisper model ({whisper_model_size})...", 40)
            _download_file(url, filepath, report, f"Downloading Whisper ({whisper_model_size})")
        else:
            report(f"Whisper model found in cache.", 40)

        # Now load it (this verifies checksum and loads into memory)
        report(f"Loading Whisper model ({whisper_model_size})...", 60)
        whisper.load_model(whisper_model_size, download_root=download_root)
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
        except OSError as e:
            # This often happens if the user hasn't accepted the terms of use
            error_msg = str(e)
            if "403" in error_msg or "forbidden" in error_msg.lower():
                 logger.error(f"Permission denied for Pyannote model: {e}")
                 raise ValueError(
                     f"Permission denied for {pipeline_name}. "
                     "Please ensure you have accepted the terms of use on the Hugging Face model page "
                     "and that your token has the correct permissions."
                 )
            else:
                 logger.error(f"Failed to download Pyannote pipeline: {e}")
                 raise e
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
