import os
import sys
import logging
import urllib.request
import time
import shutil

# Add project root to path to allow imports from backend
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.utils.config_manager import config_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Whisper model URLs (copied from whisper source to avoid importing torch/whisper at module level)
WHISPER_MODELS = {
    "tiny.en": "https://openaipublic.azureedge.net/main/whisper/models/d3dd57d32accea0b295c96e26691aa14d8822fac7d9d27d5dc00b4ca2826dd03/tiny.en.pt",
    "tiny": "https://openaipublic.azureedge.net/main/whisper/models/65147644a518d12f04e32d6f3b26facc3f8dd46e5390956a9424a650c0ce22b9/tiny.pt",
    "base.en": "https://openaipublic.azureedge.net/main/whisper/models/25a8566e1d0c1e2231d1c762132cd20e0f96a85d16145c3a00adf5d1ac670ead/base.en.pt",
    "base": "https://openaipublic.azureedge.net/main/whisper/models/ed3a0b6b1c0edf879ad9b11b1af5a0e6ab5db9205f891f668f8b0e6c6326e34e/base.pt",
    "small.en": "https://openaipublic.azureedge.net/main/whisper/models/f953ad0fd29cacd07d5a9eda5624af0f6bcf2258be67c92b79389873d91e0872/small.en.pt",
    "small": "https://openaipublic.azureedge.net/main/whisper/models/9ecf779972d90ba49c06d968637d720dd632c55bbf19d441fb42bf17a411e794/small.pt",
    "medium.en": "https://openaipublic.azureedge.net/main/whisper/models/d7440d1dc186f76616474e0ff0b3b6b879abc9d1a4926b7adfa41db2d497ab4f/medium.en.pt",
    "medium": "https://openaipublic.azureedge.net/main/whisper/models/345ae4da62f9b3d59415adc60127b97c714f32e89e936602e85993674d08dcb1/medium.pt",
    "large-v1": "https://openaipublic.azureedge.net/main/whisper/models/e4b87e7e0bf463eb8e6956e646f1e277e901512310def2c24bf0e11bd3c28e9a/large-v1.pt",
    "large-v2": "https://openaipublic.azureedge.net/main/whisper/models/81f7c96c852ee8fc832187b0132e569d6c3065a3252ed18e56effd0b6a73e524/large-v2.pt",
    "large-v3": "https://openaipublic.azureedge.net/main/whisper/models/e5b1a55b89c1367dacf97e3e19bfd829a01529dbfdeefa8caeb59b3f1b81dadb/large-v3.pt",
    "large": "https://openaipublic.azureedge.net/main/whisper/models/e5b1a55b89c1367dacf97e3e19bfd829a01529dbfdeefa8caeb59b3f1b81dadb/large-v3.pt",
}

def _download_file(url, dest_path, progress_callback, description, retries=3):
    for attempt in range(retries):
        try:
            logger.info(f"Downloading {url} to {dest_path} (Attempt {attempt + 1}/{retries})")
            
            # Check for existing partial file
            resume_header = {}
            file_mode = "wb"
            downloaded = 0
            
            if os.path.exists(dest_path):
                downloaded = os.path.getsize(dest_path)
                # Check if server supports range requests (HEAD request)
                req = urllib.request.Request(url, method="HEAD")
                try:
                    with urllib.request.urlopen(req) as response:
                        total_size = int(response.info().get("Content-Length"))
                        if downloaded == total_size:
                            logger.info("File already fully downloaded.")
                            return
                        if downloaded > total_size:
                            logger.warning("Local file larger than remote. Restarting download.")
                            downloaded = 0
                        elif response.headers.get("Accept-Ranges") == "bytes":
                            resume_header = {"Range": f"bytes={downloaded}-"}
                            file_mode = "ab"
                            logger.info(f"Resuming download from byte {downloaded}")
                        else:
                            logger.warning("Server does not support resume. Restarting download.")
                            downloaded = 0
                except Exception as e:
                    logger.warning(f"Could not check file size: {e}. Restarting download.")
                    downloaded = 0

            req = urllib.request.Request(url, headers=resume_header)
            with urllib.request.urlopen(req) as source, open(dest_path, file_mode) as output:
                if "Content-Length" in source.info():
                    total_size = int(source.info().get("Content-Length")) + downloaded
                else:
                    total_size = None # Unknown size

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
                    if current_time - last_report_time > 0.5 or (total_size and downloaded == total_size):
                        last_report_time = current_time
                        
                        # Calculate progress
                        percent = int(downloaded * 100 / total_size) if total_size else 0
                        
                        # Calculate speed and ETA
                        elapsed_time = current_time - start_time
                        if elapsed_time > 0:
                            # Speed based on this session's download
                            session_downloaded = downloaded - (int(resume_header.get("Range", "bytes=0-").split("=")[1].split("-")[0]) if resume_header else 0)
                            speed_bps = session_downloaded / elapsed_time
                            speed_mbps = speed_bps / (1024 * 1024)
                            
                            if total_size:
                                remaining_bytes = total_size - downloaded
                                eta_seconds = remaining_bytes / speed_bps if speed_bps > 0 else 0
                                eta_str = f"{int(eta_seconds)}s"
                            else:
                                eta_str = "?"
                            
                            speed_str = f"{speed_mbps:.2f} MB/s"
                        else:
                            speed_str = "..."
                            eta_str = "..."

                        progress_callback(f"{description}", percent, speed_str, eta_str)
            
            # If we get here, download completed successfully
            return

        except Exception as e:
            logger.error(f"Download failed (Attempt {attempt + 1}): {e}")
            if attempt == retries - 1:
                raise e
            time.sleep(2) # Wait before retry

def download_models(progress_callback=None, hf_token=None, whisper_model_size=None):
    """
    Downloads necessary models.
    progress_callback: function(status_message, percent_complete, speed=None, eta=None)
    """
    # Lazy imports to avoid loading heavy libraries at module level
    import torch
    import whisper
    import silero_vad
    import huggingface_hub
    from pyannote.audio import Pipeline

    # Add safe globals for Pyannote
    try:
        from pyannote.audio.core.task import Specifications, Problem, Resolution, Task
        torch.serialization.add_safe_globals([Specifications, Problem, Resolution, Task])
    except ImportError:
        pass 

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
        whisper_model_size = str(config_manager.get("whisper_model_size", "base"))
    
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
        
        # Prefer official whisper._MODELS if available (since we imported whisper)
        if hasattr(whisper, "_MODELS") and whisper_model_size in whisper._MODELS:
            url = whisper._MODELS[whisper_model_size]
        else:
            url = WHISPER_MODELS.get(whisper_model_size)

        if not url:
             # Fallback if model size not in our local dict, try to let whisper handle it or error
             logger.warning(f"Unknown whisper model size: {whisper_model_size}, letting whisper handle it.")
             # We can't get URL easily without importing whisper._MODELS which we want to avoid at top level
             # But here we HAVE imported whisper.
             if hasattr(whisper, "_MODELS"):
                 url = whisper._MODELS.get(whisper_model_size)

        if url:
            filename = os.path.basename(url)
            filepath = os.path.join(download_root, filename)
            
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

    # 5. Preload Embedding Model
    embedding_model_name = "pyannote/wespeaker-voxceleb-resnet34-LM"
    report(f"Downloading Embedding model ({embedding_model_name})...", 95)
    if hf_token:
        try:
            from pyannote.audio import Model
            # Add safe globals for Pyannote embedding model loading
            # The error message suggests adding torch.torch_version.TorchVersion
            try:
                import torch
                # We need to allowlist specific globals that might be in the checkpoint
                # Based on the error: torch.torch_version.TorchVersion
                # And potentially others.
                # Since we trust the source (Hugging Face pyannote official), we can try to be permissive
                # or just add the specific one requested.
                
                # Note: torch.serialization.add_safe_globals is available in newer torch versions
                if hasattr(torch.serialization, 'add_safe_globals'):
                    # We need to import the class to add it
                    from torch.torch_version import TorchVersion
                    torch.serialization.add_safe_globals([TorchVersion])
            except ImportError:
                pass
            except Exception as e:
                logger.warning(f"Could not add safe globals: {e}")

            Model.from_pretrained(embedding_model_name, token=hf_token)
            report(f"Embedding model ({embedding_model_name}) loaded.", 98)
        except Exception as e:
            logger.error(f"Failed to load Embedding model: {e}")
            # Don't fail completely

    report("Model download complete.", 100)

def preload_models():
    download_models()

def check_model_status(whisper_model_size=None):
    """
    Check the status of all models.
    Returns a dict with status of each model.
    """
    status = {
        "whisper": {"downloaded": False, "path": None},
        "pyannote": {"downloaded": False, "path": None},
        "embedding": {"downloaded": False, "path": None}
    }
    
    # Check Whisper
    if not whisper_model_size:
        whisper_model_size = str(config_manager.get("whisper_model_size", "base"))
    download_root = os.getenv("XDG_CACHE_HOME", os.path.join(os.path.expanduser("~"), ".cache"))
    download_root = os.path.join(download_root, "whisper")
    
    # Use local dict instead of importing whisper
    url = WHISPER_MODELS.get(whisper_model_size)
    
    if url:
        filename = os.path.basename(url)
        filepath = os.path.join(download_root, filename)
        
        if os.path.exists(filepath):
            status["whisper"]["downloaded"] = True
            status["whisper"]["path"] = filepath
    
    # Check Pyannote (harder to check without loading, but we can check cache)
    # Pyannote caches in ~/.cache/huggingface/hub/models--pyannote--speaker-diarization-community-1
    # This is a rough check.
    hf_cache = os.getenv("HF_HOME", os.path.join(os.path.expanduser("~"), ".cache", "huggingface"))
    hf_cache = os.path.join(hf_cache, "hub")
    pyannote_path = os.path.join(hf_cache, "models--pyannote--speaker-diarization-community-1")
    if os.path.exists(pyannote_path):
        status["pyannote"]["downloaded"] = True
        status["pyannote"]["path"] = pyannote_path
        
    # Check Embedding
    embedding_path = os.path.join(hf_cache, "models--pyannote--wespeaker-voxceleb-resnet34-LM")
    if os.path.exists(embedding_path):
        status["embedding"]["downloaded"] = True
        status["embedding"]["path"] = embedding_path

    return status

def delete_model(model_name: str):
    """
    Delete a specific model from the cache.
    model_name: 'whisper', 'pyannote', 'embedding'
    """
    status = check_model_status()
    model_info = status.get(model_name)
    
    if not model_info or not model_info["downloaded"] or not model_info["path"]:
        logger.warning(f"Model {model_name} not found or not downloaded.")
        return False

    path = model_info["path"]
    try:
        if os.path.isfile(path):
            os.remove(path)
            logger.info(f"Deleted file: {path}")
        elif os.path.isdir(path):
            shutil.rmtree(path)
            logger.info(f"Deleted directory: {path}")
        return True
    except Exception as e:
        logger.error(f"Failed to delete {model_name} at {path}: {e}")
        raise e

if __name__ == "__main__":
    preload_models()
