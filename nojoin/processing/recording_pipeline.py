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

    def __init__(self, output_dir=None, auto_process=False):
        super().__init__()
        self.output_dir = output_dir or get_recordings_dir()
        os.makedirs(self.output_dir, exist_ok=True)
        self.recorder = AudioRecorder(output_dir=self.output_dir)
        self._thread = None
        self._should_stop = threading.Event()
        self._is_recording = False
        self.auto_process = auto_process

    def start(self, input_device=None, output_device_loopback=None, sample_rate=None, channels=None):
        if self._is_recording:
            self.recording_error.emit("Recording is already in progress.")
            return
        # Use defaults if not provided
        sample_rate = sample_rate if sample_rate is not None else DEFAULT_SAMPLE_RATE
        channels = channels if channels is not None else DEFAULT_CHANNELS
        self._should_stop.clear()
        self._thread = threading.Thread(target=self._run_recording, args=(input_device, output_device_loopback, sample_rate, channels), daemon=True)
        self._thread.start()

    def stop(self):
        self._should_stop.set()
        if self.recorder.is_recording:
            self.recorder.is_recording = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
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
        return f"{day_of_week} {day_str} {month}, {tod} Recording"

    def _run_recording(self, input_device, output_device_loopback, sample_rate, channels):
        try:
            self._is_recording = True
            self.recording_status.emit("Starting recording...")
            self.recording_started.emit()
            # Use defaults if not provided
            sample_rate = sample_rate if sample_rate is not None else DEFAULT_SAMPLE_RATE
            channels = channels if channels is not None else DEFAULT_CHANNELS
            success = self.recorder.start_recording(input_device, output_device_loopback, sample_rate, channels)
            if not success:
                self.recording_error.emit("Failed to start recording. Check audio devices.")
                self._is_recording = False
                return
            # Wait until stop is requested
            while not self._should_stop.is_set() and self.recorder.is_recording:
                threading.Event().wait(0.1)
            # Stop recording and get result
            result = self.recorder.stop_recording()
            if result is None:
                self.recording_error.emit("No audio was recorded or saving failed.")
                self._is_recording = False
                return
            filename, duration, size = result
            # --- Discard if duration < min_meeting_length_seconds (configurable) ---
            min_length = config_manager.get("min_meeting_length_seconds", 120)
            if duration < min_length:
                try:
                    if os.path.exists(filename):
                        os.remove(filename)
                except Exception as e:
                    logger.warning(f"Failed to delete short recording file: {filename} ({e})")
                self.recording_discarded.emit("Recording was too short and has been discarded.")
                self._is_recording = False
                return
            # Add to database (filename is already relative)
            # Use human-friendly name
            dt = datetime.now()
            recording_name = self.get_human_friendly_recording_name(dt)
            rel_filename = to_project_relative_path(from_project_relative_path(filename))  # Ensure relative

            # Calculate start_time and end_time
            start_datetime_obj = dt - timedelta(seconds=duration)
            start_time_iso = start_datetime_obj.isoformat(sep=" ", timespec="seconds")
            end_time_iso = dt.isoformat(sep=" ", timespec="seconds")

            new_id = db_ops.add_recording(
                name=recording_name,
                audio_path=rel_filename,
                duration=duration,
                size_bytes=size,
                format="MP3", # Or determine dynamically if possible
                start_time=start_time_iso,
                end_time=end_time_iso
            )
            if not new_id:
                self.recording_error.emit(f"Failed to save recording details for '{recording_name}' to the database.")
                self._is_recording = False
                return
            logger.info(f"RecordingPipeline: Added recording '{recording_name}' (ID: {new_id}) to database.")
            self.recording_status.emit(f"Recording saved: {os.path.basename(filename)} ({duration:.1f}s)")
            self.recording_finished.emit(new_id, filename, duration, size)
            self._is_recording = False
            # Optionally trigger processing pipeline
            if self.auto_process:
                from nojoin.processing import pipeline as processing_pipeline
                processing_pipeline.process_recording(new_id, rel_filename)
        except Exception as e:
            logger.error(f"RecordingPipeline error: {e}", exc_info=True)
            self.recording_error.emit(str(e))
            self._is_recording = False

    def is_recording(self):
        return self._is_recording

    def cleanup(self):
        self.stop()
        self._thread = None 