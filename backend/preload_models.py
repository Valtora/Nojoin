import os
import sys
import logging
import urllib.request
import warnings
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
from backend.utils.pyannote_model_utils import resolve_local_pyannote_model, is_repo_bundled_pyannote_path

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

def _is_onnx_asr_model_cached(model_substring: str) -> bool:
    """Check if an onnx-asr model is present in the Hugging Face hub cache."""
    hf_cache_base = os.getenv("HF_HOME", os.path.join(os.path.expanduser("~"), ".cache", "huggingface"))
    hf_cache = os.path.join(hf_cache_base, "hub")
    for cache_dir in [hf_cache, os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")]:
        if os.path.isdir(cache_dir):
            try:
                for entry in os.listdir(cache_dir):
                    if model_substring in entry:
                        return True
            except OSError:
                pass
    return False


def _is_whisper_model_cached(model_size: str) -> bool:
    """Check if a Whisper model file exists in the local cache."""
    filename = WHISPER_FILENAMES.get(model_size)
    if not filename:
        return False
    download_root = os.getenv("XDG_CACHE_HOME", os.path.join(os.path.expanduser("~"), ".cache"))
    filepath = os.path.join(download_root, "whisper", filename)
    if os.path.exists(filepath):
        return True
    default_filepath = os.path.join(os.path.expanduser("~"), ".cache", "whisper", filename)
    return default_filepath != filepath and os.path.exists(default_filepath)


def _suppress_ort_warnings():
    """Suppress non-actionable ONNX Runtime warnings (memcpy node messages)."""
    os.environ["ORT_LOG_SEVERITY_LEVEL"] = "1"


def _suppress_whisper_timing_warnings():
    """Suppress Triton fallback warnings from whisper/timing.py."""
    warnings.filterwarnings(
        "ignore",
        message=r".*Failed to launch Triton kernels.*",
        category=UserWarning,
    )


def _download_file(url, dest_path, progress_callback, description, retries=3, stage=None):
    for attempt in range(retries):
        try:
            logger.info(f"Downloading {url} to {dest_path} (Attempt {attempt + 1}/{retries})")
            
            # Check for existing partial        try:
            downloaded = 0
            file_mode = "wb"
            resume_header = {}
            part_path = dest_path + ".part"

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
                            os.remove(dest_path)
                            downloaded = 0
                        else:
                            # It's partially downloaded but named dest_path? This happens from old code or whisper itself.
                            # So treat dest_path as part_path, we will resume it.
                            os.rename(dest_path, part_path)
                            resume_header = {"Range": f"bytes={downloaded}-"}
                            file_mode = "ab"
                            logger.info(f"Resuming download from byte {downloaded}")
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Could not check file size: {e}. Restarting download.")
                    if os.path.exists(dest_path):
                        os.remove(dest_path)
                    downloaded = 0

            elif os.path.exists(part_path):
                downloaded = os.path.getsize(part_path)
                
                # Check if server supports range requests (HEAD request)
                req = urllib.request.Request(url, method="HEAD")
                try:
                    with urllib.request.urlopen(req) as response:
                        total_size = int(response.info().get("Content-Length"))
                        if downloaded == total_size:
                            logger.info("Part file already fully downloaded. Renaming to complete.")
                            os.rename(part_path, dest_path)
                            return
                        elif response.headers.get("Accept-Ranges") == "bytes":
                            resume_header = {"Range": f"bytes={downloaded}-"}
                            file_mode = "ab"
                            logger.info(f"Resuming part file download from byte {downloaded}")
                        else:
                            logger.warning("Server does not support resume. Restarting download.")
                            os.remove(part_path)
                            downloaded = 0
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Could not check file size: {e}. Restarting download.")
                    os.remove(part_path)
                    downloaded = 0

            req = urllib.request.Request(url, headers=resume_header)
            with urllib.request.urlopen(req) as source, open(part_path, file_mode) as output:
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
            
            # If we get here, download completed successfully. Rename part to final.
            if os.path.exists(part_path):
                os.rename(part_path, dest_path)
            return

        except Exception as e:
            logger.error(f"Download failed (Attempt {attempt + 1}): {e}")
            if attempt == retries - 1:
                raise e
            time.sleep(2) # Wait before retry

def _default_device_for_validation() -> str:
    """Validate downloads on CPU so warmup does not reserve idle VRAM."""
    return "cpu"


def _release_validation_caches() -> None:
    """Drop any model objects created only to validate downloads."""
    import gc
    import sys

    release_hooks = (
        ("backend.processing.transcribe", "release_model_cache"),
        ("backend.processing.diarize", "release_pipeline_cache"),
        ("backend.processing.embedding_core", "release_embedding_model_cache"),
        ("backend.processing.segmentation_refinement", "release_segmentation_model_cache"),
        ("backend.processing.text_embedding", "release_embedding_model"),
    )
    for module_name, release_name in release_hooks:
        module = sys.modules.get(module_name)
        if module is None:
            continue
        release = getattr(module, release_name, None)
        if callable(release):
            release()

    gc.collect()

    torch_module = sys.modules.get("torch")
    if torch_module is not None:
        try:
            if torch_module.cuda.is_available():
                torch_module.cuda.empty_cache()
        except Exception as exc:  # noqa: BLE001
            logger.debug("CUDA cache cleanup skipped after model preparation: %s", exc)


def _prepare_whisper_model(model_size: str) -> None:
    _suppress_whisper_timing_warnings()
    import whisper

    download_root = os.getenv("XDG_CACHE_HOME", os.path.join(os.path.expanduser("~"), ".cache"))
    download_root = os.path.join(download_root, "whisper")
    os.makedirs(download_root, exist_ok=True)

    logger.info("Preparing Whisper model %s in %s", model_size, download_root)
    model = whisper.load_model(
        model_size,
        device=_default_device_for_validation(),
        download_root=download_root,
    )
    del model


def _prepare_pyannote_models(hf_token: str | None) -> None:
    from backend.processing.diarize import load_diarization_pipeline
    from backend.processing.embedding_core import load_embedding_model
    from backend.processing.segmentation_refinement import load_segmentation_model

    device = _default_device_for_validation()
    logger.info("Preparing Pyannote diarization pipeline.")
    diarization_pipeline = load_diarization_pipeline(device, hf_token)
    del diarization_pipeline

    logger.info("Preparing Pyannote speaker embedding model.")
    embedding_model = load_embedding_model(device, hf_token)
    del embedding_model

    logger.info("Preparing Pyannote segmentation refinement model.")
    segmentation_model = load_segmentation_model(device, hf_token)
    del segmentation_model


def _prepare_onnx_asr_model(model_id: str) -> None:
    _suppress_ort_warnings()
    import onnx_asr

    logger.info("Preparing ONNX ASR model %s", model_id)
    model = onnx_asr.load_model(
        model_id,
        quantization="int8",
        providers=["CPUExecutionProvider"],
    )
    del model


def _resolve_onnx_asr_id(backend: str, model_id: str | None) -> str | None:
    if backend == "parakeet":
        from backend.processing.engines.parakeet_engine import ParakeetEngine

        engine = ParakeetEngine()
        return engine._to_onnx_asr_id(model_id or engine.default_model_id)
    if backend == "canary":
        from backend.processing.engines.canary_engine import CanaryEngine

        engine = CanaryEngine()
        return engine._to_onnx_asr_id(model_id or engine.default_model_id)
    return None


def download_models(
    progress_callback=None,
    hf_token=None,
    whisper_model_size=None,
    transcription_backend=None,
    parakeet_model=None,
    canary_model=None,
    include_core=True,
):
    """
    Prepare required model assets on disk without retaining models in memory.

    Warmup intentionally runs in the worker process. It may instantiate a model
    on CPU to validate that downloads completed, then releases all caches and
    CUDA allocations before returning.
    """
    clear_download_progress()

    def report(msg, percent, speed=None, eta=None, stage=None, status="complete"):
        logger.info(f"{msg} ({percent}%)")
        set_download_progress(percent, msg, speed, eta, status=status, stage=stage)
        if progress_callback:
            try:
                progress_callback(msg, percent, speed, eta, stage=stage)
            except TypeError:
                try:
                    progress_callback(msg, percent, speed, eta)
                except TypeError:
                    progress_callback(msg, percent)

    try:
        whisper_model_size = whisper_model_size or str(config_manager.get("whisper_model_size", "turbo"))
        transcription_backend = transcription_backend or str(config_manager.get("transcription_backend", "whisper"))
        parakeet_model = parakeet_model or str(config_manager.get("parakeet_model", "parakeet-tdt-0.6b-v3"))
        canary_model = canary_model or str(config_manager.get("canary_model", "nemo-canary-1b-v2"))

        if include_core:
            report(
                f"Preparing Whisper {whisper_model_size} for live transcription...",
                5,
                stage="whisper",
                status="downloading",
            )
            _prepare_whisper_model(whisper_model_size)
            report(
                f"Whisper {whisper_model_size} is ready.",
                35,
                stage="whisper",
                status="downloading",
            )

            report(
                "Preparing Pyannote diarization, voice embedding, and segmentation models...",
                40,
                stage="pyannote",
                status="downloading",
            )
            _prepare_pyannote_models(hf_token)
            report(
                "Pyannote diarization, voice embedding, and segmentation models are ready.",
                80,
                stage="segmentation",
                status="downloading",
            )

        onnx_model_id = None
        if transcription_backend == "parakeet":
            onnx_model_id = _resolve_onnx_asr_id("parakeet", parakeet_model)
        elif transcription_backend == "canary":
            onnx_model_id = _resolve_onnx_asr_id("canary", canary_model)

        if onnx_model_id:
            report(
                f"Preparing {transcription_backend} model {onnx_model_id}...",
                85,
                stage=transcription_backend,
                status="downloading",
            )
            _prepare_onnx_asr_model(onnx_model_id)

        report("Model preparation complete.", 100, stage="complete", status="complete")
    except Exception as exc:
        logger.error("Model preparation failed: %s", exc, exc_info=True)
        set_download_progress(0, f"Model preparation failed: {exc}", status="error", stage="error")
        raise
    finally:
        _release_validation_caches()

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
        "parakeet": {"downloaded": False, "path": None, "checked_paths": []},
        "canary": {"downloaded": False, "path": None, "checked_paths": []},
        "pyannote": {"downloaded": False, "path": None, "checked_paths": []},
        "embedding": {"downloaded": False, "path": None, "checked_paths": []},
        "segmentation": {"downloaded": False, "path": None, "checked_paths": []},
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
    
    # Check Parakeet
    # Best-effort detection: onnx-asr caches the model under the Hugging Face hub
    # cache. Detection is a directory-name match; the exact repo dir name may vary
    # by onnx-asr version, so this is treated as a heuristic, not authoritative.
    hf_cache_base = os.getenv("HF_HOME", os.path.join(os.path.expanduser("~"), ".cache", "huggingface"))
    hf_cache = os.path.join(hf_cache_base, "hub")
    parakeet_hf_caches = [hf_cache]
    default_hf_cache = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")
    if default_hf_cache not in parakeet_hf_caches:
        parakeet_hf_caches.append(default_hf_cache)

    for cache_dir in parakeet_hf_caches:
        status["parakeet"]["checked_paths"].append(cache_dir)
        if os.path.isdir(cache_dir):
            try:
                for entry in os.listdir(cache_dir):
                    if "parakeet-tdt-0.6b-v3" in entry:
                        status["parakeet"]["downloaded"] = True
                        status["parakeet"]["path"] = os.path.join(cache_dir, entry)
                        break
            except OSError:
                pass
        if status["parakeet"]["downloaded"]:
            break

    # Check Canary
    # Same best-effort HF-cache directory-name match as Parakeet above.
    for cache_dir in parakeet_hf_caches:
        status["canary"]["checked_paths"].append(cache_dir)
        if os.path.isdir(cache_dir):
            try:
                for entry in os.listdir(cache_dir):
                    if "nemo-canary-1b-v2" in entry:
                        status["canary"]["downloaded"] = True
                        status["canary"]["path"] = os.path.join(cache_dir, entry)
                        break
            except OSError:
                pass
        if status["canary"]["downloaded"]:
            break

    for status_key, model_id in (
        ("pyannote", "pyannote/speaker-diarization-community-1"),
        ("embedding", "pyannote/wespeaker-voxceleb-resnet34-LM"),
        ("segmentation", "pyannote/segmentation-3.0"),
    ):
        resolved = resolve_local_pyannote_model(model_id)
        status[status_key]["checked_paths"] = resolved.checked_paths
        if resolved.path:
            status[status_key]["downloaded"] = True
            status[status_key]["path"] = resolved.path
            status[status_key]["source"] = resolved.source

    return status

def delete_model(model_name: str, whisper_model_size: str | None = None) -> bool:
    """
    Delete a specific model from the cache.
    model_name: 'whisper', 'pyannote', 'embedding'
    """
    status = check_model_status(whisper_model_size=whisper_model_size)
    model_info = status.get(model_name)
    
    if not model_info or not model_info["downloaded"] or not model_info["path"]:
        logger.warning(f"Model {model_name} (variant: {whisper_model_size}) not found or not downloaded.")
        return False

    path = model_info["path"]
    if is_repo_bundled_pyannote_path(path):
        raise ValueError(
            f"Model {model_name} is bundled with the repository at {path} and cannot be deleted from the runtime cache UI."
        )
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
