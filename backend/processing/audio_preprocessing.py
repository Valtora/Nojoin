import json
import logging
import os
import subprocess
import tempfile
import time
from typing import Dict, Optional

from backend.core.exceptions import AudioFormatError
from backend.utils.audio import convert_to_mono_16k

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
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            file_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)

        if "format" not in data:
            raise AudioFormatError(f"Could not parse audio format for {file_path}")

        duration = float(data["format"].get("duration", 0))
        if duration <= 0:
            raise AudioFormatError(f"Audio file has zero duration: {file_path}")

        # Check for at least one audio stream
        audio_streams = [
            s for s in data.get("streams", []) if s.get("codec_type") == "audio"
        ]
        if not audio_streams:
            raise AudioFormatError(f"No audio streams found in {file_path}")

        return data["format"]

    except subprocess.CalledProcessError as e:
        raise AudioFormatError(f"ffprobe failed to analyze {file_path}: {e.stderr}")
    except json.JSONDecodeError:
        raise AudioFormatError(f"ffprobe returned invalid JSON for {file_path}")
    except Exception as e:  # noqa: BLE001
        raise AudioFormatError(f"Validation error for {file_path}: {str(e)}")


def repair_audio_file(file_path: str) -> Optional[str]:
    """
    Attempts to repair a corrupted audio file by re-encoding it with ffmpeg.
    Returns the path to the repaired file (which might be a temp file) or None if repair failed.
    """
    repaired_path = None
    try:
        logger.info(f"Attempting to repair audio file: {file_path}")

        fd, repaired_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)

        # Try to convert to 16-bit PCM WAV, ignoring errors
        # -err_detect ignore_err: ignore decoding errors
        cmd = [
            "ffmpeg",
            "-y",
            "-err_detect",
            "ignore_err",
            "-i",
            file_path,
            "-c:a",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            repaired_path,
        ]

        subprocess.run(cmd, check=True, capture_output=True)

        # Validate the repaired file
        try:
            validate_audio_file(repaired_path)
            logger.info(f"Successfully repaired audio file: {repaired_path}")
            return repaired_path
        except AudioFormatError:
            logger.warning(f"Repaired file is still invalid: {repaired_path}")
            cleanup_temp_file(repaired_path)
            return None

    except subprocess.CalledProcessError as e:
        logger.error(f"ffmpeg repair failed: {e}")
        if repaired_path and os.path.exists(repaired_path):
            cleanup_temp_file(repaired_path)
        return None
    except Exception as e:  # noqa: BLE001
        logger.error(f"Unexpected error during audio repair: {e}")
        if repaired_path and os.path.exists(repaired_path):
            cleanup_temp_file(repaired_path)
        return None


def preprocess_audio_for_diarization(input_path: str) -> str | None:
    """
    Converts the input audio file (typically MP3) to mono, 16kHz WAV for diarization/transcription.
    Writes to a temporary file and returns its path. Caller is responsible for cleanup.
    Returns None on failure.
    """
    try:
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
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to delete temp file {temp_path}: {e}", exc_info=True)


# Suffixes this module and the VAD stage give their scratch files. Matched by
# suffix rather than by a bare "tmp*" glob so the sweep below can only ever reach
# files Nojoin created.
_PIPELINE_TEMP_SUFFIXES = (
    "_vad.wav",
    "_vad_processed.wav",
    "_vad_processed.mp3",
    "_preprocessed.wav",
)


def cleanup_stale_pipeline_temp_files(
    *, max_age_hours: int = 24, temp_dir: str | None = None
) -> int:
    """Reclaim pipeline scratch left behind by a worker that did not exit cleanly.

    The finalise pipeline removes these in a finally block, so nothing accumulates
    on any normal or failing run. A worker killed outright, by an OOM kill or a
    container restart mid-transcode, never reaches it, and each abandoned run
    strands a pair of full-length WAVs.

    An age floor well beyond any finalise means a file still in use cannot be
    caught: a run old enough to qualify has no process left behind it.
    """
    directory = temp_dir or tempfile.gettempdir()
    cutoff = time.time() - (max_age_hours * 60 * 60)
    reclaimed = 0

    try:
        entries = os.listdir(directory)
    except OSError as e:
        logger.warning("Could not scan %s for stale pipeline files: %s", directory, e)
        return 0

    for name in entries:
        if not name.endswith(_PIPELINE_TEMP_SUFFIXES):
            continue

        path = os.path.join(directory, name)
        try:
            if not os.path.isfile(path) or os.path.getmtime(path) >= cutoff:
                continue
            os.remove(path)
        except OSError as e:
            logger.warning("Failed to remove stale pipeline temp file %s: %s", path, e)
            continue

        reclaimed += 1
        logger.info("Removed stale pipeline temp file: %s", path)

    return reclaimed


def preprocess_audio_for_vad(input_path: str) -> str | None:
    """
    Converts the input audio file (typically MP3) to mono, 16kHz WAV for VAD processing.
    Writes to a temporary file and returns its path. Caller is responsible for cleanup.
    Returns None on failure.
    """
    try:
        logger.info(
            f"[Audio Preprocessing] Starting VAD preprocessing for: {input_path}"
        )

        temp_fd, temp_path = tempfile.mkstemp(suffix="_vad.wav")
        os.close(temp_fd)

        convert_to_mono_16k(input_path, temp_path)

        # Normalize (in-place or copy)
        normalize_audio_levels(temp_path, temp_path)

        logger.info(f"[Audio Preprocessing] VAD preprocessing completed: {temp_path}")

        return temp_path
    except Exception as e:
        logger.error(
            f"Audio preprocessing for VAD failed for {input_path}: {e}", exc_info=True
        )
        return None


def convert_wav_to_mp3(input_wav_path: str, output_mp3_path: str) -> bool:
    """
    Converts a mono, 16kHz WAV file to MP3 format. Returns True on success, raises AudioFormatError on failure.
    """
    import subprocess

    try:
        logger.info(
            f"[Audio Conversion] Converting WAV to MP3: {input_wav_path} -> {output_mp3_path}"
        )

        cmd = ["ffmpeg", "-y", "-i", input_wav_path, "-b:a", "128k", output_mp3_path]

        subprocess.run(cmd, check=True, capture_output=True)

        logger.info("[Audio Conversion] Conversion completed successfully")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(
            f"Failed to convert {input_wav_path} to MP3: {e.stderr}", exc_info=True
        )
        raise AudioFormatError(f"FFmpeg conversion failed: {e.stderr}")
    except Exception as e:
        logger.error(f"Failed to convert {input_wav_path} to MP3: {e}", exc_info=True)
        raise AudioFormatError(f"Audio conversion failed: {str(e)}")


def analyze_audio_file(file_path: str) -> Optional[Dict]:
    """
    Analyze an audio file and return basic information.
    Returns None if analysis fails.
    """
    try:
        from backend.utils.audio import ensure_ffmpeg_in_path

        ensure_ffmpeg_in_path()

        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            file_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)

        format_info = data.get("format", {})
        streams = data.get("streams", [])
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})

        return {
            "duration": float(format_info.get("duration", 0)),
            "format": format_info.get("format_name"),
            "bitrate": int(format_info.get("bit_rate", 0))
            if format_info.get("bit_rate")
            else None,
            "sample_rate": int(audio_stream.get("sample_rate", 0))
            if audio_stream.get("sample_rate")
            else None,
            "channels": int(audio_stream.get("channels", 0))
            if audio_stream.get("channels")
            else None,
            "codec": audio_stream.get("codec_name"),
            "size": int(format_info.get("size", 0))
            if format_info.get("size")
            else None,
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to analyze audio file {file_path}: {e}")
        return None


def normalize_audio_levels(
    input_path: str, output_path: str, target_dBFS: float = -20.0
) -> bool:
    """
    Normalize audio levels to improve VAD accuracy.
    Uses ffmpeg's loudnorm filter for EBU R128 normalization.
    """
    from backend.utils.audio import ensure_ffmpeg_in_path

    ensure_ffmpeg_in_path()

    try:
        logger.info(f"Normalizing audio: {input_path} -> {output_path}")

        # If input and output are the same, we need a temp file
        temp_path = None
        target_out = output_path

        if os.path.abspath(input_path) == os.path.abspath(output_path):
            fd, temp_path = tempfile.mkstemp(suffix="_norm.wav")
            os.close(fd)
            target_out = temp_path

        # generic loudnorm parameters usually work well for speech
        # I: integrated loudness target
        # TP: true peak target
        # LRA: loudness range target
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            input_path,
            "-af",
            f"loudnorm=I={target_dBFS}:TP=-1.5:LRA=11",
            "-ar",
            "16000",
            target_out,
        ]

        subprocess.run(cmd, check=True, capture_output=True)

        if temp_path:
            import shutil

            shutil.move(temp_path, output_path)

        return True
    except subprocess.CalledProcessError as e:
        logger.error(
            f"Failed to normalize audio: {e.stderr.decode() if e.stderr else str(e)}"
        )
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
        return False
    except Exception as e:  # noqa: BLE001
        logger.error(f"Unexpected error normalizing audio: {e}")
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
        return False


def get_audio_quality_metrics(file_path: str) -> Dict:
    """
    Get audio quality metrics that might affect VAD performance.
    Returns metrics dictionary or empty dict on failure.
    """
    try:
        data = analyze_audio_file(file_path)
        if not data:
            return {}

        return {
            "sample_rate": data.get("sample_rate"),
            "channels": data.get("channels"),
            "bitrate": data.get("bitrate"),
            "format": data.get("format"),
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed to get audio metrics: {e}")
        return {}
