import os
import tempfile
import logging
from pydub import AudioSegment

logger = logging.getLogger(__name__)

def preprocess_audio_for_diarization(input_path: str) -> str | None:
    """
    Converts the input audio file (typically MP3) to mono, 16kHz WAV for diarization/transcription.
    Writes to a temporary file and returns its path. Caller is responsible for cleanup.
    Returns None on failure.
    """
    try:
        audio = AudioSegment.from_file(input_path)
        # Convert to mono, 16kHz
        audio = audio.set_channels(1).set_frame_rate(16000)
        # Write to temp file
        temp_fd, temp_path = tempfile.mkstemp(suffix="_preprocessed.wav")
        os.close(temp_fd)  # We'll write using PyDub
        audio.export(temp_path, format="wav")
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
        audio = AudioSegment.from_file(input_path)
        audio = audio.set_channels(1).set_frame_rate(16000)
        temp_fd, temp_path = tempfile.mkstemp(suffix="_vad.wav")
        os.close(temp_fd)
        audio.export(temp_path, format="wav")
        logger.info(f"Preprocessed audio for VAD saved to temp file: {temp_path}")
        return temp_path
    except Exception as e:
        logger.error(f"Audio preprocessing for VAD failed for {input_path}: {e}", exc_info=True)
        return None

def convert_wav_to_mp3(input_wav_path: str, output_mp3_path: str) -> bool:
    """
    Converts a mono, 16kHz WAV file to MP3 format. Returns True on success, False on failure.
    """
    try:
        audio = AudioSegment.from_wav(input_wav_path)
        audio.export(output_mp3_path, format="mp3")
        logger.info(f"Converted {input_wav_path} to MP3: {output_mp3_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to convert {input_wav_path} to MP3: {e}", exc_info=True)
        return False 