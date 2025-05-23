import logging
from PySide6.QtCore import QObject, QTimer, Signal
from just_playback import Playback

logger = logging.getLogger(__name__)

class PlaybackController(QObject):
    # Signals for UI updates
    playback_started = Signal()
    playback_paused = Signal()
    playback_resumed = Signal()
    playback_stopped = Signal()
    playback_finished = Signal()
    playback_error = Signal(str)
    playback_position_changed = Signal(float)  # seconds

    def __init__(self, parent=None):
        super().__init__(parent)
        self.playback = Playback()
        self.audio_path = None
        self.duration = 0.0
        self._timer = QTimer()
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._update_position)
        self._last_state = None
        self._snippet_end_time = None  # New: end time for snippet playback
        self._connect_events()

    def _connect_events(self):
        # just-playback does not have signals, so we poll state in _update_position
        pass

    def play(self, audio_path, start_time=0.0, duration=None):
        try:
            if self.playback.active:
                self.stop()
            self.audio_path = audio_path
            self.playback.load_file(audio_path)
            self.duration = self.playback.duration if self.playback.duration else (duration or 0.0)
            self._snippet_end_time = start_time + duration if duration is not None else None  # Set snippet end time
            logger.info(f"PlaybackController.play: audio_path={audio_path}, start_time={start_time}, duration={duration}, snippet_end_time={self._snippet_end_time}")
            self.playback.play()
            if start_time > 0:
                self.playback.seek(start_time)
            self._timer.start()
            self.playback_started.emit()
        except Exception as e:
            logger.error(f"PlaybackController error in play: {e}", exc_info=True)
            self.playback_error.emit(str(e))

    def pause(self):
        try:
            if self.playback.playing:
                self.playback.pause()
                self._timer.stop()
                self.playback_paused.emit()
        except Exception as e:
            logger.error(f"PlaybackController error in pause: {e}", exc_info=True)
            self.playback_error.emit(str(e))

    def resume(self):
        try:
            if self.playback.paused:
                self.playback.resume()
                self._timer.start()
                self.playback_resumed.emit()
        except Exception as e:
            logger.error(f"PlaybackController error in resume: {e}", exc_info=True)
            self.playback_error.emit(str(e))

    def stop(self):
        try:
            if self.playback.active:
                self.playback.stop()
            self._timer.stop()
            self._snippet_end_time = None  # Reset snippet end time
            self.playback_stopped.emit()
        except Exception as e:
            logger.error(f"PlaybackController error in stop: {e}", exc_info=True)
            self.playback_error.emit(str(e))

    def seek(self, seconds):
        try:
            if self.playback.active:
                self.playback.seek(seconds)
                # Optionally, update position immediately
                self.playback_position_changed.emit(seconds)
        except Exception as e:
            logger.error(f"PlaybackController error in seek: {e}", exc_info=True)
            self.playback_error.emit(str(e))

    def set_volume(self, volume_float: float):
        """Set playback volume (0.0 to 1.0)."""
        if hasattr(self, 'playback') and self.playback is not None:
            try:
                self.playback.set_volume(volume_float)
            except Exception as e:
                logger.error(f"PlaybackController error in set_volume: {e}", exc_info=True)
        self._volume = volume_float

    @property
    def is_playing(self):
        return self.playback.playing

    def _update_position(self):
        try:
            pos = self.playback.curr_pos
            self.playback_position_changed.emit(pos)
            # New: Stop playback if we've reached the snippet end time
            if self._snippet_end_time is not None and pos >= self._snippet_end_time:
                self.stop()
                self.playback_finished.emit()
                return
            # Detect state transitions for finish
            if self._last_state is None:
                self._last_state = self.playback.playing
            if self._last_state and not self.playback.playing and not self.playback.paused:
                # Playback finished
                self._timer.stop()
                self._snippet_end_time = None  # Reset snippet end time
                self.playback_finished.emit()
            self._last_state = self.playback.playing
        except Exception as e:
            logger.error(f"PlaybackController error in _update_position: {e}", exc_info=True)
            self.playback_error.emit(str(e)) 