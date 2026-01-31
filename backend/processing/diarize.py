# nojoin/processing/diarize.py

import logging
import os
import torch
import huggingface_hub
import warnings
from pyannote.audio import Pipeline
from backend.utils import config_manager
from pyannote.core import Annotation
import pandas as pd # Optional, useful if manipulating results
from dotenv import load_dotenv
from pyannote.audio.pipelines.utils.hook import ProgressHook
import contextlib

from ..utils.config_manager import config_manager

# Add safe globals for Pyannote
try:
    from pyannote.audio.core.task import Specifications, Problem, Resolution
    torch.serialization.add_safe_globals([Specifications, Problem, Resolution])
except ImportError:
    pass # Should not happen if pyannote is installed, but safe to ignore

logger = logging.getLogger(__name__)

# Suppress specific warnings
warnings.filterwarnings("ignore", message="TensorFloat-32")
warnings.filterwarnings("ignore", message="std(): degrees of freedom is <= 0")

# Default diarization pipeline
DEFAULT_PIPELINE = "pyannote/speaker-diarization-community-1"
OFFLINE_DIARIZATION_CONFIG = "backend/processing/offline_diarization_config.yaml"

# Cache for loaded pipelines
_pipeline_cache = {}

def release_pipeline_cache():
    """Releases all loaded Pyannote pipelines from memory and clears CUDA cache."""
    global _pipeline_cache
    if _pipeline_cache:
        logger.info(f"Releasing {_pipeline_cache.keys()} from Pyannote pipeline cache.")
        _pipeline_cache.clear()
        
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

def _filter_short_segments(annotation: Annotation, min_duration_s: float = 0.3) -> Annotation:
    """
    Filters out segments shorter than a minimum duration from a pyannote Annotation.

    Args:
        annotation: The input annotation object.
        min_duration_s: The minimum duration in seconds for a segment to be kept.

    Returns:
        A new annotation object with short segments removed.
    """
    if not isinstance(annotation, Annotation):
        logger.warning(f"Cannot filter segments, input is not a pyannote.core.Annotation: {type(annotation)}")
        return annotation

    filtered_annotation = Annotation(uri=annotation.uri)
    for segment, track, label in annotation.itertracks(yield_label=True):
        if (segment.end - segment.start) >= min_duration_s:
            filtered_annotation[segment, track] = label

    original_segments_count = len(list(annotation.itersegments()))
    filtered_segments_count = len(list(filtered_annotation.itersegments()))
    if original_segments_count > filtered_segments_count:
        logger.info(
            f"Filtered {original_segments_count - filtered_segments_count} segments shorter than {min_duration_s:.2f}s. "
            f"({filtered_segments_count} of {original_segments_count} segments remain)."
        )

    return filtered_annotation

def load_diarization_pipeline(device_str: str, hf_token: str = None):
    """Load pyannote diarization pipeline from Hugging Face and move to device."""
    try:
        if not hf_token:
            hf_token = config_manager.get("hf_token")
        
        if not hf_token:
            raise ValueError("Hugging Face token (hf_token) not found in configuration.")

        # Login to Hugging Face to ensure internal calls (like Inference) have access
        huggingface_hub.login(token=hf_token)

        # Pyannote 3.1+ and HuggingFace Hub use 'token' instead of 'use_auth_token'
        pipeline = Pipeline.from_pretrained(DEFAULT_PIPELINE, token=hf_token)
        pipeline.to(torch.device(device_str))
        return pipeline
    except OSError as e:
        error_msg = str(e)
        if "403" in error_msg or "forbidden" in error_msg.lower():
             logger.error(f"Permission denied for Pyannote model: {e}")
             raise RuntimeError(
                 "Permission denied for Pyannote model. "
                 "Please ensure you have accepted the terms of use on the Hugging Face model page "
                 "and that your token has the correct permissions."
             ) from e
        else:
             logger.error(f"Failed to load diarization pipeline (OSError): {e}", exc_info=True)
             raise RuntimeError(f"Could not load diarization pipeline: {e}") from e
    except Exception as e:
        logger.error(f"Failed to load diarization pipeline: {e}", exc_info=True)
        raise RuntimeError("Could not load diarization pipeline. Please check your HF token and internet connection.") from e

def diarize_audio(audio_path: str, config: dict = None) -> Annotation | None:
    """Performs speaker diarization on the given audio file using pyannote.audio.

    Args:
        audio_path: Path to the audio file (e.g., MP3).
        config: Optional configuration dictionary to override defaults.

    Returns:
        A pyannote.core.Annotation object containing speaker segments, or None on failure.
    """
    if not os.path.exists(audio_path):
        logger.error(f"Audio file not found for diarization: {audio_path}")
        return None

    if not audio_path.lower().endswith('.wav'):
        logger.warning(f"Diarization input {audio_path} is not a WAV file. Compressed formats like MP3 can cause sample count mismatches.")

    # Use provided config or fall back to system config
    get_config = config.get if config else config_manager.get
    device_str = get_config("processing_device", "auto")
    
    if device_str == "auto":
        device_str = "cuda" if torch.cuda.is_available() else "cpu"

    hf_token = get_config("hf_token")
    
    logger.info(f"Starting diarization for {audio_path} using pipeline: {DEFAULT_PIPELINE}, device: {device_str}")
    try:
        device = torch.device(device_str)
        
        # Check cache first
        cache_key = f"{DEFAULT_PIPELINE}_{device_str}"
        if cache_key in _pipeline_cache:
            pipeline = _pipeline_cache[cache_key]
        else:
            pipeline = load_diarization_pipeline(device_str, hf_token)
            _pipeline_cache[cache_key] = pipeline

        # Log audio file info
        try:
            import soundfile as sf
            with sf.SoundFile(audio_path) as f:
                logger.info(f"Audio file info - samplerate: {f.samplerate}, channels: {f.channels}, duration: {len(f) / f.samplerate:.2f}s")
        except Exception as e:
            logger.warning(f"Could not read audio file info for logging: {e}")

        # Run diarization
        logger.info("Running diarization inference...")
        diarization_result = pipeline(audio_path)
        
        # Handle different return types (Annotation vs DiarizeOutput dataclass)
        if hasattr(diarization_result, 'speaker_diarization'):
            logger.info("Detected DiarizeOutput object, extracting 'speaker_diarization' annotation.")
            diarization_result = diarization_result.speaker_diarization
        
        # Check results
        num_segments = len(list(diarization_result.itersegments()))
        num_speakers = len(diarization_result.labels())
        logger.info(f"Diarization complete. Found {num_segments} segments and {num_speakers} speakers.")
        
        if num_segments == 0:
            logger.warning("Diarization returned 0 segments! This explains why speakers are UNKNOWN.")
            
        return _filter_short_segments(diarization_result, min_duration_s=0.3)

    except Exception as e:
        logger.error(f"Diarization failed with error: {e}", exc_info=True)
        # Clear cache if it's a runtime error (e.g. CUDA OOM or device issue)
        if isinstance(e, RuntimeError):
             cache_key = (DEFAULT_PIPELINE, device_str)
             if cache_key in _pipeline_cache:
                logger.warning(f"Clearing pipeline cache for pipeline on {device_str} due to error.")
                del _pipeline_cache[cache_key]
                if device_str == "cuda":
                    torch.cuda.empty_cache()
        return None

def diarize_audio_with_progress(audio_path: str, progress_callback=None, cancel_check=None) -> Annotation | None:
    """Performs speaker diarization with progress callback (0-100%) using a subprocess to capture progress from stdout. Supports cancellation."""
    import subprocess
    import tempfile
    import sys
    import pickle
    import re
    import time
    if not os.path.exists(audio_path):
        logger.error(f"Audio file not found for diarization: {audio_path}")
        return None

    device_str = config_manager.get("processing_device", "cpu")
    pipeline_config_path = OFFLINE_DIARIZATION_CONFIG
    logger.info(f"Starting diarization for {audio_path} using offline config: {pipeline_config_path}, device: {device_str} (subprocess mode)")

    # Regex to match progress lines from subprocess (e.g., "PROGRESS: 50")
    progress_re = re.compile(r"PROGRESS:\s*(\d+)")
    try:
        with tempfile.NamedTemporaryFile(suffix="_diarization.pkl", delete=False) as tmp:
            output_path = tmp.name
        script_path = os.path.join(os.path.dirname(__file__), "diarize_subprocess_entry.py")
        cmd = [sys.executable, script_path, audio_path, output_path, pipeline_config_path, device_str]
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        last_percent = -1
        all_output = []
        for line in process.stdout:
            all_output.append(line)
            # Check for cancellation during output
            if cancel_check and cancel_check():
                logger.info("Diarization cancelled during subprocess output read.")
                process.terminate()
                process.wait()
                return None
            # Look for progress percentage in the line
            match = progress_re.search(line)
            if match:
                percent = int(match.group(1))
                if percent != last_percent and progress_callback:
                    progress_callback(min(percent, 100))
                    last_percent = percent
            # Log the line for debugging
            if line.strip():
                logger.debug(f"[diarization-subprocess] {line.strip()}")
        process.wait()
        if cancel_check and cancel_check():
            logger.info("Diarization cancelled after subprocess wait.")
            return None
        if progress_callback:
            progress_callback(100)
        if process.returncode != 0:
            logger.error(f"Diarization subprocess failed with code {process.returncode}")
            logger.error("Subprocess output:\n" + "".join(all_output))
            return None
        # Load result from output file
        with open(output_path, "rb") as f:
            diarization_result = pickle.load(f)
        logger.info(f"Diarization completed for {audio_path} (subprocess mode).")
        return _filter_short_segments(diarization_result, min_duration_s=0.3)
    except Exception as e:
        logger.error(f"Error during diarization subprocess for {audio_path}: {e}", exc_info=True)
        return None
    finally:
        try:
            os.remove(output_path)
        except Exception:
            pass

