import logging
import os
import shutil
import sys
import time
import urllib.request
import warnings

# Add project root to path to allow imports from backend
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.utils.config_manager import config_manager
from backend.utils.download_progress import (
    clear_download_progress,
    set_download_progress,
)
from backend.utils.logging_config import setup_logging
from backend.utils.model_cache_paths import (
    hf_hub_cache_root,
    hf_repo_dirname,
    whisper_cache_root,
)
from backend.utils.onnx_asr_cache import ONNX_ASR_MODELS, find_cached_onnx_asr_model
from backend.utils.pyannote_model_utils import (
    is_repo_bundled_pyannote_path,
    resolve_local_pyannote_model,
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


# The Pyannote models status reports, by status key.
PYANNOTE_STATUS_MODELS = {
    "pyannote": "pyannote/speaker-diarization-community-1",
    "embedding": "pyannote/wespeaker-voxceleb-resnet34-LM",
    "segmentation": "pyannote/segmentation-3.0",
}


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


def _download_file(
    url, dest_path, progress_callback, description, retries=3, stage=None
):
    for attempt in range(retries):
        try:
            logger.info(
                f"Downloading {url} to {dest_path} (Attempt {attempt + 1}/{retries})"
            )

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
                            logger.warning(
                                "Local file larger than remote. Restarting download."
                            )
                            os.remove(dest_path)
                            downloaded = 0
                        else:
                            # A partial download left under the final name,
                            # which whisper itself can produce. Move it to the
                            # part path so the transfer resumes rather than
                            # restarting.
                            os.rename(dest_path, part_path)
                            resume_header = {"Range": f"bytes={downloaded}-"}
                            file_mode = "ab"
                            logger.info(f"Resuming download from byte {downloaded}")
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"Could not check file size: {e}. Restarting download."
                    )
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
                            logger.info(
                                "Part file already fully downloaded. Renaming to complete."
                            )
                            os.rename(part_path, dest_path)
                            return
                        elif response.headers.get("Accept-Ranges") == "bytes":
                            resume_header = {"Range": f"bytes={downloaded}-"}
                            file_mode = "ab"
                            logger.info(
                                f"Resuming part file download from byte {downloaded}"
                            )
                        else:
                            logger.warning(
                                "Server does not support resume. Restarting download."
                            )
                            os.remove(part_path)
                            downloaded = 0
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"Could not check file size: {e}. Restarting download."
                    )
                    os.remove(part_path)
                    downloaded = 0

            req = urllib.request.Request(url, headers=resume_header)
            with (
                urllib.request.urlopen(req) as source,
                open(part_path, file_mode) as output,
            ):
                if "Content-Length" in source.info():
                    total_size = int(source.info().get("Content-Length")) + downloaded
                else:
                    total_size = None  # Unknown size

                start_time = time.time()
                chunk_size = 1024 * 1024  # 1MB chunks

                last_report_time = 0

                while True:
                    buffer = source.read(chunk_size)
                    if not buffer:
                        break

                    downloaded += len(buffer)
                    output.write(buffer)

                    current_time = time.time()
                    # Report every 0.5 seconds
                    if current_time - last_report_time > 0.5 or (
                        total_size and downloaded == total_size
                    ):
                        last_report_time = current_time

                        # Calculate progress
                        percent = (
                            int(downloaded * 100 / total_size) if total_size else 0
                        )

                        # Calculate speed and ETA
                        elapsed_time = current_time - start_time
                        if elapsed_time > 0:
                            # Speed based on this session's download
                            session_downloaded = downloaded - (
                                int(
                                    resume_header.get("Range", "bytes=0-")
                                    .split("=")[1]
                                    .split("-")[0]
                                )
                                if resume_header
                                else 0
                            )
                            speed_bps = session_downloaded / elapsed_time
                            speed_mbps = speed_bps / (1024 * 1024)

                            if total_size:
                                remaining_bytes = total_size - downloaded
                                eta_seconds = (
                                    remaining_bytes / speed_bps if speed_bps > 0 else 0
                                )
                                eta_str = f"{int(eta_seconds)}s"
                            else:
                                eta_str = "?"

                            speed_str = f"{speed_mbps:.2f} MB/s"
                        else:
                            speed_str = "..."
                            eta_str = "..."

                        progress_callback(
                            f"{description}", percent, speed_str, eta_str, stage=stage
                        )

            # If we get here, download completed successfully. Rename part to final.
            if os.path.exists(part_path):
                os.rename(part_path, dest_path)
            return

        except Exception as e:
            logger.error(f"Download failed (Attempt {attempt + 1}): {e}")
            if attempt == retries - 1:
                raise e
            time.sleep(2)  # Wait before retry


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
        (
            "backend.processing.segmentation_refinement",
            "release_segmentation_model_cache",
        ),
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

    download_root = whisper_cache_root()
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
        whisper_model_size = whisper_model_size or str(
            config_manager.get("whisper_model_size", "turbo")
        )
        transcription_backend = transcription_backend or str(
            config_manager.get("transcription_backend", "whisper")
        )
        parakeet_model = parakeet_model or str(
            config_manager.get("parakeet_model", "parakeet-tdt-0.6b-v3")
        )
        canary_model = canary_model or str(
            config_manager.get("canary_model", "nemo-canary-1b-v2")
        )

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
        set_download_progress(
            0, f"Model preparation failed: {exc}", status="error", stage="error"
        )
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

    # Only where the engine loads from: whisper.load_model is given exactly
    # this directory, so a copy anywhere else would be downloaded again.
    download_root = whisper_cache_root()

    # Use local dict instead of importing whisper
    filename = WHISPER_FILENAMES.get(whisper_model_size)

    if filename:
        filepath = os.path.join(download_root, filename)
        status["whisper"]["checked_paths"].append(filepath)

        if os.path.exists(filepath):
            status["whisper"]["downloaded"] = True
            status["whisper"]["path"] = filepath

    # Check the ONNX ASR models, only in the hub cache onnx-asr downloads into
    # and only under the exact repo it loads. A copy anywhere else (a personal
    # ~/.cache/huggingface on a bare-metal install, or another repo with a
    # similar name) is never loaded, so reporting it would hide a download
    # still to come.
    hf_cache = hf_hub_cache_root()
    for status_key, onnx_model in ONNX_ASR_MODELS.items():
        status[status_key]["checked_paths"].append(
            os.path.join(hf_cache, onnx_model.repo_dirname)
        )
        repo_dir = find_cached_onnx_asr_model(onnx_model)
        if repo_dir:
            status[status_key]["downloaded"] = True
            status[status_key]["path"] = repo_dir

    for status_key, model_id in PYANNOTE_STATUS_MODELS.items():
        resolved = resolve_local_pyannote_model(model_id)
        status[status_key]["checked_paths"] = resolved.checked_paths
        if resolved.path:
            status[status_key]["downloaded"] = True
            status[status_key]["path"] = resolved.path
            status[status_key]["source"] = resolved.source

    return status


def _deletion_target(model_name: str, found_path: str) -> tuple[str, str]:
    """The managed root, and the one entry in it, that deleting this model removes.

    Whisper is a single file. The Hugging Face models are a whole repo
    directory: blobs, snapshots and refs together. Removing only the snapshot
    a Pyannote status points at would delete symlinks and leave the weights in
    blobs/, with refs/main naming a revision that is gone. Blobs are per repo
    in the hub cache, so no other model shares them.
    """
    if model_name == "whisper":
        return whisper_cache_root(), os.path.basename(found_path)
    if model_name in ONNX_ASR_MODELS:
        return hf_hub_cache_root(), ONNX_ASR_MODELS[model_name].repo_dirname
    return hf_hub_cache_root(), hf_repo_dirname(PYANNOTE_STATUS_MODELS[model_name])


def delete_model(model_name: str, whisper_model_size: str | None = None) -> bool:
    """Delete one model from the cache its loader downloads into.

    Removes exactly the model's own file or repo directory in that cache, and
    raises ValueError for anything else status may have found: a bundled
    asset, a copy in another cache that the loader also reads (Pyannote's
    personal-cache fallback), or an entry that is a symbolic link to
    somewhere else.
    """
    status = check_model_status(whisper_model_size=whisper_model_size)
    model_info = status.get(model_name)

    if not model_info or not model_info["downloaded"] or not model_info["path"]:
        logger.warning(
            f"Model {model_name} (variant: {whisper_model_size}) not found or not downloaded."
        )
        return False

    path = model_info["path"]
    if is_repo_bundled_pyannote_path(path):
        raise ValueError(
            f"Model {model_name} is bundled with the repository at {path} and cannot be deleted from the runtime cache UI."
        )

    root, entry = _deletion_target(model_name, path)
    root = os.path.abspath(root)
    target = os.path.join(root, entry)
    # Status finds Pyannote in the personal cache too, and loads it from there,
    # but Nojoin did not download it and does not delete it.
    if os.path.commonpath([os.path.abspath(path), target]) != target:
        raise ValueError(
            f"Model {model_name} is outside Nojoin's model cache and is not deleted "
            "from here."
        )
    # Compared after resolving links, so the target must be a real entry of
    # the root rather than a link to a directory elsewhere, inside it or not.
    resolved = os.path.realpath(target)
    if resolved != os.path.join(os.path.realpath(root), entry):
        raise ValueError(
            f"Model {model_name} at {target} is a link to {resolved}, not a model "
            "Nojoin downloaded, and is not deleted from here. Remove it by hand."
        )

    if os.path.isfile(target):
        os.remove(target)
        logger.info(f"Deleted file: {target}")
    elif os.path.isdir(target):
        shutil.rmtree(target)
        logger.info(f"Deleted directory: {target}")
    else:
        return False
    return True


if __name__ == "__main__":
    preload_models()
