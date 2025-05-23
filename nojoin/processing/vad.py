import torch
import logging
import silero_vad
import os
import numpy as np

logger = logging.getLogger(__name__)


def mute_non_speech_segments(input_wav_path: str, output_wav_path: str, sampling_rate: int = 16000, threshold: float = 0.5, window_size_samples: int = 512) -> bool:
    """
    Uses Silero VAD to mute non-speech segments in a WAV file. Writes the result to output_wav_path.
    Returns True on success, False on failure.
    """
    try:
        logger.info(f"[VAD] Input WAV: {input_wav_path}, Output WAV: {output_wav_path}")
        if not os.path.exists(input_wav_path):
            logger.error(f"[VAD] Input file does not exist: {input_wav_path}")
            return False
        logger.info(f"[VAD] Input file size: {os.path.getsize(input_wav_path)} bytes")

        # Load audio using silero_vad.read_audio
        wav = silero_vad.read_audio(input_wav_path, sampling_rate=sampling_rate)
        logger.info(f"[VAD] Loaded audio shape: {wav.shape}, dtype: {wav.dtype}")
        # Convert to numpy for masking
        wav_np = wav.cpu().numpy() if hasattr(wav, 'cpu') else np.array(wav)
        logger.info(f"[VAD] Converted to numpy, shape: {wav_np.shape}, dtype: {wav_np.dtype}")

        # Load Silero VAD model
        logger.info("[VAD] Loading Silero VAD model...")
        model = silero_vad.load_silero_vad()
        logger.info("[VAD] Model loaded.")

        # Run VAD
        logger.info(f"[VAD] Running get_speech_timestamps (len={len(wav_np)})...")
        speech_timestamps = silero_vad.get_speech_timestamps(wav_np, model, sampling_rate=sampling_rate, threshold=threshold, window_size_samples=window_size_samples)
        logger.info(f"[VAD] Detected {len(speech_timestamps)} speech segments in {input_wav_path}")

        # Create mask for speech
        mask = np.zeros_like(wav_np, dtype=bool)
        for seg in speech_timestamps:
            logger.debug(f"[VAD] Speech segment: {seg}")
            mask[seg['start']:seg['end']] = True

        # Mute non-speech
        processed_audio = np.where(mask, wav_np, 0)
        logger.info(f"[VAD] Processed audio dtype: {processed_audio.dtype}")

        # Convert back to torch tensor for saving
        processed_audio_tensor = torch.from_numpy(processed_audio.astype(np.float32))
        logger.info(f"[VAD] Converted processed audio to torch tensor, dtype: {processed_audio_tensor.dtype}")

        # Save output using silero_vad.save_audio
        silero_vad.save_audio(output_wav_path, processed_audio_tensor, sampling_rate=sampling_rate)
        logger.info(f"[VAD] Wrote VAD-processed audio to {output_wav_path}")
        return True
    except AssertionError as ae:
        logger.error(f"[VAD] Assertion failed: {ae}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"[VAD] Processing failed for {input_wav_path}: {e}", exc_info=True)
        return False 