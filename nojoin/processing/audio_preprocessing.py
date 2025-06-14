import os
import tempfile
import logging
from pydub import AudioSegment
from typing import Dict, Tuple, Optional

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
        logger.info(f"[Audio Preprocessing] Starting VAD preprocessing for: {input_path}")
        
        # Analyze input audio first
        audio_info = analyze_audio_file(input_path)
        if audio_info:
            logger.info(f"[Audio Preprocessing] Input audio: {audio_info['duration']:.2f}s, "
                       f"{audio_info['channels']} channels, {audio_info['sample_rate']}Hz, "
                       f"{audio_info['format']}, {audio_info['size_mb']:.1f}MB")
        
        audio = AudioSegment.from_file(input_path)
        original_duration = len(audio) / 1000.0  # Convert to seconds
        
        # Convert to mono, 16kHz for optimal VAD processing
        audio = audio.set_channels(1).set_frame_rate(16000)
        
        # Apply basic audio normalization to improve VAD accuracy
        audio = normalize_audio_levels(audio)
        
        temp_fd, temp_path = tempfile.mkstemp(suffix="_vad.wav")
        os.close(temp_fd)
        audio.export(temp_path, format="wav")
        
        # Verify the output
        output_size = os.path.getsize(temp_path)
        logger.info(f"[Audio Preprocessing] VAD preprocessing completed: {temp_path}")
        logger.info(f"[Audio Preprocessing] Output: {original_duration:.2f}s, mono, 16kHz, {output_size/1024/1024:.1f}MB")
        
        return temp_path
    except Exception as e:
        logger.error(f"Audio preprocessing for VAD failed for {input_path}: {e}", exc_info=True)
        return None

def convert_wav_to_mp3(input_wav_path: str, output_mp3_path: str) -> bool:
    """
    Converts a mono, 16kHz WAV file to MP3 format. Returns True on success, False on failure.
    """
    try:
        logger.info(f"[Audio Conversion] Converting WAV to MP3: {input_wav_path} -> {output_mp3_path}")
        
        audio = AudioSegment.from_wav(input_wav_path)
        
        # Use reasonable MP3 encoding settings
        audio.export(output_mp3_path, format="mp3", bitrate="128k")
        
        # Log file size comparison
        input_size = os.path.getsize(input_wav_path)
        output_size = os.path.getsize(output_mp3_path)
        compression_ratio = output_size / input_size if input_size > 0 else 0
        
        logger.info(f"[Audio Conversion] Conversion completed successfully")
        logger.info(f"[Audio Conversion] Size: {input_size/1024/1024:.1f}MB -> {output_size/1024/1024:.1f}MB "
                   f"(compression: {compression_ratio:.2f})")
        
        return True
    except Exception as e:
        logger.error(f"Failed to convert {input_wav_path} to MP3: {e}", exc_info=True)
        return False

def analyze_audio_file(file_path: str) -> Optional[Dict]:
    """
    Analyze an audio file and return basic information.
    Returns None if analysis fails.
    """
    try:
        audio = AudioSegment.from_file(file_path)
        file_size = os.path.getsize(file_path)
        
        return {
            "duration": len(audio) / 1000.0,  # seconds
            "channels": audio.channels,
            "sample_rate": audio.frame_rate,
            "format": file_path.split('.')[-1].upper(),
            "size_bytes": file_size,
            "size_mb": file_size / (1024 * 1024),
            "bit_depth": audio.sample_width * 8,
            "max_possible_amplitude": audio.max_possible_amplitude
        }
    except Exception as e:
        logger.warning(f"Could not analyze audio file {file_path}: {e}")
        return None

def normalize_audio_levels(audio: AudioSegment, target_dBFS: float = -20.0) -> AudioSegment:
    """
    Normalize audio levels to improve VAD accuracy.
    Applies gentle normalization to bring audio to consistent levels.
    """
    try:
        # Get current dBFS
        current_dBFS = audio.dBFS
        
        # Calculate gain needed
        if current_dBFS is not None and current_dBFS != float('-inf'):
            gain_needed = target_dBFS - current_dBFS
            
            # Apply gain with reasonable limits (-12dB to +12dB)
            gain_needed = max(-12.0, min(12.0, gain_needed))
            
            if abs(gain_needed) > 1.0:  # Only apply if significant change needed
                audio = audio + gain_needed
                logger.debug(f"[Audio Normalization] Applied {gain_needed:.1f}dB gain "
                           f"({current_dBFS:.1f}dBFS -> {audio.dBFS:.1f}dBFS)")
        
        return audio
    except Exception as e:
        logger.warning(f"Audio normalization failed, using original audio: {e}")
        return audio

def get_audio_quality_metrics(file_path: str) -> Dict:
    """
    Get audio quality metrics that might affect VAD performance.
    Returns metrics dictionary or empty dict on failure.
    """
    try:
        audio_info = analyze_audio_file(file_path)
        if not audio_info:
            return {}
        
        audio = AudioSegment.from_file(file_path)
        
        # Calculate some basic quality metrics
        rms = audio.rms
        max_amplitude = audio.max
        dynamic_range = max_amplitude - rms if rms > 0 else 0
        
        # Estimate SNR (very basic - just dynamic range)
        estimated_snr = 20 * (dynamic_range / max_amplitude) if max_amplitude > 0 else 0
        
        quality_assessment = "Good"
        if audio_info["sample_rate"] < 16000:
            quality_assessment = "Low (sample rate)"
        elif rms < 1000:  # Very quiet audio
            quality_assessment = "Low (quiet)"
        elif estimated_snr < 10:
            quality_assessment = "Moderate (low dynamic range)"
        
        return {
            "rms": rms,
            "max_amplitude": max_amplitude,
            "dynamic_range": dynamic_range,
            "estimated_snr_db": estimated_snr,
            "quality_assessment": quality_assessment,
            **audio_info
        }
    except Exception as e:
        logger.warning(f"Could not calculate quality metrics for {file_path}: {e}")
        return {} 