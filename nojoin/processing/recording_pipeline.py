import os
import logging
import threading
from PySide6.QtCore import QObject, Signal
from nojoin.audio.recorder import AudioRecorder, DEFAULT_SAMPLE_RATE, DEFAULT_CHANNELS
from nojoin.db import database as db_ops
from datetime import datetime, timedelta
from nojoin.utils.config_manager import to_project_relative_path, from_project_relative_path, get_recordings_dir, config_manager

logger = logging.getLogger(__name__)

class RecordingPipeline(QObject):
    # Signals for UI or downstream logic
    recording_started = Signal()
    recording_finished = Signal(str, str, float, int)  # recording_id (str), filename, duration, size
    recording_error = Signal(str)
    recording_status = Signal(str)  # status message for UI
    recording_discarded = Signal(str)  # message for UI when a recording is discarded
    recording_paused = Signal()  # new signal for pause state
    recording_resumed = Signal()  # new signal for resume state

    def __init__(self, output_dir=None, auto_process=False):
        super().__init__()
        self.output_dir = output_dir or get_recordings_dir()
        os.makedirs(self.output_dir, exist_ok=True)
        self.recorder = AudioRecorder(output_dir=self.output_dir)
        self._thread = None
        self._should_stop = threading.Event()
        self._should_pause = threading.Event()
        self._is_recording = False
        self._is_paused = False
        self.auto_process = auto_process
        
        # For pause/resume functionality
        self._audio_segments = []  # List of (filename, duration, size) tuples
        self._recording_start_time = None
        self._total_pause_duration = 0
        self._pause_start_time = None
        self._recording_name = None
        self._sample_rate = None
        self._channels = None
        self._input_device = None
        self._output_device_loopback = None

    def start(self, input_device=None, output_device_loopback=None, sample_rate=None, channels=None):
        if self._is_recording and not self._is_paused:
            self.recording_error.emit("Recording is already in progress.")
            return
        
        # Use defaults if not provided
        sample_rate = sample_rate if sample_rate is not None else DEFAULT_SAMPLE_RATE
        channels = channels if channels is not None else DEFAULT_CHANNELS
        
        # Store device settings for resume
        self._input_device = input_device
        self._output_device_loopback = output_device_loopback
        self._sample_rate = sample_rate
        self._channels = channels
        
        if not self._is_recording:
            # Starting fresh recording
            self._recording_start_time = datetime.now()
            self._total_pause_duration = 0
            self._audio_segments = []
            self._recording_name = self.get_human_friendly_recording_name(self._recording_start_time)
        
        self._should_stop.clear()
        self._should_pause.clear()
        self._thread = threading.Thread(target=self._run_recording, args=(input_device, output_device_loopback, sample_rate, channels), daemon=True)
        self._thread.start()

    def pause(self):
        """Pause the current recording by stopping audio capture but not processing."""
        if not self._is_recording or self._is_paused:
            return
        
        logger.info("Pausing recording...")
        self._pause_start_time = datetime.now()
        self._should_pause.set()
        
        # Stop the recorder but don't process the file yet
        if self.recorder.is_recording:
            self.recorder.is_recording = False
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        
        self._is_paused = True
        self.recording_paused.emit()

    def resume(self):
        """Resume the paused recording by starting a new audio capture."""
        if not self._is_recording or not self._is_paused:
            return
        
        logger.info("Resuming recording...")
        
        # Calculate pause duration
        if self._pause_start_time:
            pause_duration = (datetime.now() - self._pause_start_time).total_seconds()
            self._total_pause_duration += pause_duration
            self._pause_start_time = None
        
        self._is_paused = False
        self._should_pause.clear()
        
        # Start new recording segment
        self._thread = threading.Thread(
            target=self._run_recording, 
            args=(self._input_device, self._output_device_loopback, self._sample_rate, self._channels), 
            daemon=True
        )
        self._thread.start()
        self.recording_resumed.emit()

    def stop(self):
        self._should_stop.set()
        if self.recorder.is_recording:
            self.recorder.is_recording = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        
        # If we were paused when stopping, need to finalize
        if self._is_paused:
            self._is_paused = False
            if self._pause_start_time:
                pause_duration = (datetime.now() - self._pause_start_time).total_seconds()
                self._total_pause_duration += pause_duration
                self._pause_start_time = None
        
        # Process the final recording
        if self._audio_segments:
            self._finalize_recording()
        
        self._is_recording = False

    def get_human_friendly_recording_name(self, dt=None):
        """
        Generate a human-friendly default recording name based on the datetime.
        Example: 'Wednesday 30th April, Afternoon Recording'
        """
        if dt is None:
            dt = datetime.now()
        day_of_week = dt.strftime('%A')
        day = dt.day
        month = dt.strftime('%B')
        # Suffix for day (1st, 2nd, 3rd, etc.)
        def day_suffix(d):
            return 'th' if 11<=d<=13 else {1:'st',2:'nd',3:'rd'}.get(d%10, 'th')
        day_str = f"{day}{day_suffix(day)}"
        hour = dt.hour
        if 5 <= hour < 12:
            tod = 'Morning'
        elif 12 <= hour < 17:
            tod = 'Afternoon'
        elif 17 <= hour < 21:
            tod = 'Evening'
        else:
            tod = 'Night'
        return f"{day_of_week} {day_str} {month}, {tod} Meeting"

    def _run_recording(self, input_device, output_device_loopback, sample_rate, channels):
        try:
            if not self._is_recording:
                # First recording segment
                self._is_recording = True
                self.recording_started.emit()
            
            self.recording_status.emit("Recording audio...")
            
            # Use defaults if not provided
            sample_rate = sample_rate if sample_rate is not None else DEFAULT_SAMPLE_RATE
            channels = channels if channels is not None else DEFAULT_CHANNELS
            success = self.recorder.start_recording(input_device, output_device_loopback, sample_rate, channels)
            if not success:
                self.recording_error.emit("Failed to start recording. Check audio devices.")
                self._is_recording = False
                return
            
            # Wait until stop or pause is requested
            while not self._should_stop.is_set() and not self._should_pause.is_set() and self.recorder.is_recording:
                threading.Event().wait(0.1)
            
            # Stop recording and get result
            result = self.recorder.stop_recording()
            if result is None:
                if not self._should_pause.is_set():  # Only error if not pausing
                    self.recording_error.emit("No audio was recorded or saving failed.")
                    self._is_recording = False
                return
            
            filename, duration, size = result
            
            # Store this segment
            self._audio_segments.append((filename, duration, size))
            logger.info(f"Recorded segment {len(self._audio_segments)}: {filename} ({duration:.2f}s)")
            
            if self._should_pause.is_set():
                # This was a pause, not a stop
                self.recording_status.emit(f"Recording paused. Segment {len(self._audio_segments)} saved.")
            elif self._should_stop.is_set():
                # This was a stop, finalize the recording
                self._finalize_recording()
                
        except Exception as e:
            logger.error(f"RecordingPipeline error: {e}", exc_info=True)
            self.recording_error.emit(str(e))
            self._is_recording = False

    def _finalize_recording(self):
        """Concatenate all audio segments and finalize the recording."""
        try:
            if not self._audio_segments:
                self.recording_error.emit("No audio segments to process.")
                return
            
            # If only one segment, just use it directly
            if len(self._audio_segments) == 1:
                filename, duration, size = self._audio_segments[0]
                final_filename = filename
                total_duration = duration
                total_size = size
            else:
                # Concatenate multiple segments
                final_filename, total_duration, total_size = self._concatenate_audio_segments()
                if not final_filename:
                    self.recording_error.emit("Failed to concatenate audio segments.")
                    return
            
            # Check minimum recording length
            min_length = config_manager.get("min_meeting_length_seconds", 120)
            if total_duration < min_length:
                try:
                    # Clean up all segment files
                    for seg_filename, _, _ in self._audio_segments:
                        abs_path = from_project_relative_path(seg_filename)
                        if os.path.exists(abs_path):
                            os.remove(abs_path)
                    # Clean up final file if different
                    final_abs_path = from_project_relative_path(final_filename)
                    if final_abs_path != from_project_relative_path(self._audio_segments[0][0]) and os.path.exists(final_abs_path):
                        os.remove(final_abs_path)
                except Exception as e:
                    logger.warning(f"Failed to delete short recording files: {e}")
                self.recording_discarded.emit("Recording was too short and has been discarded.")
                self._is_recording = False
                return
            
            # Calculate actual recording times (excluding pauses)
            end_datetime = datetime.now()
            actual_duration_seconds = total_duration
            start_datetime = self._recording_start_time if self._recording_start_time else (end_datetime - timedelta(seconds=actual_duration_seconds))
            
            start_time_iso = start_datetime.isoformat(sep=" ", timespec="seconds")
            end_time_iso = end_datetime.isoformat(sep=" ", timespec="seconds")
            
            # Ensure filename is relative
            rel_filename = to_project_relative_path(from_project_relative_path(final_filename))
            
            # Add to database
            new_id = db_ops.add_recording(
                name=self._recording_name,
                audio_path=rel_filename,
                duration=total_duration,
                size_bytes=total_size,
                format="MP3",
                start_time=start_time_iso,
                end_time=end_time_iso
            )
            
            if not new_id:
                self.recording_error.emit(f"Failed to save recording details for '{self._recording_name}' to the database.")
                self._is_recording = False
                return
            
            logger.info(f"RecordingPipeline: Added recording '{self._recording_name}' (ID: {new_id}) to database.")
            
            # Clean up intermediate files if we concatenated
            if len(self._audio_segments) > 1:
                for seg_filename, _, _ in self._audio_segments:
                    try:
                        abs_path = from_project_relative_path(seg_filename)
                        if abs_path != from_project_relative_path(final_filename) and os.path.exists(abs_path):
                            os.remove(abs_path)
                            logger.info(f"Cleaned up intermediate file: {abs_path}")
                    except Exception as e:
                        logger.warning(f"Failed to clean up intermediate file {seg_filename}: {e}")
            
            self.recording_status.emit(f"Recording saved: {os.path.basename(final_filename)} ({total_duration:.1f}s)")
            self.recording_finished.emit(new_id, final_filename, total_duration, total_size)
            self._is_recording = False
            
            # Optionally trigger processing pipeline
            if self.auto_process:
                from nojoin.processing import pipeline as processing_pipeline
                processing_pipeline.process_recording(new_id, rel_filename)
                
        except Exception as e:
            logger.error(f"Error finalizing recording: {e}", exc_info=True)
            self.recording_error.emit(str(e))
            self._is_recording = False

    def _concatenate_audio_segments(self):
        """Concatenate multiple audio segments into a single file."""
        try:
            from pydub import AudioSegment
            
            logger.info(f"Concatenating {len(self._audio_segments)} audio segments...")
            
            # Load first segment
            first_filename, _, _ = self._audio_segments[0]
            first_abs_path = from_project_relative_path(first_filename)
            combined_audio = AudioSegment.from_mp3(first_abs_path)
            
            # Add subsequent segments
            for i in range(1, len(self._audio_segments)):
                seg_filename, _, _ = self._audio_segments[i]
                seg_abs_path = from_project_relative_path(seg_filename)
                segment_audio = AudioSegment.from_mp3(seg_abs_path)
                combined_audio += segment_audio
                logger.info(f"Added segment {i+1} to concatenated audio")
            
            # Generate output filename
            timestamp = self._recording_start_time.strftime("%Y%m%d_%H%M%S") if self._recording_start_time else datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = f"recording_{timestamp}_combined.mp3"
            output_path = os.path.join(self.output_dir, output_filename)
            
            # Export combined audio
            combined_audio.export(output_path, format="mp3")
            
            total_duration = len(combined_audio) / 1000.0
            total_size = os.path.getsize(output_path)
            rel_output_path = to_project_relative_path(output_path)
            
            logger.info(f"Successfully concatenated audio: {output_path} ({total_duration:.2f}s, {total_size} bytes)")
            
            return rel_output_path, total_duration, total_size
            
        except Exception as e:
            logger.error(f"Failed to concatenate audio segments: {e}", exc_info=True)
            return None, 0, 0

    def is_recording(self):
        return self._is_recording

    def is_paused(self):
        return self._is_paused

    def cleanup(self):
        self.stop()
        self._thread = None 