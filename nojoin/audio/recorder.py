# nojoin/audio/recorder.py

import soundcard as sc
import numpy as np
import logging
import os
import threading
import time
from datetime import datetime
from pydub import AudioSegment

from ..utils.config_manager import config_manager, to_project_relative_path, get_recordings_dir

logger = logging.getLogger(__name__)

# Constants
DEFAULT_SAMPLE_RATE = 48000 # Sample rate used by Whisper models
# TODO: Make channels configurable? Whisper generally expects mono, but might be useful to record stereo?
DEFAULT_CHANNELS = 1 

class AudioRecorder:
    def __init__(self, output_dir=None):
        self.output_dir = output_dir or get_recordings_dir()
        self.is_recording = False
        self.recording_thread = None
        self.frames = []
        self.start_time = None
        self.output_filename = None
        self._sample_rate = DEFAULT_SAMPLE_RATE
        self._channels = DEFAULT_CHANNELS
        
        # Audio level tracking
        self._current_input_level = 0.0
        self._current_output_level = 0.0
        self._level_lock = threading.Lock()

        # Ensure output directory exists
        if not os.path.exists(self.output_dir):
            try:
                os.makedirs(self.output_dir, exist_ok=True)
                logger.info(f"Created recordings directory: {self.output_dir}")
            except OSError as e:
                logger.error(f"Failed to create recordings directory {self.output_dir}: {e}", exc_info=True)
                raise # Can't proceed without output directory

    def _get_devices(self):
        """Utility to get available input and output devices."""
        all_mics = sc.all_microphones(include_loopback=True)
        all_speakers = sc.all_speakers()
        default_mic = sc.default_microphone()
        default_speaker = sc.default_speaker()
        logger.debug(f"Default mic: {default_mic}")
        logger.debug(f"Default speaker: {default_speaker}")
        logger.debug(f"All mics: {all_mics}")
        logger.debug(f"All speakers: {all_speakers}")
        return default_mic, default_speaker

    def get_available_devices(self):
        """Returns lists of available input and output devices suitable for recording.

        Returns:
            tuple: (input_devices, output_devices)
                   input_devices: List of tuples [(name, device_object), ...]
                   output_devices: List of tuples [(name, device_object), ...]
                                   (These are loopback devices corresponding to speakers)
        """
        input_devices = []
        output_loopback_devices = []
        try:
            all_mics = sc.all_microphones(include_loopback=True)
            # Filter out loopback devices from inputs, keep only real mics
            for mic in all_mics:
                if not mic.isloopback:
                    input_devices.append((mic.name, mic))

            # Find loopback devices
            for mic in all_mics:
                if mic.isloopback:
                    # Try to extract a cleaner name (often contains speaker name)
                    clean_name = mic.name # Default
                    try:
                        # Heuristic: Extract part before parentheses if present
                        if '(' in mic.name:
                            clean_name = mic.name.split('(')[0].strip()
                        # Or look for common loopback patterns (this is OS/driver dependent)
                        elif 'Loopback' in mic.name:
                           clean_name = mic.name.replace('Loopback', '').replace('(','').replace(')','').strip()
                    except Exception:
                        pass # Ignore potential parsing errors
                    output_loopback_devices.append((f"{clean_name} (Loopback)", mic))

            # Add default devices if not already included (edge case?)
            default_mic = sc.default_microphone()
            if default_mic and default_mic.name not in [d[0] for d in input_devices]:
                 logger.debug(f"Adding default mic '{default_mic.name}' to list.")
                 input_devices.insert(0, (default_mic.name, default_mic))

            default_speaker = sc.default_speaker()
            # Try to find the default speaker's loopback
            default_loopback_found = False
            for name, device in output_loopback_devices:
                if default_speaker.name in device.name: # Match based on original name
                    default_loopback_found = True
                    # Optionally move default loopback to top
                    # output_loopback_devices.remove((name, device))
                    # output_loopback_devices.insert(0, (name, device))
                    break
            if not default_loopback_found:
                logger.warning(f"Could not explicitly find loopback for default speaker '{default_speaker.name}' in loopback list.")

            if not input_devices:
                logger.warning("No suitable input devices found.")
            if not output_loopback_devices:
                logger.warning("No loopback output devices found.")

        except Exception as e:
            logger.error(f"Error getting available devices: {e}", exc_info=True)
            # Return empty lists on error
            return [], []

        logger.debug(f"Found input devices: {[d[0] for d in input_devices]}")
        logger.debug(f"Found output loopback devices: {[d[0] for d in output_loopback_devices]}")
        return input_devices, output_loopback_devices

    def get_current_levels(self):
        """Get current audio input and output levels.
        
        Returns:
            tuple: (input_level, output_level) - RMS levels between 0.0 and 1.0
        """
        with self._level_lock:
            return self._current_input_level, self._current_output_level

    def _record_audio(self, input_device, output_device_loopback, sample_rate, channels):
        """Internal method run in a separate thread to capture audio."""
        self.frames = [] # Reset frames
        self.is_recording = True
        self.start_time = time.time()
        logger.info(f"Starting recording. Input: '{input_device.name}', Output Loopback: '{output_device_loopback.name}', Sample Rate: {sample_rate}, Channels: {channels}")
        error_occurred = False
        # Choose blocksize based on sample rate, aiming for ~100ms chunks
        blocksize = int(sample_rate * 0.1)

        try:
            # Get recorders for mic input and speaker loopback
            with input_device.recorder(samplerate=sample_rate, channels=channels, blocksize=blocksize) as mic_rec, \
                 output_device_loopback.recorder(samplerate=sample_rate, channels=channels, blocksize=blocksize) as spk_rec:
                
                while self.is_recording:
                    # Record data from microphone and speakers (loopback)
                    mic_data = mic_rec.record(numframes=blocksize)
                    spk_data = spk_rec.record(numframes=blocksize)

                    # Calculate audio levels
                    with self._level_lock:
                        # Calculate RMS (root mean square) for level indication
                        self._current_input_level = float(np.sqrt(np.mean(mic_data**2)))
                        self._current_output_level = float(np.sqrt(np.mean(spk_data**2)))

                    # Simple mixing: average the signals
                    # Ensure data is float for averaging
                    mixed_data = (mic_data.astype(np.float32) + spk_data.astype(np.float32)) / 2.0

 

                    self.frames.append(mixed_data.astype(np.float32)) # Store as float32
        except Exception as e:
            logger.error(f"Error during recording: {e}", exc_info=True)
            error_occurred = True
            self.is_recording = False # Stop recording on error
        finally:
            # Reset audio levels when recording stops
            with self._level_lock:
                self._current_input_level = 0.0
                self._current_output_level = 0.0
            logger.info(f"Recording thread finished. Frames captured: {len(self.frames)}. Error occurred: {error_occurred}")
            # Ensure is_recording is false if loop finishes cleanly
            self.is_recording = False 

    def start_recording(self, input_device=None, output_device_loopback=None, sample_rate=DEFAULT_SAMPLE_RATE, channels=DEFAULT_CHANNELS):
        if self.is_recording:
            logger.warning("Recording is already in progress.")
            return False

        try:
            # Use provided devices or get defaults
            if not input_device or not output_device_loopback:
                logger.info("Input or output device not provided, attempting to use defaults.")
                default_mic, default_speaker = self._get_devices()

                if not input_device:
                    input_device = default_mic
                    if not input_device:
                         logger.error("No default input device found.")
                         return False
                    logger.info(f"Using default input device: {input_device.name}")

                if not output_device_loopback:
                    # Find the loopback device corresponding to the default speaker
                    found_loopback = None
                    for mic in sc.all_microphones(include_loopback=True):
                        if default_speaker.name in mic.name and mic.isloopback:
                            found_loopback = mic
                            break
                    if found_loopback:
                         output_device_loopback = found_loopback
                         logger.info(f"Using default output loopback device: {output_device_loopback.name}")
                    else:
                         # Fallback: Try finding *any* loopback if default match failed
                         loopbacks = [m for m in sc.all_microphones(include_loopback=True) if m.isloopback]
                         if loopbacks:
                             output_device_loopback = loopbacks[0] # Pick the first one
                             logger.warning(f"Default loopback not found. Falling back to: '{output_device_loopback.name}'")
                         else:
                             logger.error("No loopback devices found. Cannot record system output.")
                             return False # Cannot proceed

            # Track actual sample rate and channels used
            self._sample_rate = sample_rate
            self._channels = channels

            # Prepare filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename_base = f"recording_{timestamp}.mp3"
            self.output_filename = os.path.join(self.output_dir, filename_base)

            self.recording_thread = threading.Thread(
                target=self._record_audio,
                args=(input_device, output_device_loopback, sample_rate, channels)
            )
            self.recording_thread.daemon = True
            self.recording_thread.start()
            return True

        except Exception as e:
            logger.error(f"Failed to start recording: {e}", exc_info=True)
            self.is_recording = False
            return False

    def stop_recording(self):
        if not self.is_recording:
            logger.info("stop_recording() called, but self.is_recording is already False. Proceeding to process frames if any exist.")

        logger.info("Stopping recording...")
        self.is_recording = False
        if self.recording_thread:
            self.recording_thread.join() # Wait for the thread to finish processing last chunk
        logger.info("Recording stopped.")

        logger.info(f"Frames to save: {len(self.frames)}")
        if not self.frames:
            logger.warning(f"No frames recorded. Input device: {getattr(self, 'input_device', None)}, Output device: {getattr(self, 'output_device_loopback', None)}")
            logger.error("No audio data was captured. This may be due to device issues, permissions, or no input signal. Please check your input/output devices and try again.")
            return None

        # Save the recorded frames to an MP3 file
        try:
            # Concatenate all frames
            recording_data = np.concatenate(self.frames, axis=0)
            logger.info(f"Recording data shape: {recording_data.shape}, dtype: {recording_data.dtype}, sample_rate: {self._sample_rate}, channels: {self._channels}")
            logger.info(f"Recording data min: {np.min(recording_data)}, max: {np.max(recording_data)}, mean: {np.mean(recording_data)}")
            if np.isnan(recording_data).any() or np.isinf(recording_data).any():
                logger.error("Recording data contains NaN or Inf values!")
                return None
            # Ensure data is in float32 for pydub
            if recording_data.dtype != np.float32:
                recording_data = recording_data.astype(np.float32)
            # Convert to int16 PCM for pydub
            pcm_data = (recording_data * 32767).astype(np.int16)
            # If stereo, shape = (n_samples, n_channels)
            if pcm_data.ndim == 2:
                channels = pcm_data.shape[1]
            else:
                channels = 1
            # Create AudioSegment from raw PCM data
            audio_segment = AudioSegment(
                pcm_data.tobytes(),
                frame_rate=self._sample_rate,
                sample_width=2,  # int16
                channels=channels
            )
            audio_segment.export(self.output_filename, format="mp3")
            duration = len(audio_segment) / 1000.0
            size_bytes = os.path.getsize(self.output_filename)
            logger.info(f"Recording saved to {self.output_filename} ({duration:.2f}s, {size_bytes} bytes)")
            rel_output_filename = to_project_relative_path(self.output_filename)
            return rel_output_filename, duration, size_bytes

        except (IOError, ValueError, OSError) as e:
            logger.error(f"Failed to save recording to {self.output_filename}: {e}", exc_info=True)
            logger.error(f"Context: frames={len(self.frames)}, output_dir={self.output_dir}, filename={self.output_filename}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error saving recording: {e}", exc_info=True)
            logger.error(f"Context: frames={len(self.frames)}, output_dir={self.output_dir}, filename={self.output_filename}")
            return None
        finally:
            self.frames = [] # Clear frames after saving
            self.output_filename = None
            self.start_time = None

 