import os
import tempfile
import logging
import subprocess
import json
from typing import Dict, Tuple, Optional
from backend.utils.audio import convert_to_mono_16k
from backend.core.exceptions import AudioFormatError

logger = logging.getLogger(__name__)

def validate_audio_file(file_path: str) -> Dict:
    """
    Validates that the file is a valid audio file using ffprobe.
    Returns metadata dict if valid, raises AudioFormatError if invalid.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Audio file not found: {file_path}")

    try:
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            file_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)

        if "format" not in data:
            raise AudioFormatError(f"Could not parse audio format for {file_path}")
        
        duration = float(data["format"].get("duration", 0))
        if duration <= 0:
            raise AudioFormatError(f"Audio file has zero duration: {file_path}")
            
        # Check for at least one audio stream
        audio_streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
        if not audio_streams:
            raise AudioFormatError(f"No audio streams found in {file_path}")

        return data["format"]

    except subprocess.CalledProcessError as e:
        raise AudioFormatError(f"ffprobe failed to analyze {file_path}: {e.stderr}")
    except json.JSONDecodeError:
        raise AudioFormatError(f"ffprobe returned invalid JSON for {file_path}")
    except Exception as e:
        raise AudioFormatError(f"Validation failed for {file_path}: {str(e)}")

def preprocess_audio_for_diarization(input_path: str) -> str | None:
    """
    Converts the input audio file (typically MP3) to mono, 16kHz WAV for diarization/transcription.
    Writes to a temporary file and returns its path. Caller is responsible for cleanup.
    Returns None on failure.
    """
    try:
        # Write to temp file
        temp_fd, temp_path = tempfile.mkstemp(suffix="_preprocessed.wav")
        os.close(temp_fd)
        
        convert_to_mono_16k(input_path, temp_path)
        
        logger.info(f"Preprocessed audio saved to temp file: {temp_path}")
        return temp_path
    except Exception as e:
        logger.error(f"Audio preprocessing failed for {input_path}: {e}", exc_info=True)
        return None

def cleanup_temp_file(temp_path: str):
    """Deletes the specified temp file, logging any errors."""
    try:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
            logger.info(f"Deleted temp file: {temp_path}")
    except Exception as e:
        logger.warning(f"Failed to delete temp file {temp_path}: {e}", exc_info=True)

def preprocess_audio_for_vad(input_path: str) -> str | None:
    """
    Converts the input audio file (typically MP3) to mono, 16kHz WAV for VAD processing.
    Writes to a temporary file and returns its path. Caller is responsible for cleanup.
    Returns None on failure.
    """
    try:
        logger.info(f"[Audio Preprocessing] Starting VAD preprocessing for: {input_path}")
        
        temp_fd, temp_path = tempfile.mkstemp(suffix="_vad.wav")
        os.close(temp_fd)
        
        # Convert to mono 16k
        convert_to_mono_16k(input_path, temp_path)
        
        # Normalize (in-place or copy)
        normalize_audio_levels(temp_path, temp_path)
        
        logger.info(f"[Audio Preprocessing] VAD preprocessing completed: {temp_path}")
        
        return temp_path
    except Exception as e:
        logger.error(f"Audio preprocessing for VAD failed for {input_path}: {e}", exc_info=True)
        return None

def convert_wav_to_mp3(input_wav_path: str, output_mp3_path: str) -> bool:
    """
    Converts a mono, 16kHz WAV file to MP3 format. Returns True on success, raises AudioFormatError on failure.
    """
    import subprocess
    try:
        logger.info(f"[Audio Conversion] Converting WAV to MP3: {input_wav_path} -> {output_mp3_path}")
        
        cmd = [
            "ffmpeg",
            "-y",
            "-i", input_wav_path,
            "-b:a", "128k",
            output_mp3_path
        ]
        
        subprocess.run(cmd, check=True, capture_output=True)
        
        logger.info(f"[Audio Conversion] Conversion completed successfully")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to convert {input_wav_path} to MP3: {e.stderr}", exc_info=True)
        raise AudioFormatError(f"FFmpeg conversion failed: {e.stderr}")
    except Exception as e:
        logger.error(f"Failed to convert {input_wav_path} to MP3: {e}", exc_info=True)
        raise AudioFormatError(f"Audio conversion failed: {str(e)}")

def analyze_audio_file(file_path: str) -> Optional[Dict]:
    """
    Analyze an audio file and return basic information.
    Returns None if analysis fails.
    """
    # TODO: Implement using ffprobe if needed, for now returning dummy data or None
    # Since we removed pydub, we can't easily get all this info without ffprobe parsing
    return None

def normalize_audio_levels(input_path: str, output_path: str, target_dBFS: float = -20.0) -> bool:
    """
    Normalize audio levels to improve VAD accuracy.
    Applies gentle normalization to bring audio to consistent levels.
    """
    # TODO: Implement normalization using ffmpeg-normalize or similar if needed
    # For now, just copy the file
    import shutil
    try:
        if os.path.abspath(input_path) == os.path.abspath(output_path):
            return True
        shutil.copy2(input_path, output_path)
        return True
    except Exception as e:
        logger.error(f"Failed to normalize audio: {e}")
        return False

def get_audio_quality_metrics(file_path: str) -> Dict:
    """
    Get audio quality metrics that might affect VAD performance.
    Returns metrics dictionary or empty dict on failure.
    """
    # TODO: Implement using ffprobe or other tools
    return {}
 