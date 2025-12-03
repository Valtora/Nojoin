import os
import sys
import logging
import urllib.request
import time
import shutil

# Add project root to path to allow imports from backend
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.utils.config_manager import config_manager
from backend.utils.logging_config import setup_logging
from backend.utils.download_progress import (
    set_download_progress,
    clear_download_progress,
    is_download_in_progress
)

setup_logging()
logger = logging.getLogger("backend.preload_models")

# Whisper model filenames (used for checking status without importing whisper)
WHISPER_FILENAMES = {
    "tiny.en": "tiny.en.pt",
    "tiny": "tiny.pt",
    "base.en": "base.en.pt",
    "base": "base.pt",
    "small.en": "small.en.pt",
    "small": "small.pt",
    "medium.en": "medium.en.pt",
    "medium": "medium.pt",
    "large-v1": "large-v1.pt",
    "large-v2": "large-v2.pt",
    "large-v3": "large-v3.pt",
    "large": "large-v3.pt",
    "turbo": "large-v3-turbo.pt",
}

def _download_file(url, dest_path, progress_callback, description, retries=3, stage=None):
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

                        progress_callback(f"{description}", percent, speed_str, eta_str, stage=stage)
            
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
    progress_callback: function(status_message, percent_complete, speed=None, eta=None, stage=None)
    """
    # Clear any stale progress from previous runs to prevent progress bar glitches
    clear_download_progress()

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

    def report(msg, percent, speed=None, eta=None, stage=None):
        logger.info(f"{msg} ({percent}%)")
        # Write to shared Redis progress for frontend visibility
        set_download_progress(percent, msg, speed, eta, status="downloading", stage=stage)
        if progress_callback:
            try:
                progress_callback(msg, percent, speed, eta, stage=stage)
            except TypeError:
                # Fallback for callbacks that don't accept stage
                try:
                    progress_callback(msg, percent, speed, eta)
                except TypeError:
                    progress_callback(msg, percent)

    report("Starting model download...", 0, stage="init")

    # 1. Load Configuration
    if not hf_token:
        hf_token = config_manager.get("hf_token")
    
    if not whisper_model_size:
        whisper_model_size = str(config_manager.get("whisper_model_size", "turbo"))
    
    device = config_manager.get("processing_device", "auto")
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    
    if not hf_token:
        logger.warning("HF_TOKEN not found. Pyannote model download might fail if not already cached.")
    else:
        report("Logging in to Hugging Face...", 10, stage="init")
        try:
            huggingface_hub.login(token=hf_token)
        except Exception as e:
            logger.error(f"Hugging Face login failed: {e}")
            raise ValueError(f"Hugging Face login failed: {str(e)}. Please check your token.")

    # 2. Preload Silero VAD
    report("Downloading Silero VAD model...", 20, stage="vad")
    try:
        silero_vad.load_silero_vad()
        report("Silero VAD model loaded.", 100, stage="vad")
    except Exception as e:
        logger.error(f"Failed to load Silero VAD: {e}")
        # Don't fail completely, VAD might be optional or retriable

    # 3. Preload Whisper Model
    report(f"Checking Whisper model ({whisper_model_size})...", 0, stage="whisper")
    try:
        # Determine download path
        download_root = os.getenv("XDG_CACHE_HOME", os.path.join(os.path.expanduser("~"), ".cache"))
        download_root = os.path.join(download_root, "whisper")
        os.makedirs(download_root, exist_ok=True)
        
        # Prefer official whisper._MODELS if available (since we imported whisper)
        url = None
        if hasattr(whisper, "_MODELS") and whisper_model_size in whisper._MODELS:
            url = whisper._MODELS[whisper_model_size]
        
        # Fallback for turbo if not in installed whisper version
        if not url and whisper_model_size == "turbo":
             url = "https://openaipublic.azureedge.net/main/whisper/models/aff26ae408abcba5fbf8813c21e62b0941638c5f6eebfb145be0c9839262a19a/large-v3-turbo.pt"

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
                report(f"Downloading Whisper model ({whisper_model_size})...", 0, stage="whisper")
                _download_file(url, filepath, report, f"Downloading Whisper ({whisper_model_size})", stage="whisper")
            else:
                report(f"Whisper model found in cache.", 100, stage="whisper")

        # Now load it (this verifies checksum and loads into memory)
        report(f"Loading Whisper model ({whisper_model_size})...", 0, stage="whisper_loading")
        whisper.load_model(whisper_model_size, download_root=download_root)
        report(f"Whisper model ({whisper_model_size}) loaded.", 100, stage="whisper_loading")
        
    except Exception as e:
        logger.error(f"Failed to load Whisper model: {e}")
        raise e

    # 4. Preload Pyannote Diarization Model
    pipeline_name = "pyannote/speaker-diarization-community-1"
    report(f"Downloading Pyannote pipeline ({pipeline_name})...", 0, stage="pyannote")
    
    if hf_token:
        try:
            # We just need to download it, not necessarily run it.
            # from_pretrained will download and cache it.
            Pipeline.from_pretrained(pipeline_name, token=hf_token)
            report(f"Pyannote pipeline ({pipeline_name}) loaded.", 100, stage="pyannote")
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
        report("Skipping Pyannote (no token).", 100, stage="pyannote")

    # 5. Preload Embedding Model
    embedding_model_name = "pyannote/wespeaker-voxceleb-resnet34-LM"
    report(f"Downloading Embedding model ({embedding_model_name})...", 0, stage="embedding")
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
            report(f"Embedding model ({embedding_model_name}) loaded.", 100, stage="embedding")
        except Exception as e:
            logger.error(f"Failed to load Embedding model: {e}")
            # Don't fail completely

    report("Model download complete.", 100, stage="complete")
    # Mark download as complete in shared state
    set_download_progress(100, "Model download complete.", status="complete")

def preload_models():
    try:
        download_models()
    except Exception as e:
        # Mark download as errored in shared state
        set_download_progress(0, f"Download failed: {str(e)}", status="error")
        raise

def check_model_status(whisper_model_size=None):
    """
    Check the status of all models.
    Returns a dict with status of each model.
    """
    status = {
        "whisper": {"downloaded": False, "path": None, "checked_paths": []},
        "pyannote": {"downloaded": False, "path": None, "checked_paths": []},
        "embedding": {"downloaded": False, "path": None, "checked_paths": []}
    }
    
    # Check Whisper
    if not whisper_model_size:
        whisper_model_size = str(config_manager.get("whisper_model_size", "base"))
    
    # 1. Check XDG_CACHE_HOME location (Primary)
    download_root = os.getenv("XDG_CACHE_HOME", os.path.join(os.path.expanduser("~"), ".cache"))
    download_root = os.path.join(download_root, "whisper")
    
    # Use local dict instead of importing whisper
    filename = WHISPER_FILENAMES.get(whisper_model_size)
    
    if filename:
        filepath = os.path.join(download_root, filename)
        status["whisper"]["checked_paths"].append(filepath)
        
        if os.path.exists(filepath):
            status["whisper"]["downloaded"] = True
            status["whisper"]["path"] = filepath
        else:
            # 2. Fallback: Check default ~/.cache/whisper
            # This helps if XDG_CACHE_HOME is set but files are in default location
            default_root = os.path.join(os.path.expanduser("~"), ".cache", "whisper")
            default_filepath = os.path.join(default_root, filename)
            if default_filepath != filepath:
                status["whisper"]["checked_paths"].append(default_filepath)
                if os.path.exists(default_filepath):
                    status["whisper"]["downloaded"] = True
                    status["whisper"]["path"] = default_filepath
    
    # Check Pyannote
    # 1. Check HF_HOME location (Primary)
    hf_cache_base = os.getenv("HF_HOME", os.path.join(os.path.expanduser("~"), ".cache", "huggingface"))
    hf_cache = os.path.join(hf_cache_base, "hub")
    pyannote_path = os.path.join(hf_cache, "models--pyannote--speaker-diarization-community-1")
    
    status["pyannote"]["checked_paths"].append(pyannote_path)
    # Check if directory exists and has content (snapshots)
    if os.path.exists(pyannote_path) and (
        os.path.exists(os.path.join(pyannote_path, "snapshots")) or 
        os.path.exists(os.path.join(pyannote_path, "refs"))
    ):
        status["pyannote"]["downloaded"] = True
        status["pyannote"]["path"] = pyannote_path
    else:
        # 2. Fallback: Check default ~/.cache/huggingface/hub
        default_hf_cache = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")
        default_pyannote_path = os.path.join(default_hf_cache, "models--pyannote--speaker-diarization-community-1")
        if default_pyannote_path != pyannote_path:
            status["pyannote"]["checked_paths"].append(default_pyannote_path)
            if os.path.exists(default_pyannote_path) and (
                os.path.exists(os.path.join(default_pyannote_path, "snapshots")) or 
                os.path.exists(os.path.join(default_pyannote_path, "refs"))
            ):
                status["pyannote"]["downloaded"] = True
                status["pyannote"]["path"] = default_pyannote_path
        
    # Check Embedding
    embedding_path = os.path.join(hf_cache, "models--pyannote--wespeaker-voxceleb-resnet34-LM")
    status["embedding"]["checked_paths"].append(embedding_path)
    
    if os.path.exists(embedding_path) and (
        os.path.exists(os.path.join(embedding_path, "snapshots")) or 
        os.path.exists(os.path.join(embedding_path, "refs"))
    ):
        status["embedding"]["downloaded"] = True
        status["embedding"]["path"] = embedding_path
    else:
        # 2. Fallback
        default_hf_cache = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")
        default_embedding_path = os.path.join(default_hf_cache, "models--pyannote--wespeaker-voxceleb-resnet34-LM")
        if default_embedding_path != embedding_path:
            status["embedding"]["checked_paths"].append(default_embedding_path)
            if os.path.exists(default_embedding_path) and (
                os.path.exists(os.path.join(default_embedding_path, "snapshots")) or 
                os.path.exists(os.path.join(default_embedding_path, "refs"))
            ):
                status["embedding"]["downloaded"] = True
                status["embedding"]["path"] = default_embedding_path

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
