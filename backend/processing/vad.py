import torch
import logging
import silero_vad
import os
import numpy as np
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


def mute_non_speech_segments(
    input_wav_path: str, 
    output_wav_path: str, 
    sampling_rate: int = 16000, 
    threshold: float = 0.5, 
    window_size_samples: int = 512,
    min_speech_duration_ms: int = 250,
    min_silence_duration_ms: int = 100,
    fade_duration_ms: int = 50,
    silence_method: str = "mute"  # "mute" or "fade"
) -> bool:
    """
    Uses Silero VAD to mute non-speech segments in a WAV file with enhanced metrics and quality improvements.
    
    Args:
        input_wav_path: Path to input WAV file
        output_wav_path: Path to output WAV file
        sampling_rate: Audio sampling rate (default: 16000)
        threshold: VAD threshold for speech detection (default: 0.5)
        window_size_samples: Window size for VAD processing (default: 512)
        min_speech_duration_ms: Minimum speech segment duration to keep (default: 250ms)
        min_silence_duration_ms: Minimum silence gap to preserve (default: 100ms)
        fade_duration_ms: Fade duration for smooth transitions (default: 50ms)
        silence_method: Method for handling non-speech ("mute" or "fade")
    
    Returns:
        True on success, False on failure
    """
    try:
        logger.info(f"[VAD] Starting VAD processing...")
        logger.info(f"[VAD] Input: {input_wav_path}")
        logger.info(f"[VAD] Output: {output_wav_path}")
        logger.info(f"[VAD] Parameters: threshold={threshold}, min_speech={min_speech_duration_ms}ms, "
                   f"min_silence={min_silence_duration_ms}ms, fade={fade_duration_ms}ms, method={silence_method}")
        
        if not os.path.exists(input_wav_path):
            logger.error(f"[VAD] Input file does not exist: {input_wav_path}")
            return False
        
        input_file_size = os.path.getsize(input_wav_path)
        logger.info(f"[VAD] Input file size: {input_file_size:,} bytes")

        # Load audio using silero_vad.read_audio
        wav = silero_vad.read_audio(input_wav_path, sampling_rate=sampling_rate)
        logger.info(f"[VAD] Loaded audio shape: {wav.shape}, dtype: {wav.dtype}")
        
        # Convert to numpy for processing
        wav_np = wav.cpu().numpy() if hasattr(wav, 'cpu') else np.array(wav)
        total_samples = len(wav_np)
        total_duration_s = total_samples / sampling_rate
        logger.info(f"[VAD] Audio duration: {total_duration_s:.2f}s ({total_samples:,} samples at {sampling_rate}Hz)")

        # Load Silero VAD model
        logger.info("[VAD] Loading Silero VAD model...")
        model = silero_vad.load_silero_vad()
        logger.info("[VAD] Model loaded successfully")

        # Run VAD
        logger.info(f"[VAD] Running speech detection...")
        speech_timestamps = silero_vad.get_speech_timestamps(
            wav_np, model, 
            sampling_rate=sampling_rate, 
            threshold=threshold, 
            window_size_samples=window_size_samples,
            min_speech_duration_ms=min_speech_duration_ms,
            min_silence_duration_ms=min_silence_duration_ms
        )
        
        # Calculate and log detailed VAD metrics
        vad_metrics = _calculate_vad_metrics(speech_timestamps, total_duration_s, sampling_rate)
        _log_vad_metrics(vad_metrics, input_wav_path)
        
        # Process audio based on VAD results
        processed_audio = _apply_vad_processing(
            wav_np, speech_timestamps, sampling_rate, 
            fade_duration_ms, silence_method
        )
        
        # Convert back to torch tensor for saving
        processed_audio_tensor = torch.from_numpy(processed_audio.astype(np.float32))
        
        # Save output
        # silero_vad.save_audio expects a tensor [channels, samples] or [samples]
        # If our numpy array is 1D, we might need to unsqueeze
        if processed_audio_tensor.ndim == 1:
            processed_audio_tensor = processed_audio_tensor.unsqueeze(0)
            
        silero_vad.save_audio(output_wav_path, processed_audio_tensor, sampling_rate=sampling_rate)
        
        output_file_size = os.path.getsize(output_wav_path)
        logger.info(f"[VAD] Output file size: {output_file_size:,} bytes")
        logger.info(f"[VAD] VAD processing completed successfully")
        
        return True
        
    except AssertionError as ae:
        logger.error(f"[VAD] Assertion failed: {ae}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"[VAD] Processing failed for {input_wav_path}: {e}", exc_info=True)
        return False


def _calculate_vad_metrics(speech_timestamps: list, total_duration_s: float, sampling_rate: int) -> Dict[str, Any]:
    """Calculate detailed VAD metrics for logging."""
    if not speech_timestamps:
        return {
            "total_duration_s": total_duration_s,
            "speech_duration_s": 0.0,
            "silence_duration_s": total_duration_s,
            "speech_percentage": 0.0,
            "silence_percentage": 100.0,
            "num_speech_segments": 0,
            "avg_speech_segment_duration_s": 0.0,
            "longest_speech_segment_s": 0.0,
            "longest_silence_gap_s": total_duration_s
        }
    
    # Calculate speech duration
    speech_duration_s = sum((seg['end'] - seg['start']) / sampling_rate for seg in speech_timestamps)
    silence_duration_s = total_duration_s - speech_duration_s
    
    # Calculate percentages
    speech_percentage = (speech_duration_s / total_duration_s) * 100
    silence_percentage = 100 - speech_percentage
    
    # Speech segment statistics
    num_segments = len(speech_timestamps)
    segment_durations = [(seg['end'] - seg['start']) / sampling_rate for seg in speech_timestamps]
    avg_segment_duration_s = sum(segment_durations) / num_segments if num_segments > 0 else 0.0
    longest_segment_s = max(segment_durations) if segment_durations else 0.0
    
    # Calculate longest silence gap
    longest_silence_gap_s = 0.0
    if num_segments > 1:
        for i in range(1, num_segments):
            gap_duration = (speech_timestamps[i]['start'] - speech_timestamps[i-1]['end']) / sampling_rate
            longest_silence_gap_s = max(longest_silence_gap_s, gap_duration)
        
        # Check silence at beginning and end
        if speech_timestamps[0]['start'] > 0:
            longest_silence_gap_s = max(longest_silence_gap_s, speech_timestamps[0]['start'] / sampling_rate)
        
        last_end = speech_timestamps[-1]['end'] / sampling_rate
        if last_end < total_duration_s:
            longest_silence_gap_s = max(longest_silence_gap_s, total_duration_s - last_end)
    
    return {
        "total_duration_s": total_duration_s,
        "speech_duration_s": speech_duration_s,
        "silence_duration_s": silence_duration_s,
        "speech_percentage": speech_percentage,
        "silence_percentage": silence_percentage,
        "num_speech_segments": num_segments,
        "avg_speech_segment_duration_s": avg_segment_duration_s,
        "longest_speech_segment_s": longest_segment_s,
        "longest_silence_gap_s": longest_silence_gap_s
    }


def _log_vad_metrics(metrics: Dict[str, Any], input_path: str) -> None:
    """Log detailed VAD metrics at INFO level."""
    logger.info(f"[VAD] ═══ VAD PROCESSING METRICS ═══")
    logger.info(f"[VAD] File: {os.path.basename(input_path)}")
    logger.info(f"[VAD] Total Duration: {metrics['total_duration_s']:.2f}s")
    logger.info(f"[VAD] Speech Duration: {metrics['speech_duration_s']:.2f}s ({metrics['speech_percentage']:.1f}%)")
    logger.info(f"[VAD] Silence Duration: {metrics['silence_duration_s']:.2f}s ({metrics['silence_percentage']:.1f}%)")
    logger.info(f"[VAD] Speech Segments: {metrics['num_speech_segments']}")
    
    if metrics['num_speech_segments'] > 0:
        logger.info(f"[VAD] Average Speech Segment: {metrics['avg_speech_segment_duration_s']:.2f}s")
        logger.info(f"[VAD] Longest Speech Segment: {metrics['longest_speech_segment_s']:.2f}s")
        logger.info(f"[VAD] Longest Silence Gap: {metrics['longest_silence_gap_s']:.2f}s")
    
    # Quality assessment
    if metrics['speech_percentage'] < 10:
        logger.warning(f"[VAD] Low speech content detected ({metrics['speech_percentage']:.1f}%) - consider reviewing recording quality")
    elif metrics['speech_percentage'] > 90:
        logger.info(f"[VAD] High speech content detected ({metrics['speech_percentage']:.1f}%) - minimal silence removal")
    else:
        logger.info(f"[VAD] Normal speech/silence ratio detected ({metrics['speech_percentage']:.1f}% speech)")
    
    logger.info(f"[VAD] ═══ END VAD METRICS ═══")


def _apply_vad_processing(
    wav_np: np.ndarray, 
    speech_timestamps: list, 
    sampling_rate: int,
    fade_duration_ms: int,
    silence_method: str
) -> np.ndarray:
    """Apply VAD processing with improved quality handling."""
    if not speech_timestamps:
        logger.warning("[VAD] No speech detected - returning silence")
        return np.zeros_like(wav_np)
    
    processed_audio = np.zeros_like(wav_np)
    fade_samples = int((fade_duration_ms / 1000.0) * sampling_rate)
    
    for i, seg in enumerate(speech_timestamps):
        start_sample = seg['start']
        end_sample = seg['end']
        
        # Ensure we don't go out of bounds
        start_sample = max(0, start_sample)
        end_sample = min(len(wav_np), end_sample)
        
        if start_sample >= end_sample:
            continue
        
        # Copy speech segment
        segment_audio = wav_np[start_sample:end_sample].copy()
        
        # Apply fade-in/fade-out for smoother transitions
        if silence_method == "fade" and fade_samples > 0:
            segment_len = len(segment_audio)
            
            # Fade-in at beginning
            if segment_len > fade_samples:
                fade_in = np.linspace(0, 1, fade_samples)
                segment_audio[:fade_samples] *= fade_in
            
            # Fade-out at end
            if segment_len > fade_samples:
                fade_out = np.linspace(1, 0, fade_samples)
                segment_audio[-fade_samples:] *= fade_out
        
        processed_audio[start_sample:end_sample] = segment_audio
        
        logger.debug(f"[VAD] Preserved speech segment {i+1}/{len(speech_timestamps)}: "
                    f"{start_sample/sampling_rate:.2f}s - {end_sample/sampling_rate:.2f}s")
    
    return processed_audio


def get_vad_config_from_settings() -> Dict[str, Any]:
    """Get VAD configuration from application settings (placeholder for future config integration)."""
    # TODO: Integrate with config_manager when available
    return {
        "threshold": 0.5,
        "min_speech_duration_ms": 250,
        "min_silence_duration_ms": 100,  
        "fade_duration_ms": 50,
        "silence_method": "mute"
    } 