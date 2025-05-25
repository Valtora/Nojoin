import sys
import os
import logging # Import logging
from datetime import datetime, timedelta # Import timedelta
import time # Import time for timer start
import threading # Add threading for playback
import re
import markdown2
import json

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QListView, QTextEdit, QStatusBar, QLabel, QFrame, QMessageBox,
    QTableView, QMenu, QStyle, QSplitter, QLineEdit, QInputDialog, QDialog, QComboBox, QDialogButtonBox, QFormLayout, QProgressBar, QFileDialog, QCheckBox, QSlider, QCompleter, QStyledItemDelegate, QScrollArea, QSizePolicy, QStyleOptionViewItem, QStylePainter, QStyle,
    QGraphicsDropShadowEffect, QListWidget, QListWidgetItem, QGridLayout, QToolButton
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QTime, QAbstractTableModel, QModelIndex, Slot, QPoint, QSize, QStringListModel, QRect, QItemSelectionModel, QItemSelection, QPropertyAnimation, QEasingCurve, Property, QMetaObject
from PySide6.QtGui import QStandardItemModel, QStandardItem, QAction, QIcon, QPixmap, QPainter, QBrush, QColor, QFont, QTextListFormat, QKeySequence, QShortcut
from PySide6 import QtWidgets
# Attempt to import Recorder, handle potential errors
try:
    from ..audio.recorder import AudioRecorder # Relative import
except ImportError:
    # Fallback for running directly or packaging issues?
    try:
        from nojoin.audio.recorder import AudioRecorder
    except ImportError as e:
        print(f"FATAL: Could not import AudioRecorder: {e}")
        # Optionally show a message box before exiting if QApplication is available
        # app = QApplication.instance()
        # if app:
        #     QMessageBox.critical(None, "Import Error", "Failed to load audio recording module. The application cannot continue.")
        sys.exit(1) # Exit if recorder can't be imported

# Import database functions
try:
    from ..db import database as db_ops # Relative import
except ImportError:
    try:
        from nojoin.db import database as db_ops
    except ImportError as e:
        print(f"FATAL: Could not import database module: {e}")
        sys.exit(1)

# Import Processing Pipeline function
try:
    from ..processing import pipeline as processing_pipeline # Relative import
except ImportError:
    try:
        from nojoin.processing import pipeline as processing_pipeline
    except ImportError as e:
        print(f"WARNING: Could not import processing pipeline module: {e}")
        # Allow app to run, but processing won't work
        processing_pipeline = None

# Import audio playback libraries
try:
    import soundfile as sf
    import pyaudio
    playback_enabled = True
except ImportError as e:
    print(f"WARNING: Could not import pyaudio or soundfile: {e}. Audio playback disabled.")
    sf = None
    pyaudio = None
    playback_enabled = False

from nojoin.utils.config_manager import (
    config_manager,
    get_available_whisper_model_sizes,
    get_available_processing_devices,
    get_available_input_devices,
    get_available_output_devices,
    from_project_relative_path,
    get_recordings_dir,
    get_transcripts_dir,
    get_available_themes, # Import theme getter
)

from nojoin.utils.theme_utils import apply_theme_to_widget, get_theme_qss, get_menu_qss, get_border_color, wrap_html_body

from .playback_controller import PlaybackController
from nojoin.processing.recording_pipeline import RecordingPipeline
from .settings_dialog import SettingsDialog
from .processing_dialog import ProcessingProgressDialog, GeminiNotesSpinnerDialog # Import progress dialog and spinner dialog
from nojoin.audio.importer import import_multiple_audio_files
from nojoin.utils.speaker_label_manager import SpeakerLabelManager
from .transcript_dialog import TranscriptViewDialog # Import the new dialog
from nojoin.search.search_logic import SearchEngine
from .participants_dialog import ParticipantsDialog
from .search_bar_widget import SearchBarWidget
from nojoin.processing.LLM_Services import get_llm_backend

logger = logging.getLogger(__name__) # Setup logger for this module

# --- Custom Click-to-Seek Slider ---
class ClickToSeekSlider(QSlider):
    """Custom QSlider that allows click-to-seek functionality."""
    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # Calculate position from click
            if self.orientation() == Qt.Horizontal:
                value = QStyle.sliderValueFromPosition(
                    self.minimum(), self.maximum(),
                    event.x(), self.width())
            else:
                value = QStyle.sliderValueFromPosition(
                    self.minimum(), self.maximum(),
                    event.y(), self.height())
            
            # Set the value and emit signals
            self.setValue(value)
            self.sliderMoved.emit(value)
            self.sliderPressed.emit()
            event.accept()
        else:
            super().mousePressEvent(event)
            
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.sliderReleased.emit()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

# --- Worker Thread for Recording ---
class RecordingWorker(QThread):
    started = Signal()
    finished = Signal(str, float, int)  # filename, duration, size
    error = Signal(str)  # error_message

    def __init__(self, recorder: 'AudioRecorder'):
        super().__init__()
        self.recorder = recorder
        self._should_stop = False

    def run(self):
        try:
            # Start recording (blocking call)
            self.started.emit()
            # Use default devices and settings for now
            success = self.recorder.start_recording()
            if not success:
                self.error.emit("Failed to start recording. Check audio devices.")
                return
            # Wait until stop is requested
            while not self._should_stop and self.recorder.is_recording:
                self.msleep(100)
            # Stop recording and get result
            result = self.recorder.stop_recording()
            if result is None:
                self.error.emit("No audio was recorded or saving failed.")
                return
            filename, duration, size = result
            self.finished.emit(filename, duration, size)
        except Exception as e:
            logging.getLogger(__name__).error(f"RecordingWorker error: {e}", exc_info=True)
            self.error.emit(str(e))

    def stop(self):
        self._should_stop = True
        if hasattr(self.recorder, 'is_recording') and self.recorder.is_recording:
            self.recorder.is_recording = False

# --- Worker Thread for Processing ---
class ProcessingWorker(QThread):
    started = Signal(str) # recording_id
    finished = Signal(str) # recording_id
    error = Signal(str, str) # recording_id, error_message
    stage_update = Signal(str) # e.g., "Transcribing...", "Diarizing..."
    stage_changed = Signal(str) # Stage name: 'vad', 'transcription', 'diarization'
    progress_update = Signal(int, float, float) # percent, elapsed, eta

    def __init__(self, recording_id, audio_path):
        super().__init__()
        self.recording_id = str(recording_id)
        self.audio_path = audio_path
        self._cancel_requested = False
        self._current_stage = None

    def request_cancel(self):
        logger.info(f"ProcessingWorker cancel requested for ID: {self.recording_id}")
        self._cancel_requested = True

    def run(self):
        if not processing_pipeline:
            self.error.emit(self.recording_id, "Processing module not loaded.")
            return
        import time
        logger.info(f"ProcessingWorker started for ID: {self.recording_id}")
        self.started.emit(self.recording_id)
        try:
            start_time = time.monotonic()
            
            def stage_callback(stage_name):
                """Callback for stage transitions"""
                self._current_stage = stage_name
                self.stage_changed.emit(stage_name)
                
            def whisper_progress_callback(percent):
                elapsed = time.monotonic() - start_time
                eta = (elapsed / percent * (100 - percent)) if percent > 0 else float('inf')
                self.progress_update.emit(percent, elapsed, eta)
                
            def diarization_progress_callback(percent):
                elapsed = time.monotonic() - start_time
                eta = (elapsed / percent * (100 - percent)) if percent > 0 else float('inf')
                self.progress_update.emit(percent, elapsed, eta)
                
            success = processing_pipeline.process_recording(
                self.recording_id,
                self.audio_path,
                whisper_progress_callback=whisper_progress_callback,
                diarization_progress_callback=diarization_progress_callback,
                stage_update_callback=self.stage_update.emit,
                stage_callback=stage_callback,
                cancel_check=lambda: self._cancel_requested
            )
            if self._cancel_requested:
                logger.info(f"ProcessingWorker cancelled after process_recording for ID: {self.recording_id}")
                db_ops.update_recording_status(self.recording_id, "Cancelled")
                self.finished.emit(self.recording_id)
                return
            if success:
                logger.info(f"ProcessingWorker finished successfully for ID: {self.recording_id}")
                self.finished.emit(self.recording_id)
        except Exception as e:
            logger.error(f"ProcessingWorker caught exception for ID {self.recording_id}: {e}", exc_info=True)
            try:
                db_ops.update_recording_status(self.recording_id, 'Error')
            except Exception as db_e:
                 logger.error(f"Failed to update status to Error after processing exception for {self.recording_id}: {db_e}")
            self.error.emit(self.recording_id, f"An unexpected error occurred: {e}")
        logger.info(f"ProcessingWorker finished run method for ID: {self.recording_id}")

# --- Model Download Worker ---
class ModelDownloadWorker(QThread):
    progress = Signal(int)  # percent
    finished = Signal()
    error = Signal(str)

    def __init__(self, model_size, device):
        super().__init__()
        self.model_size = model_size
        self.device = device

    def run(self):
        try:
            # Patch tqdm in whisper to emit progress
            import tqdm
            orig_tqdm = tqdm.tqdm
            worker = self
            class QtTqdm(orig_tqdm):
                def update(self2, n):
                    super().update(n)
                    percent = int((self2.n / self2.total) * 100) if self2.total else 0
                    worker.progress.emit(percent)
            # Patch
            import whisper.transcribe
            whisper.transcribe.tqdm.tqdm = QtTqdm
            # Actually load the model (triggers download if needed)
            whisper.load_model(self.model_size, device=self.device)
            # Restore tqdm
            whisper.transcribe.tqdm.tqdm = orig_tqdm
            self.progress.emit(100)
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))

# --- Main Window ---
class MainWindow(QMainWindow):
    chat_response_signal = Signal(str)
    chat_error_signal = Signal(str)
    def __init__(self):
        super().__init__()
        # --- Robust: Initialize all attributes used in early methods ---
        self.current_chat_history = []
        self._chat_threads = []
        self._chat_request_in_progress = False
        self.logger = logging.getLogger(__name__)
        # --- End robust early attribute init ---
        self.setWindowTitle("Nojoin")
        self.setGeometry(100, 100, 1200, 800)
        # Set minimum width for the main window
        self.setMinimumSize(1550, 500) # Increased minimum width for new right panel and to prevent cut-off

        # --- Base Spacing Unit ---
        self.BASE_SPACING = 8

        # Set application icon
        icon_path = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "NojoinLogo.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        else:
            logger.warning(f"Application icon not found at: {icon_path}")

        # --- Apply Theme (loaded from config) ---
        self.apply_theme(config_manager.get("theme", "dark"))

        # --- Initialize recorder and related variables ---
        self.recordings_dir = get_recordings_dir()
        os.makedirs(self.recordings_dir, exist_ok=True)
        try:
            self.recorder = AudioRecorder(output_dir=self.recordings_dir)
        except Exception as e:
            QMessageBox.critical(self, "Initialization Error", f"Failed to initialize audio recorder: {e}\nCheck permissions and logs.")
            sys.exit(1)

        # Initialize workers
        self.recording_worker = None
        self.processing_workers = {} # Keep track of active workers: {recording_id: worker}

        # Recording state variables
        self.is_recording = False # UI state flag
        self.recording_start_time = None # To calculate duration for timer
        self.selected_audio_path = None # For playback
        
        # --- Timer Setup ---
        self.recording_timer = QTimer(self)
        self.recording_timer.setInterval(1000) # Update every second
        self.recording_timer.timeout.connect(self.update_recording_timer_display)

        # --- Initialize DB if needed ---
        try:
            db_ops.init_db()
            logger.info("Database initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}", exc_info=True)
            QMessageBox.critical(self, "Database Error", f"Failed to initialize the database: {e}")

        # --- Setup UI and connections ---
        self.setup_ui()
        self.load_recordings()
        self.setup_worker_connections()

        # --- Playback Controller ---
        self.playback_controller = PlaybackController()
        self._setup_playback_controller_connections()

        # --- Initialize recorder pipeline ---
        self.recording_pipeline = RecordingPipeline()
        self._setup_recording_pipeline_connections()

        # --- Startup: Check for stuck/orphaned processing states ---
        stuck_processing = []
        try:
            stuck_processing = [dict(rec) for rec in db_ops.get_all_recordings() if str(dict(rec).get('status', '')).lower() == 'processing']
        except Exception as e:
            logger.error(f"Error checking for stuck processing states: {e}")
        if stuck_processing:
            logger.warning(f"Found {len(stuck_processing)} stuck/orphaned recordings in 'Processing' state at startup.")
            msg = (f"There are {len(stuck_processing)} recording(s) left in 'Processing' state from a previous session. "
                   "These may be stuck or orphaned. You can retry processing, delete them, or reset their status to 'Error'.")
            # Add Reset All button
            from PySide6.QtWidgets import QMessageBox
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Stuck Recordings Detected")
            box.setText(msg)
            box.setStandardButtons(QMessageBox.Ok)
            reset_btn = box.addButton("Reset All to Error", QMessageBox.ActionRole)
            box.exec()
            if box.clickedButton() == reset_btn:
                for rec in stuck_processing:
                    db_ops.update_recording_status(rec['id'], 'Error')
                self.load_recordings()

        # --- Search Engine and Search State ---
        self.search_engine = SearchEngine()
        self.all_meetings_cache_for_search = []  # List of dicts: {id, original_data, searchable_text}
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(300)
        self.search_timer.timeout.connect(self._perform_search)

        self.chat_response_signal.connect(self._on_chat_response)
        self.chat_error_signal.connect(self._on_chat_error)

    def apply_theme(self, theme_name):
        self.current_theme = theme_name  # Track the current theme for context menus
        apply_theme_to_widget(self, theme_name)
        # Re-apply to settings dialog if open
        if hasattr(self, 'settings_dialog') and self.settings_dialog:
            apply_theme_to_widget(self.settings_dialog, theme_name)
        # Update settings button accent color
        if hasattr(self, 'settings_button'):
            self._set_settings_button_accent()
        # Remove direct border styling from child widgets; rely on panel QSS
        # Meeting Notes
        if hasattr(self, 'meeting_notes_edit'):
            # Remove setStyleSheet for border or margin
            pass
        # Meeting Context Display (update on theme change)
        if hasattr(self, 'meeting_context_display'):
            selected_items = self.meetings_list_widget.selectedItems() if hasattr(self, 'meetings_list_widget') else []
            if selected_items:
                recording_id = selected_items[0].data(Qt.UserRole)
                recording_data = db_ops.get_recording_by_id(recording_id)
                if recording_data:
                    created_at = recording_data.get("created_at")
                    meeting_title = recording_data.get("name", "Untitled Meeting")
                    import datetime
                    date_str = "Unknown date"
                    day_str = ""
                    time_str = ""
                    tz_str = ""
                    try:
                        from zoneinfo import ZoneInfo
                        import tzlocal
                        dt = None
                        if created_at:
                            if isinstance(created_at, datetime.datetime):
                                dt = created_at
                            elif isinstance(created_at, str):
                                try:
                                    dt = datetime.datetime.fromisoformat(created_at)
                                except Exception:
                                    try:
                                        dt = datetime.datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
                                    except Exception:
                                        logger.warning(f"Could not parse created_at: {created_at}")
                            if dt and dt.tzinfo is None:
                                dt = dt.replace(tzinfo=datetime.timezone.utc)
                            local_tz = None
                            try:
                                local_tz = tzlocal.get_localzone()
                            except Exception:
                                try:
                                    local_tz = ZoneInfo(os.environ.get('TZ', 'Europe/London'))
                                except Exception:
                                    local_tz = datetime.timezone.utc
                            if dt:
                                dt_local = dt.astimezone(local_tz)
                                day_str = dt_local.strftime("%A")
                                date_str = dt_local.strftime("%d %B %Y")
                                time_str = dt_local.strftime("%H:%M")
                                tz_str = dt_local.strftime("%Z")
                    except Exception as e:
                        logger.warning(f"Error parsing created_at: {created_at} ({e})")
                        date_str = "Unknown date"
                    context_html = f"""
                    <h1>{meeting_title}</h1>
                    <div class='meta'>{day_str if day_str else ''}{', ' if day_str and date_str else ''}{date_str if date_str else ''}</div>
                    <div class='meta'>{time_str if time_str else ''} <span class='meta'>{tz_str if tz_str else ''}</span></div>
                    """.strip()
                    self._set_meeting_context_html_with_dynamic_font(context_html.strip(), theme_name)
                else:
                    self.meeting_context_display.clear()
            else:
                self.meeting_context_display.clear()
        # Speaker Relabelling: apply border to panel, not scroll area
        if hasattr(self, 'speakerLabelingPanel'):
            self.speakerLabelingPanel.setStyleSheet("")
        if hasattr(self, 'speakerLabelingScroll'):
            self.speakerLabelingScroll.setStyleSheet("")
        # Apply theme to panels
        for panel_name in ["MainPanelLeft", "MainPanelCenter", "MainPanelRight"]:
            panel = self.findChild(QFrame, panel_name)
            if panel:
                apply_theme_to_widget(panel, theme_name)
                panel.setStyleSheet("")  # Remove any direct stylesheet that could override QSS
        # 1. Theme-responsive 'Participants' title
        # if hasattr(self, 'speaker_label_title'):
        #     apply_theme_to_widget(self.speaker_label_title, theme_name)
        # 2. Theme-responsive 'Add Label' button
        if hasattr(self, 'add_label_btn'):
            apply_theme_to_widget(self.add_label_btn, theme_name)
        # --- New: Apply theme to chat header and chat display area ---
        if hasattr(self, 'chat_header_label'):
            apply_theme_to_widget(self.chat_header_label, theme_name)
        if hasattr(self, 'chat_display_area'):
            apply_theme_to_widget(self.chat_display_area, theme_name)
            # Re-render chat history with new theme if needed
            self.chat_display_area.clear()
            for msg in self.current_chat_history:
                role = msg.get("role")
                text = msg.get("parts", [{}])[0].get("text", "")
                import markdown2
                html = markdown2.markdown(text)
                from datetime import datetime
                timestamp = datetime.now().strftime("%H:%M")
                if role == "user":
                    user_html = f'<div class="chat-message user"><b>You</b> <span class="timestamp">{timestamp}</span><div class="content">{html}</div></div>'
                    self.chat_display_area.append(user_html)
                elif role == "model":
                    ai_html = f'<div class="chat-message assistant"><b>Assistant</b> <span class="timestamp">{timestamp}</span><div class="content">{html}</div></div>'
                    self.chat_display_area.append(ai_html)
                else:
                    sys_html = f'<div class="chat-message system">{html}</div>'
                    self.chat_display_area.append(sys_html)
        # --- New: Apply theme to search bar widget ---
        if hasattr(self, 'search_bar_widget'):
            from nojoin.utils.theme_utils import get_border_color
            border_color = get_border_color(theme_name)
            self.search_bar_widget.set_theme(border_color)
        # --- Apply theme to audio warning banner ---
        if hasattr(self, 'audio_warning_banner'):
            apply_theme_to_widget(self.audio_warning_banner, theme_name)

    def _set_settings_button_accent(self):
        theme = config_manager.get("theme", "dark")
        if theme == "dark":
            accent = "#ff9800"
            accent2 = "#ff6f00"
            text = "#181818"
        else:
            accent = "#007acc"
            accent2 = "#005f9e"
            text = "#ffffff"
        self.settings_button.setStyleSheet(f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {accent}, stop:1 {accent2}); color: {text}; border: none; border-radius: 6px; font-weight: bold;")

    def setup_ui(self):
        # --- Menu Bar Navigation ---
        menubar = self.menuBar()

        
        # --- Central Widget and Main Layout ---
        central_widget = QWidget()
        central_widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setCentralWidget(central_widget)
        central_layout = QVBoxLayout(central_widget)
        central_layout.setContentsMargins(self.BASE_SPACING, self.BASE_SPACING, self.BASE_SPACING, self.BASE_SPACING)
        central_layout.setSpacing(self.BASE_SPACING)

        # --- Top Controls Bar: Recording + Playback + Settings ---
        top_controls_layout = QHBoxLayout()
        top_controls_layout.setSpacing(self.BASE_SPACING) # Spacing within the top bar
        top_controls_layout.setContentsMargins(0, 0, 0, 0)
        # Settings button (icon only)
        self.settings_button = QPushButton("Settings")
        self.settings_button.setMinimumWidth(self.BASE_SPACING * 14)  # Match Transcribe button width
        self.settings_button.setFixedHeight(self.BASE_SPACING * 5)
        self.settings_button.setToolTip("Open application settings")
        # Set accent color based on theme
        self._set_settings_button_accent()
        self.settings_button.clicked.connect(self.open_settings_dialog)
        # --- Import Audio Button ---
        self.import_audio_button = QPushButton("Import Audio")
        self.import_audio_button.setMinimumWidth(self.BASE_SPACING * 14)
        self.import_audio_button.setFixedHeight(self.BASE_SPACING * 5)
        self.import_audio_button.setToolTip("Import existing audio files (MP3, WAV, etc.)")
        self.import_audio_button.clicked.connect(self.on_import_audio_clicked)
        # Transcribe button
        self.transcribe_button = QPushButton("Transcribe")
        self.transcribe_button.setToolTip("Transcribe and diarize the selected recording")
        self.transcribe_button.setEnabled(False)
        self.transcribe_button.setMinimumWidth(self.BASE_SPACING * 14) # Give text some space
        self.transcribe_button.setFixedHeight(self.BASE_SPACING * 5)
        self.transcribe_button.clicked.connect(self.on_transcribe_clicked)
        # Recording controls
        self.record_button = QPushButton("Start Meeting")
        record_pixmap = QPixmap(20, 20)
        record_pixmap.fill(Qt.transparent)
        painter = QPainter(record_pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(QColor(220, 0, 0)))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(3, 3, 14, 14)
        painter.end()
        self.record_button.setIcon(QIcon(record_pixmap))
        self.record_button.setMinimumWidth(self.BASE_SPACING * 18) # Give text space
        self.record_button.setFixedHeight(self.BASE_SPACING * 5)
        self.record_button.clicked.connect(self.toggle_recording)
        self.status_indicator = QLabel("Status: Idle")
        # Margin handled by layout/QSS
        self.timer_label = QLabel("00:00:00")
        self.timer_label.setToolTip("Elapsed recording time")
        # Font weight and margin handled by QSS
        # Playback controls
        self.play_button = QPushButton()
        self.play_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.play_button.setToolTip("Play Selected Recording")
        self.play_button.setEnabled(False)
        self.play_button.setFixedSize(self.BASE_SPACING * 5, self.BASE_SPACING * 5)
        self.play_button.clicked.connect(self.on_play_clicked)
        self.pause_button = QPushButton()
        self.pause_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        self.pause_button.setToolTip("Pause Playback")
        self.pause_button.setEnabled(False)
        self.pause_button.setFixedSize(self.BASE_SPACING * 5, self.BASE_SPACING * 5)
        self.pause_button.clicked.connect(self.on_pause_clicked)
        self.stop_button = QPushButton()
        self.stop_button.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
        self.stop_button.setToolTip("Stop Playback")
        self.stop_button.setEnabled(False)
        self.stop_button.setFixedSize(self.BASE_SPACING * 5, self.BASE_SPACING * 5)
        self.stop_button.clicked.connect(self.on_stop_clicked)
        # Audio seeker slider (with click-to-seek functionality)
        self.seek_slider = ClickToSeekSlider(Qt.Horizontal)
        self.seek_slider.setMinimum(0)
        self.seek_slider.setMaximum(100)
        self.seek_slider.setValue(0)
        self.seek_slider.setEnabled(False)
        self.seek_slider.setFixedWidth(300)  # Wider for visibility
        self.seek_slider.setCursor(Qt.PointingHandCursor)  # Show hand cursor to indicate clickability
        self.seek_slider.setToolTip("Click to seek or drag to navigate through audio")
        self.seek_slider.sliderMoved.connect(self.handle_seek_slider_moved)
        self.seek_slider.sliderPressed.connect(self.handle_seek_slider_pressed)
        self.seek_slider.sliderReleased.connect(self.handle_seek_slider_released)
        self.seeker_user_is_dragging = False
        self.seek_time_label = QLabel("00:00 / 00:00")
        # Volume control
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setMinimum(0)
        self.volume_slider.setMaximum(100)
        self.volume_slider.setValue(75)
        self.volume_slider.setFixedWidth(80)
        self.volume_slider.valueChanged.connect(self._handle_volume_changed)
        self.volume_label = QLabel("75%")
        # Add widgets to top bar
        top_controls_layout.addWidget(self.transcribe_button)
        top_controls_layout.addWidget(self.record_button)
        top_controls_layout.addWidget(self.status_indicator)
        top_controls_layout.addWidget(self.timer_label)
        top_controls_layout.addSpacing(self.BASE_SPACING * 2.5) # Extra space before playback

        # --- Playback Control Group ---
        playback_group = QHBoxLayout()
        playback_group.setSpacing(self.BASE_SPACING)
        playback_group.setContentsMargins(0, 0, 0, 0)
        playback_group.addWidget(self.play_button)
        playback_group.addWidget(self.pause_button)
        playback_group.addWidget(self.stop_button)
        playback_group.addSpacing(self.BASE_SPACING * 1.5) # Space before slider
        playback_group.addWidget(self.seek_slider)
        playback_group.addWidget(self.seek_time_label)
        # Add separator between timestamp and volume controls
        separator = QFrame()
        separator.setFrameShape(QFrame.VLine)
        separator.setFrameShadow(QFrame.Sunken)
        separator.setFixedHeight(20)
        playback_group.addWidget(separator)
        playback_group.addWidget(QLabel("Vol:"))
        playback_group.addWidget(self.volume_slider)
        playback_group.addWidget(self.volume_label)
        # Wrap in a widget to set minimum width for the group
        playback_widget = QWidget()
        playback_widget.setLayout(playback_group)
        playback_widget.setMinimumWidth(450) # Minimum width for playback controls + slider

        top_controls_layout.addWidget(playback_widget)
        top_controls_layout.addStretch() # Add stretch *before* settings button
        top_controls_layout.addWidget(self.import_audio_button)
        top_controls_layout.addWidget(self.settings_button) # Move settings button to the end
        central_layout.addLayout(top_controls_layout)

        # --- Audio Detection Warning Banner ---
        self.audio_warning_banner = QFrame()
        self.audio_warning_banner.setObjectName("AudioWarningBanner")
        self.audio_warning_banner.setVisible(False)  # Hidden by default
        self.audio_warning_banner.setMaximumHeight(40)
        self.audio_warning_banner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        
        warning_layout = QHBoxLayout(self.audio_warning_banner)
        warning_layout.setContentsMargins(self.BASE_SPACING, 4, self.BASE_SPACING, 4)
        
        # Warning icon
        warning_icon_label = QLabel()
        warning_icon_label.setPixmap(self.style().standardIcon(QStyle.SP_MessageBoxWarning).pixmap(20, 20))
        warning_layout.addWidget(warning_icon_label)
        
        # Warning message (centered with stretch on both sides)
        warning_layout.addStretch()
        self.audio_warning_label = QLabel("No audio detected")
        self.audio_warning_label.setObjectName("AudioWarningLabel")
        self.audio_warning_label.setAlignment(Qt.AlignCenter)
        warning_layout.addWidget(self.audio_warning_label)
        warning_layout.addStretch()
        
        # Close button (smaller)
        close_warning_btn = QPushButton("✕")
        close_warning_btn.setObjectName("CloseWarningButton")
        close_warning_btn.setFixedSize(15, 15)
        close_warning_btn.setFlat(True)
        close_warning_btn.clicked.connect(lambda: self.audio_warning_banner.setVisible(False))
        close_warning_btn.setVisible(False)  # Hide the dismiss button
        warning_layout.addWidget(close_warning_btn)
        
        
        central_layout.addWidget(self.audio_warning_banner)

        # --- Audio Level Monitoring ---
        self.audio_level_timer = QTimer(self)
        self.audio_level_timer.setInterval(100)  # Check every 100ms
        self.audio_level_timer.timeout.connect(self._check_audio_levels)
        
        self.no_audio_timer = QTimer(self)
        self.no_audio_timer.setSingleShot(True)
        self.no_audio_timer.setInterval(10000)  # 10 seconds
        self.no_audio_timer.timeout.connect(self._show_audio_warning)
        
        self.last_input_level = 0.0
        self.last_output_level = 0.0
        self.audio_detected_recently = False

        # --- Separator Line ---
        # Spacing handled by central_layout.setSpacing
        line1 = QFrame()
        line1.setFrameShape(QFrame.HLine)
        line1.setFrameShadow(QFrame.Sunken)
        central_layout.addWidget(line1)

        # --- Main Display Area: Static Panes (Meeting List | Meeting Notes | Meeting Chat) ---
        main_display_layout = QHBoxLayout()
        main_display_layout.setContentsMargins(5, 5, 5, 5)
        main_display_layout.setSpacing(0)  # No horizontal spacing between panes

        # --- Left: Meetings List (Card Style) ---
        left_panel = QFrame()
        left_panel.setObjectName("MainPanelLeft")
        left_panel.setStyleSheet("padding: 0px;")
        left_panel.setMinimumWidth(360)
        left_panel.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_panel.setContentsMargins(0, 0, 0, 0)
        # Remove setMaximumWidth and setFixedWidth

        # --- Search Bar Area (now a separate widget) ---
        self.search_bar_widget = SearchBarWidget()
        self.search_bar_widget.setMinimumWidth(360)
        self.search_bar_widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        if hasattr(self.search_bar_widget, 'layout') and self.search_bar_widget.layout() is not None:
            self.search_bar_widget.layout().setContentsMargins(0, 2, 0, 2)
        self.search_bar_widget.text_changed.connect(self._on_search_text_changed)
        self.search_bar_widget.cleared.connect(self._clear_search)
        left_layout.addWidget(self.search_bar_widget)

        self.meetings_list_widget = QListWidget()
        self.meetings_list_widget.setSelectionMode(QListWidget.SingleSelection)
        self.meetings_list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.meetings_list_widget.setMinimumWidth(360)
        self.meetings_list_widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.meetings_list_widget.itemSelectionChanged.connect(self.handle_meeting_selection_changed)
        # Remove direct border styling from meetings_list_widget (QListWidget)
        self.meetings_list_widget.setStyleSheet("QListWidget { border: none; border-radius: 10px; padding: 0px; background: transparent; }")
        left_layout.addWidget(self.meetings_list_widget, 1)
        # Remove setMaximumWidth and setFixedWidth
        main_display_layout.addWidget(left_panel, 1)

        # --- Center: Meeting Notes ---
        center_panel = QFrame()
        center_panel.setObjectName("MainPanelCenter")
        center_panel.setStyleSheet("padding: 0px;")
        center_panel.setMinimumWidth(500)
        center_panel.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        meeting_notes_layout = QVBoxLayout(center_panel)
        meeting_notes_layout.setContentsMargins(0, 0, 0, 0)
        meeting_notes_layout.setSpacing(0)
        self.meeting_context_display = QTextEdit()
        self.meeting_context_display.setReadOnly(True)
        self.meeting_context_display.setFrameStyle(QTextEdit.NoFrame)
        self.meeting_context_display.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.meeting_context_display.setObjectName("MeetingContextDisplay")
        # Remove setStyleSheet for padding or border
        self.meeting_context_display.setMaximumHeight(110)  # Restore original max height
        meeting_notes_layout.addWidget(self.meeting_context_display)
        self.meeting_tags_widget = QWidget()
        self.meeting_tags_layout = QHBoxLayout(self.meeting_tags_widget)
        self.meeting_tags_layout.setContentsMargins(0, 0, 0, 0)
        self.meeting_tags_layout.setSpacing(6)
        self.meeting_tags_widget.setStyleSheet("")
        meeting_notes_layout.addWidget(self.meeting_tags_widget)
        self.meeting_notes_edit = QTextEdit()
        self.meeting_notes_edit.setObjectName("MeetingNotesEdit")
        self.meeting_notes_edit.setPlaceholderText("Meeting notes will appear here. Right-click a recording to generate or view notes.")
        self.meeting_notes_edit.setReadOnly(False)  # Make editable
        self.meeting_notes_edit.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.meeting_notes_edit.setVisible(True)
        # Remove setStyleSheet for border or padding
        # --- Add custom context menu for rich text formatting ---
        self.meeting_notes_edit.setContextMenuPolicy(Qt.CustomContextMenu)
        self.meeting_notes_edit.customContextMenuRequested.connect(self._show_notes_context_menu)
        # --- Keyboard shortcuts for formatting ---
        QShortcut(QKeySequence("Ctrl+B"), self.meeting_notes_edit, activated=lambda: self._format_notes_selection("bold"))
        QShortcut(QKeySequence("Ctrl+I"), self.meeting_notes_edit, activated=lambda: self._format_notes_selection("italic"))
        QShortcut(QKeySequence("Ctrl+U"), self.meeting_notes_edit, activated=lambda: self._format_notes_selection("underline"))
        QShortcut(QKeySequence("Ctrl+Shift+B"), self.meeting_notes_edit, activated=lambda: self._format_notes_selection("bullet"))
        QShortcut(QKeySequence("Ctrl+Shift+N"), self.meeting_notes_edit, activated=lambda: self._format_notes_selection("numbered"))
        # --- Debounced autosave setup ---
        self._notes_autosave_timer = QTimer(self)
        self._notes_autosave_timer.setSingleShot(True)
        self._notes_autosave_timer.setInterval(1500)  # 1.5 seconds debounce
        self.meeting_notes_edit.textChanged.connect(self._on_notes_edited_autosave)
        self._notes_last_saved_content = ""
        meeting_notes_layout.addWidget(self.meeting_notes_edit, 3)
        main_display_layout.addWidget(center_panel, 2)

        # --- Right: Meeting Chat ---
        chat_panel = QFrame()
        chat_panel.setObjectName("MainPanelRight")
        chat_panel.setStyleSheet("padding: 4px;")
        chat_panel.setMinimumWidth(360)
        chat_panel.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        chat_layout = QVBoxLayout(chat_panel)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(0)
        self.chat_display_area = QTextEdit()
        self.chat_display_area.setReadOnly(True)
        self.chat_display_area.setFrameStyle(QTextEdit.NoFrame)
        self.chat_display_area.setObjectName("ChatDisplayArea")
        self.chat_display_area.setStyleSheet("padding: 4px;")
        self.chat_display_area.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        chat_layout.addWidget(self.chat_display_area, 3)
        # --- Chat Input Area (vertical layout) ---
        self.chat_panel = QFrame()
        self.chat_panel.setStyleSheet("")
        chat_input_vlayout = QVBoxLayout(self.chat_panel)
        chat_input_vlayout.setContentsMargins(0, 0, 0, 0)
        chat_input_vlayout.setSpacing(4)
        # Text entry field
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Ask a question about this meeting...")
        self.chat_input.setObjectName("MeetingChatLineEdit")
        self.chat_input.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        chat_input_vlayout.addWidget(self.chat_input)
        # Buttons row (horizontal layout)
        chat_buttons_layout = QHBoxLayout()
        chat_buttons_layout.setContentsMargins(0, 0, 0, 0)
        chat_buttons_layout.setSpacing(8)
        self.chat_send_button = QPushButton()
        self.chat_send_button.setToolTip("Send/Ask")
        self.chat_send_button.setIcon(self.style().standardIcon(QStyle.SP_ArrowUp))
        self.chat_send_button.setObjectName("ChatSendButton")
        self.chat_send_button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.clear_chat_button = QPushButton("Clear Chat")
        self.clear_chat_button.setToolTip("Clear all chat history for this meeting")
        self.clear_chat_button.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        chat_buttons_layout.addWidget(self.chat_send_button)
        chat_buttons_layout.addWidget(self.clear_chat_button)
        chat_input_vlayout.addLayout(chat_buttons_layout)
        self.clear_chat_button.clicked.connect(self.clear_chat_for_selected_meeting)
        self.clear_chat_button.setEnabled(False)
        self.chat_panel.setLayout(chat_input_vlayout)
        chat_layout.addWidget(self.chat_panel, 0)
        main_display_layout.addWidget(chat_panel, 1)

        central_layout.addLayout(main_display_layout)

        # --- Status Bar ---
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

        # Track notes edit state
        self.notes_have_been_edited = False
        self.meeting_notes_edit.textChanged.connect(self.on_notes_edited)

        # Add context menu to meetings list
        self.meetings_list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.meetings_list_widget.customContextMenuRequested.connect(self.show_meeting_context_menu)

        # Apply theme to all widgets (including borders) at the very end
        self.apply_theme(config_manager.get("theme", "dark"))
        # Highlight entry fields (theme-aware)
        from nojoin.utils.theme_utils import get_border_color
        border_color = get_border_color(config_manager.get("theme", "dark"))
        border_radius = "8px"
        self.search_bar_widget.set_theme(border_color, border_radius)
        # Connect chat send
        self.chat_send_button.clicked.connect(self._handle_send_chat_message)
        self.chat_input.returnPressed.connect(self._handle_send_chat_message)

        self._setup_notes_autosave()

    def _set_meeting_context_html_with_dynamic_font(self, context_html, theme):
        """
        Set the meeting context HTML, dynamically adjusting the <h1> font size so the content fits within 110px.
        """
        min_font_size = 12
        max_font_size = 24
        max_height = 110
        font_size = max_font_size
        # Try decreasing font size until it fits or reach min
        while font_size >= min_font_size:
            # Inject font size into <h1> style
            html_with_size = context_html.replace(
                '<h1>', f'<h1 style="font-size: {font_size}px; margin: 0; padding: 0;">'
            )
            themed_html = wrap_html_body(html_with_size, theme)
            self.meeting_context_display.setHtml(themed_html)
            doc_height = self.meeting_context_display.document().size().height()
            if doc_height <= max_height:
                break
            font_size -= 1
        # If even min size doesn't fit, just use min size
        if font_size < min_font_size:
            html_with_size = context_html.replace(
                '<h1>', f'<h1 style="font-size: {min_font_size}px; margin: 0; padding: 0;">'
            )
            themed_html = wrap_html_body(html_with_size, theme)
            self.meeting_context_display.setHtml(themed_html)

    def toggle_recording(self):
        if not self.is_recording:
            self.status_bar.showMessage("Starting recording...")
            self.status_indicator.setText("Status: Starting...")
            self.timer_label.setText("00:00:00")
            self.record_button.setText("End Meeting")
            self.record_button.setEnabled(False)
            self.recording_pipeline.start()
        else:
            self.status_bar.showMessage("Stopping recording...")
            self.status_indicator.setText("Status: Stopping...")
            self.record_button.setEnabled(False)
            self.recording_timer.stop()
            # Dismiss audio warning banner when ending recording
            self.audio_warning_banner.setVisible(False)
            self.recording_pipeline.stop()

    def handle_recording_started(self):
        self.is_recording = True
        self.recording_start_time = time.monotonic()
        self.status_indicator.setText("Status: Recording")
        self.status_bar.showMessage("Recording in progress...")
        self.record_button.setEnabled(True)
        self.record_button.setText("End Meeting")
        self.update_recording_timer_display()
        self.recording_timer.start()
        logger.info("UI: Recording started signal received.")
        
        # Start audio level monitoring
        self.audio_level_timer.start()
        self.no_audio_timer.start()
        self.audio_warning_banner.setVisible(False)
        self.audio_detected_recently = False

    def handle_recording_finished(self, filename, duration, size):
        self.is_recording = False
        self.recording_timer.stop()
        self.record_button.setText("Start Meeting")
        self.status_indicator.setText("Status: Idle")
        self.timer_label.setText("00:00:00")
        self.record_button.setEnabled(True)
        base_filename = os.path.basename(filename)
        self.status_bar.showMessage(f"Recording saved: {base_filename} ({duration:.1f}s)")
        logger.info(f"UI: Recording finished: {filename}, Duration: {duration}, Size: {size}")
        
        # Stop audio level monitoring
        self.audio_level_timer.stop()
        self.no_audio_timer.stop()
        self.audio_warning_banner.setVisible(False)
        
        # No DB update needed here; pipeline handles it
        self.load_recordings()

        # --- Auto-transcribe if enabled ---
        if config_manager.get("auto_transcribe_on_recording_finish", False):
            # Find the most recent recording (should be the one just added)
            from nojoin.db import database as db_ops
            recordings = db_ops.get_all_recordings()
            if recordings:
                latest = recordings[0]  # get_all_recordings returns newest first
                # Convert to dict if needed
                if not isinstance(latest, dict):
                    latest = dict(latest)
                recording_id = latest['id']
                audio_path = latest.get('audio_path')
                if audio_path and os.path.exists(from_project_relative_path(audio_path)):
                    logger.info(f"Auto-transcribe enabled: starting processing for recording ID {recording_id}")
                    self.process_selected_recording(recording_id, latest)
                else:
                    logger.warning(f"Auto-transcribe: audio file not found for recording ID {recording_id}")

    def handle_recording_error(self, error_message):
        self.is_recording = False
        self.recording_timer.stop()
        self.record_button.setText("Start Meeting")
        self.status_indicator.setText("Status: Error")
        self.timer_label.setText("00:00:00")
        self.record_button.setEnabled(True)
        self.status_bar.showMessage(f"Error: {error_message}")
        
        # Stop audio level monitoring
        self.audio_level_timer.stop()
        self.no_audio_timer.stop()
        self.audio_warning_banner.setVisible(False)
        
        QMessageBox.warning(self, "Recording Error", error_message)
        print(f"UI: Recording error: {error_message}")

    def update_recording_timer_display(self):
        if self.is_recording and self.recording_start_time is not None:
            elapsed_seconds = int(time.monotonic() - self.recording_start_time)
            # Format as HH:MM:SS
            elapsed_time_str = str(timedelta(seconds=elapsed_seconds))
            self.timer_label.setText(elapsed_time_str)
        else:
            # Should not happen if timer is stopped correctly, but reset just in case
            self.timer_label.setText("00:00:00")

    def setup_worker_connections(self):
        """Connect signals for a newly created worker."""
        if self.recording_worker:
            self.recording_worker.started.connect(self.handle_recording_started)
            self.recording_worker.finished.connect(self.handle_recording_finished)
            self.recording_worker.error.connect(self.handle_recording_error)
            # Ensure thread cleanup when finished
            self.recording_worker.finished.connect(self.recording_worker.deleteLater)
            self.recording_worker.error.connect(self.recording_worker.deleteLater)

    def rename_recording_dialog(self, recording_id: str, recording_data: dict):
        """Opens a dialog to rename a recording."""
        current_name = recording_data.get('name', '') # Use .get for safety if 'name' might be missing
        if not isinstance(current_name, str): # Ensure it's a string
            current_name = str(current_name)

        new_name, ok = QInputDialog.getText(self, 
                                            "Rename Recording", 
                                            "Enter new name for the recording:", 
                                            QLineEdit.EchoMode.Normal, # QLineEdit.Normal for PySide6
                                            current_name)

        if ok and new_name:
            new_name = new_name.strip()
            if new_name and new_name != current_name:
                if db_ops.update_recording_name(recording_id, new_name):
                    self.status_bar.showMessage(f"Recording '{current_name}' renamed to '{new_name}'.", 3000)
                    self.load_recordings() # Refresh the list
                    # If the renamed recording was selected, update the displayed info
                    selected_items = self.meetings_list_widget.selectedItems()
                    if selected_items and selected_items[0].data(Qt.UserRole) == recording_id:
                        updated_recording_data = db_ops.get_recording_by_id(recording_id)
                        if updated_recording_data:
                            self.handle_meeting_selection_changed()
                        else:
                            self.meeting_notes_edit.clear()
                else:
                    QMessageBox.warning(self, "Rename Failed", "Could not rename the recording in the database.")
            elif new_name == current_name:
                self.status_bar.showMessage("Recording name unchanged.", 2000)
            else: # Empty new name after strip
                QMessageBox.warning(self, "Invalid Name", "Recording name cannot be empty.")
        elif ok and not new_name.strip(): # User entered only spaces or nothing
             QMessageBox.warning(self, "Invalid Name", "Recording name cannot be empty.")

    def delete_selected_recording(self, recording_id, recording_data):
        """Handles deletion of the selected recording."""
        str_recording_id = str(recording_id) # Ensure string ID
        # Always allow deletion, but warn if processing
        status = recording_data.get('status', '').lower()
        is_processing = status == 'processing'
        worker = self.processing_workers.get(str_recording_id)
        warning_msg = f"Are you sure you want to permanently delete recording '{recording_data.get('name', f'ID {recording_id}')}'?\nThis will also delete the associated audio file and cannot be undone."
        if is_processing:
            warning_msg = ("This recording is currently being processed. Deleting it will stop processing and remove all associated data.\n\n" + warning_msg)
        reply = QMessageBox.question(self,
                                   "Confirm Delete",
                                   warning_msg,
                                   QMessageBox.Yes | QMessageBox.No,
                                   QMessageBox.No)

        if reply == QMessageBox.Yes:
            logger.info(f"Confirmed deletion for recording ID: {recording_id}")
            # Cancel processing worker if running
            if worker:
                logger.info(f"Cancelling processing worker for recording ID: {recording_id}")
                worker.request_cancel()
                self.processing_workers.pop(str_recording_id, None)
            audio_path = recording_data.get('audio_path')
            abs_audio_path = from_project_relative_path(audio_path) if audio_path else None
            if db_ops.delete_recording(recording_id):
                logger.info(f"Successfully deleted recording ID {recording_id} from database.")
                if abs_audio_path and os.path.exists(abs_audio_path):
                    try:
                        os.remove(abs_audio_path)
                        logger.info(f"Successfully deleted audio file: {abs_audio_path}")
                    except OSError as e:
                        logger.error(f"Error deleting audio file {abs_audio_path}: {e}")
                        QMessageBox.warning(self, "File Deletion Error", f"Could not delete the audio file:\n{abs_audio_path}\n\nPlease remove it manually.")
                self.load_recordings()
                self.meetings_list_widget.clearSelection()
                self.meeting_notes_edit.clear()
            else:
                logger.error(f"Failed to delete recording ID {recording_id} from database.")
                QMessageBox.critical(self, "Database Error", "Failed to delete the recording from the database.")
        else:
            logger.info(f"Deletion cancelled for recording ID: {recording_id}")

    def _context_view_edit_meeting_notes(self, recording_id, recording_data):
        notes_entry = db_ops.get_meeting_notes_for_recording(recording_id)
        if notes_entry:
            try:
                import markdown2  # Local import for performance
                html_notes = markdown2.markdown(notes_entry['notes'])
                self.meeting_notes_edit.setHtml(wrap_html_body(html_notes, config_manager.get("theme", "dark")))
            except Exception as e:
                self.meeting_notes_edit.setHtml(wrap_html_body(f"<p>Error displaying notes: {e}</p><pre>{notes_entry['notes']}</pre>", config_manager.get("theme", "dark")))
        else:
            self.meeting_notes_edit.setHtml(wrap_html_body("<p>No meeting notes available. Right-click the recording to generate notes.</p>", config_manager.get("theme", "dark")))
        
        self.meeting_notes_edit.setVisible(True)
        self.notes_have_been_edited = False

    def _context_show_diarized_transcript_dialog(self, recording_id, recording_data):
        """Shows the diarized transcript in a new dialog window."""
        logger.info(f"Showing diarized transcript dialog for recording ID: {recording_id}")
        
        diarized_transcript_path = recording_data.get("diarized_transcript_path")
        abs_diarized_transcript_path = from_project_relative_path(diarized_transcript_path) if diarized_transcript_path else None

        if not abs_diarized_transcript_path or not os.path.exists(abs_diarized_transcript_path):
            QMessageBox.warning(self, "Transcript Missing", "Diarized transcript file not found for this recording.")
            return

        dialog_title = f"Diarized Transcript - {recording_data.get('name', f'ID {recording_id}')}"
        # Pass recording_id to ensure latest speaker names are fetched
        from .transcript_dialog import TranscriptViewDialog
        dialog = TranscriptViewDialog(window_title=dialog_title, parent=self, recording_id=recording_id)
        dialog.exec()

    def on_generate_meeting_notes_clicked(self, recording_id=None, recording_data=None):
        if recording_id is None or recording_data is None:
            selected_items = self.meetings_list_widget.selectedItems()
            if not selected_items:
                QMessageBox.information(self, "No Selection", "Please select a recording to generate meeting notes.")
                return
            item = selected_items[0]
            recording_id = item.data(Qt.UserRole)
            recording_data = db_ops.get_recording_by_id(recording_id)
        diarized_transcript_path = recording_data.get("diarized_transcript_path")
        abs_diarized_transcript_path = from_project_relative_path(diarized_transcript_path) if diarized_transcript_path else None
        provider = config_manager.get("llm_provider", "gemini")
        api_key = config_manager.get(f"{provider}_api_key")
        model = config_manager.get(f"{provider}_model", "gemini-2.5-flash-preview-05-20")
        if not api_key or not abs_diarized_transcript_path or not os.path.exists(abs_diarized_transcript_path):
            transcript_html = self.load_transcript(recording_id)
            msg = (f"<b>No API key provided for {provider.title()}.</b> Displaying diarized transcript instead of meeting notes.")
            themed_html = wrap_html_body(f"<p>{msg}</p>" + transcript_html, config_manager.get("theme", "dark"))
            self.meeting_notes_edit.setHtml(themed_html)
            self.meeting_notes_edit.setVisible(True)
            self.notes_have_been_edited = False
            self.status_bar.showMessage(f"Meeting notes not generated. No API key for {provider.title()}.", 3000)
            return
        with open(abs_diarized_transcript_path, 'r', encoding='utf-8') as f:
            transcript = f.read()
        spinner = GeminiNotesSpinnerDialog(self)
        spinner.show()
        QApplication.processEvents()  # Ensure spinner is shown
        try:
            backend = get_llm_backend(provider, api_key=api_key, model=model)
            mapping, notes = backend.infer_speakers_and_generate_notes(transcript)
            db_ops.add_meeting_notes(recording_id, provider, model, notes)
            try:
                import markdown2  # Local import for performance
                html = markdown2.markdown(notes)
            except Exception:
                html = f"<pre>{notes}</pre>"
            html = wrap_html_body(html, config_manager.get("theme", "dark"))
            self.meeting_notes_edit.setHtml(html)
            self.meeting_notes_edit.setVisible(True)
            self.notes_have_been_edited = False
            self.status_bar.showMessage("Meeting notes generated.", 3000)
        except Exception as e:
            logger.error(f"Failed to generate meeting notes: {e}")
            placeholder = (f"<p><b>Meeting notes cannot be generated right now.</b><br>"
                           f"Please check your {provider.title()} API key in settings or view the raw diarized transcript via the context menu.")
            themed_html = wrap_html_body(placeholder, config_manager.get("theme", "dark"))
            self.meeting_notes_edit.setHtml(themed_html)
            self.meeting_notes_edit.setVisible(True)
            self.notes_have_been_edited = False
            self.status_bar.showMessage("Failed to generate meeting notes.", 3000)
        finally:
            spinner.close()

    def on_regenerate_meeting_notes_clicked(self, recording_id=None, recording_data=None):
        if recording_id is None or recording_data is None:
            selected_items = self.meetings_list_widget.selectedItems()
            if not selected_items:
                QMessageBox.information(self, "No Selection", "Please select a recording to regenerate meeting notes.")
                return
            item = selected_items[0]
            recording_id = item.data(Qt.UserRole)
            recording_data = db_ops.get_recording_by_id(recording_id)
        diarized_transcript_path = recording_data.get("diarized_transcript_path")
        abs_diarized_transcript_path = from_project_relative_path(diarized_transcript_path) if diarized_transcript_path else None
        provider = config_manager.get("llm_provider", "gemini")
        api_key = config_manager.get(f"{provider}_api_key")
        model = config_manager.get(f"{provider}_model", "gemini-2.5-flash-preview-05-20")
        if not api_key or not abs_diarized_transcript_path or not os.path.exists(abs_diarized_transcript_path):
            transcript_html = self.load_transcript(recording_id)
            msg = (f"<b>No API key provided for {provider.title()}.</b> Displaying diarized transcript instead of meeting notes.")
            themed_html = wrap_html_body(f"<p>{msg}</p>" + transcript_html, config_manager.get("theme", "dark"))
            self.meeting_notes_edit.setHtml(themed_html)
            self.meeting_notes_edit.setVisible(True)
            self.notes_have_been_edited = False
            self.status_bar.showMessage(f"Meeting notes not generated. No API key for {provider.title()}.", 3000)
            return
        reply = QMessageBox.question(self, "Regenerate Notes", "Regenerating will overwrite any unsaved edits. Continue?", QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        with open(abs_diarized_transcript_path, 'r', encoding='utf-8') as f:
            transcript = f.read()
        spinner = GeminiNotesSpinnerDialog(self)
        spinner.show()
        QApplication.processEvents()  # Ensure spinner is shown
        try:
            backend = get_llm_backend(provider, api_key=api_key, model=model)
            mapping, notes = backend.infer_speakers_and_generate_notes(transcript)
            db_ops.add_meeting_notes(recording_id, provider, model, notes)
            try:
                import markdown2  # Local import for performance
                html = markdown2.markdown(notes)
            except Exception:
                html = f"<pre>{notes}</pre>"
            html = wrap_html_body(html, config_manager.get("theme", "dark"))
            self.meeting_notes_edit.setHtml(html)
            self.meeting_notes_edit.setVisible(True)
            self.notes_have_been_edited = False
            self.status_bar.showMessage("Meeting notes generated.", 3000)
        except Exception as e:
            logger.error(f"Failed to regenerate meeting notes: {e}")
            placeholder = (f"<p><b>Meeting notes cannot be generated right now.</b><br>"
                           f"Please check your {provider.title()} API key in settings or view the raw diarized transcript via the context menu.")
            themed_html = wrap_html_body(placeholder, config_manager.get("theme", "dark"))
            self.meeting_notes_edit.setHtml(themed_html)
            self.meeting_notes_edit.setVisible(True)
            self.notes_have_been_edited = False
            self.status_bar.showMessage("Failed to generate meeting notes.", 3000)
        finally:
            spinner.close()

    def on_notes_edited(self):
        self.notes_have_been_edited = True

    def play_selected_recording(self, recording_id, recording_data):
        """Placeholder function to handle playing audio for the selected recording."""
        # This is now handled by the Play button click and selection changed signal
        logger.debug(f"play_selected_recording called for {recording_id} (now handled by button).")
        pass

    # --- Playback Button Slots ---
    @Slot()
    def on_play_clicked(self):
        if not self.selected_audio_path:
            logger.warning("Play clicked but no audio path selected.")
            return
        # If playback is paused, resume. Otherwise, play from start.
        if hasattr(self.playback_controller, 'playback') and self.playback_controller.playback.paused:
            self.playback_controller.resume()
        else:
            duration = getattr(self, '_playback_duration', 0.0)
            self.playback_controller.play(self.selected_audio_path, start_time=0.0, duration=duration)

    @Slot()
    def on_pause_clicked(self):
        self.playback_controller.pause()

    @Slot()
    def on_stop_clicked(self):
        self.playback_controller.stop()

    # --- Processing Worker Slots ---
    @Slot(str)
    def handle_processing_started(self, recording_id):
        self.update_table_status(recording_id, "Processing")

    @Slot(str)
    def handle_processing_finished(self, recording_id):
        logger.info(f"handle_processing_finished received for ID: {recording_id}")

    @Slot(str, str)
    def handle_processing_error(self, recording_id, error_message):
        logger.error(f"handle_processing_error received for ID: {recording_id}: {error_message}")

    # --- Utility to update status in table model --- 
    # (Alternative to full reload after status changes)
    def update_table_status(self, recording_id: str, new_status: str):
        # With the list widget, just reload the recordings to reflect status changes
        self.load_recordings()

    # --- Playback Controller Slots ---
    def _setup_playback_controller_connections(self):
        pc = self.playback_controller
        pc.playback_started.connect(self._on_playback_started)
        pc.playback_paused.connect(self._on_playback_paused)
        pc.playback_resumed.connect(self._on_playback_resumed)
        pc.playback_stopped.connect(self._on_playback_stopped)
        pc.playback_finished.connect(self._on_playback_finished)
        pc.playback_error.connect(self._on_playback_error)
        pc.playback_position_changed.connect(self._on_playback_position_changed)

    def _on_playback_started(self):
        self.play_button.setEnabled(False)
        self.pause_button.setEnabled(True)
        self.stop_button.setEnabled(True)
        self.seek_slider.setEnabled(True)
    def _on_playback_paused(self):
        self.play_button.setEnabled(True)
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(True)
    def _on_playback_resumed(self):
        self.play_button.setEnabled(False)
        self.pause_button.setEnabled(True)
        self.stop_button.setEnabled(True)
    def _on_playback_stopped(self):
        self.play_button.setEnabled(bool(self.selected_audio_path))
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.seek_slider.setEnabled(True)
        # Reset the seeker bar to the beginning
        self.seek_slider.setValue(0)
        self.update_seek_time_label(0, getattr(self, '_playback_duration', 0.0))
    def _on_playback_finished(self):
        self._on_playback_stopped()
    def _on_playback_error(self, msg):
        QMessageBox.warning(self, "Playback Error", msg)
        self._on_playback_stopped()
    def _on_playback_position_changed(self, seconds):
        if not self.seeker_user_is_dragging:
            self.seek_slider.setValue(int(seconds * 1000))
            self.update_seek_time_label(seconds, self.seek_slider.maximum() / 1000.0)

    def load_recordings(self):
        self._populate_search_cache()
        self._update_meetings_list([m['original_data'] for m in self.all_meetings_cache_for_search])

    def handle_meeting_selection_changed(self):
        selected_items = self.meetings_list_widget.selectedItems()
        if not selected_items:
            self.meeting_context_display.clear()
            self.meeting_notes_edit.clear()
            # --- Clear chat display and history on no selection ---
            self.current_chat_history = []
            if hasattr(self, 'chat_display_area'):
                self.chat_display_area.clear()
            # --- Disable Clear Chat button when no meeting selected ---
            if hasattr(self, 'clear_chat_button'):
                self.clear_chat_button.setEnabled(False)
            return
        item = selected_items[0]
        recording_id = item.data(Qt.UserRole)
        recording_data = db_ops.get_recording_by_id(recording_id)
        if not recording_data:
            self.meeting_context_display.clear()
            self.meeting_notes_edit.clear()
            # --- Clear chat display and history on invalid selection ---
            self.current_chat_history = []
            if hasattr(self, 'chat_display_area'):
                self.chat_display_area.clear()
            return
        # --- Context Info ---
        created_at = recording_data.get("created_at")
        meeting_title = recording_data.get("name", "Untitled Meeting")
        import datetime
        date_str = "Unknown date"
        day_str = ""
        time_str = ""
        tz_str = ""
        try:
            from zoneinfo import ZoneInfo
            import tzlocal
            dt = None
            if created_at:
                if isinstance(created_at, datetime.datetime):
                    dt = created_at
                elif isinstance(created_at, str):
                    try:
                        dt = datetime.datetime.fromisoformat(created_at)
                    except Exception:
                        try:
                            dt = datetime.datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
                        except Exception:
                            logger.warning(f"Could not parse created_at: {created_at}")
                    if dt and dt.tzinfo is None:
                        dt = dt.replace(tzinfo=datetime.timezone.utc)
                    local_tz = None
                    try:
                        local_tz = tzlocal.get_localzone()
                    except Exception:
                        try:
                            local_tz = ZoneInfo(os.environ.get('TZ', 'Europe/London'))
                        except Exception:
                            local_tz = datetime.timezone.utc
                    if dt:
                        dt_local = dt.astimezone(local_tz)
                        day_str = dt_local.strftime("%A")
                        date_str = dt_local.strftime("%d %B %Y")
                        time_str = dt_local.strftime("%H:%M")
                        tz_str = dt_local.strftime("%Z")
        except Exception as e:
            logger.warning(f"Error parsing created_at: {created_at} ({e})")
            date_str = "Unknown date"
        theme = config_manager.get("theme", "dark")
        context_html = f"""
        <h1>{meeting_title}</h1>
        <div class='meta'>{day_str if day_str else ''}{', ' if day_str and date_str else ''}{date_str if date_str else ''}</div>
        <div class='meta'>{time_str if time_str else ''} <span class='meta'>{tz_str if tz_str else ''}</span></div>
        """
        self._set_meeting_context_html_with_dynamic_font(context_html, theme)
        # --- Meeting Notes ---
        notes_entry = db_ops.get_meeting_notes_for_recording(recording_id)
        if notes_entry:
            try:
                import markdown2  # Local import for performance
                html_notes = markdown2.markdown(notes_entry['notes'])
            except Exception as e:
                logger.error(f"Error converting notes to HTML: {e}")
                html_notes = f"<pre>{notes_entry['notes']}</pre>"
        else:
            html_notes = "<p>No meeting notes available. Right-click the recording to generate notes or view transcript.</p>"
        self.meeting_notes_edit.setHtml(wrap_html_body(html_notes, theme))
        # Remove any setStyleSheet calls here
        # self.meeting_notes_edit.setStyleSheet("")
        # self.meeting_notes_edit.setStyleSheet("margin-bottom: 6px;")
        # --- Tag/Label Widget ---
        while self.meeting_tags_layout.count():
            item = self.meeting_tags_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        tags = db_ops.get_tags_for_recording(recording_id)
        if tags:
            for tag in tags:
                chip = TagChip(f"{tag['name']}")
                chip.setCursor(Qt.PointingHandCursor)
                def make_remove_tag(tag_name):
                    return lambda e: self._remove_tag_from_recording(recording_id, tag_name)
                chip.mousePressEvent = make_remove_tag(tag['name'])
                self.meeting_tags_layout.addWidget(chip)
        else:
            no_tag = QLabel("No tags")
            self.meeting_tags_layout.addWidget(no_tag)
        add_label_btn = QPushButton("Add Label")
        add_label_btn.setObjectName("AddLabelButton")
        add_label_btn.setCursor(Qt.PointingHandCursor)
        def open_tag_editor():
            from PySide6.QtWidgets import QDialog
            initial_tags = [t['name'] for t in db_ops.get_tags_for_recording(recording_id)]
            dlg = TagEditorDialog(self, recording_id, initial_tags)
            if dlg.exec() == QDialog.Accepted:
                new_tags = dlg.get_tags()
                for tag in initial_tags:
                    db_ops.unassign_tag_from_recording(recording_id, tag)
                for tag in new_tags:
                    db_ops.assign_tag_to_recording(recording_id, tag)
                self.handle_meeting_selection_changed()
        add_label_btn.clicked.connect(open_tag_editor)
        self.meeting_tags_layout.addWidget(add_label_btn)
        self.meeting_tags_layout.addStretch(1)
        # --- Adapted logic from old handle_recording_selection_changed ---
        status = recording_data.get("status", "").lower()
        audio_exists = os.path.exists(recording_data.get("audio_path", ""))
        if status == "processing":
            self.transcribe_button.setEnabled(False)
            self.transcribe_button.setToolTip("This recording is currently being processed.")
        elif not audio_exists:
            self.transcribe_button.setEnabled(False)
            self.transcribe_button.setToolTip("Audio file for this recording is missing.")
        else:
            self.transcribe_button.setEnabled(True)
            self.transcribe_button.setToolTip("Transcribe and diarize the selected recording (re-processing is allowed)")
        # Enable playback controls if audio path exists
        audio_path = recording_data.get("audio_path")
        abs_audio_path = from_project_relative_path(audio_path) if audio_path else None
        self.selected_audio_path = None
        self._playback_duration = 0.0
        if abs_audio_path and os.path.exists(abs_audio_path):
            self.play_button.setEnabled(True)
            self.selected_audio_path = abs_audio_path
            try:
                with sf.SoundFile(abs_audio_path) as f:
                    duration = len(f) / f.samplerate
                    self._playback_duration = duration
                    self.seek_slider.setMaximum(int(duration * 1000))
                    self.seek_slider.setValue(0)
                    self.update_seek_time_label(0, duration)
            except Exception as e:
                logger.error(f"Failed to read audio duration: {e}")
                self.seek_slider.setMaximum(100)
                self.seek_slider.setValue(0)
                self.update_seek_time_label(0, 0)
        else:
            self.play_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            self.seek_slider.setMaximum(100)
            self.seek_slider.setValue(0)
            self.update_seek_time_label(0, 0)
        self.meeting_notes_edit.setVisible(True)

        # --- Chat: Load and display chat history for this meeting ---
        chat_json = db_ops.get_chat_history_for_recording(recording_id)
        try:
            self.current_chat_history = json.loads(chat_json) if chat_json else []
        except Exception:
            self.current_chat_history = []
        if hasattr(self, 'chat_display_area'):
            self.chat_display_area.clear()
            for msg in self.current_chat_history:
                role = msg.get("role")
                text = msg.get("parts", [{}])[0].get("text", "")
                import markdown2
                html = markdown2.markdown(text)
                from datetime import datetime
                timestamp = datetime.now().strftime("%H:%M")
                if role == "user":
                    user_html = f'<div class="chat-message user"><b>You</b> <span class="timestamp">{timestamp}</span><div class="content">{html}</div></div>'
                    self.chat_display_area.append(user_html)
                elif role == "model":
                    ai_html = f'<div class="chat-message assistant"><b>Assistant</b> <span class="timestamp">{timestamp}</span><div class="content">{html}</div></div>'
                    self.chat_display_area.append(ai_html)
                else:
                    sys_html = f'<div class="chat-message system">{html}</div>'
                    self.chat_display_area.append(sys_html)

        if hasattr(self, 'clear_chat_button'):
            self.clear_chat_button.setEnabled(True)

    def open_settings_dialog(self):
        if not hasattr(self, 'settings_dialog') or not self.settings_dialog:
            self.settings_dialog = SettingsDialog(self)
            self.settings_dialog.settings_saved.connect(self._handle_settings_saved)

        # Apply current main window palette to ensure consistency
        self.settings_dialog.setPalette(self.palette())
        self.settings_dialog.exec()

    def _handle_settings_saved(self):
        # Reload theme from config and apply it
        new_theme = config_manager.get("theme", "dark")
        self.apply_theme(new_theme)
        # Optional: Show confirmation or perform other updates if needed
        self.status_bar.showMessage("Settings saved successfully.", 3000)

    def handle_seek_slider_moved(self, value):
        # User is dragging the slider; update the time label
        self.seeker_user_is_dragging = True
        if hasattr(self, '_playback_duration'):
            self.update_seek_time_label(value / 1000.0, self._playback_duration)

    def handle_seek_slider_pressed(self):
        self.seeker_user_is_dragging = True

    def handle_seek_slider_released(self):
        value = self.seek_slider.value()
        duration = getattr(self, '_playback_duration', 0.0)
        self.playback_controller.seek(value / 1000.0)
        self.update_seek_time_label(value / 1000.0, duration)
        self.seeker_user_is_dragging = False

    def on_transcribe_clicked(self):
        """Handler for the Transcribe button. Processes the selected recording."""
        selected_items = self.meetings_list_widget.selectedItems()
        if not selected_items:
            QMessageBox.information(self, "No Selection", "Please select a recording to transcribe.")
            return
        item = selected_items[0]
        recording_id = item.data(Qt.UserRole)
        recording_data = db_ops.get_recording_by_id(recording_id)
        if not recording_id or not recording_data:
            QMessageBox.warning(self, "Selection Error", "Could not retrieve selected recording data.")
            return
        self.process_selected_recording(recording_id, recording_data)

    def _setup_recording_pipeline_connections(self):
        rp = self.recording_pipeline
        rp.recording_started.connect(self.handle_recording_started)
        rp.recording_finished.connect(self.handle_recording_finished)
        rp.recording_error.connect(self.handle_recording_error)
        rp.recording_status.connect(self.status_bar.showMessage)
        rp.recording_discarded.connect(self.handle_recording_discarded)

    def add_tag_filter_from_input(self):
        tag = self.tag_filter_input.text().strip()
        if tag and tag not in self.active_tag_filters:
            self.active_tag_filters.add(tag)
            self.refresh_tag_filter_chips()
            self.apply_tag_filter()
        self.tag_filter_input.clear()

    def refresh_tag_filter_chips(self):
        while self.tag_filter_chips.count():
            item = self.tag_filter_chips.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for tag in sorted(self.active_tag_filters, key=str.lower):
            chip = TagChip(tag)
            chip.mousePressEvent = lambda e, t=tag: self.remove_tag_filter(t)
            self.tag_filter_chips.addWidget(chip)

    def remove_tag_filter(self, tag):
        self.active_tag_filters.discard(tag)
        self.refresh_tag_filter_chips()
        self.apply_tag_filter()

    def apply_tag_filter(self):
        if not self.active_tag_filters:
            self.load_recordings()
            return
        filtered = []
        for rec in db_ops.get_all_recordings():
            tag_names = [t['name'] for t in db_ops.get_tags_for_recording(rec['id'])]
            speaker_objs = db_ops.get_speakers_for_recording(rec['id'])
            speaker_names = []
            for s in speaker_objs:
                if s.get('name'):
                    speaker_names.append(s['name'])
                if s.get('diarization_label'):
                    speaker_names.append(s['diarization_label'])
            combined = tag_names + speaker_names
            # For each filter term, require it to be a substring (case-insensitive) of at least one tag or speaker
            match = True
            for filter_term in self.active_tag_filters:
                filter_term_lower = filter_term.lower()
                if not any(filter_term_lower in str(val).lower() for val in combined):
                    match = False
                    break
            if match:
                filtered.append(rec)
        self.recording_table_model.update_data(filtered)

    def toggle_merge_mode(self, checked):
        self._merge_mode = checked
        # Show/hide checkboxes
        for widgets in getattr(self, 'current_speaker_widgets', {}).values():
            if 'merge_checkbox' in widgets:
                widgets['merge_checkbox'].setVisible(checked)
                widgets['merge_checkbox'].setChecked(False)
        self._merge_selected = set()
        self.merge_btn.setEnabled(False)

    def handle_merge_checkbox(self, speaker_id, state):
        if state:
            self._merge_selected.add(speaker_id)
        else:
            self._merge_selected.discard(speaker_id)
        self.merge_btn.setEnabled(len(self._merge_selected) >= 2)

    def handle_delete_speaker(self, recording_id, speaker_id):
        # Confirm deletion
        from nojoin.db import database as db_ops
        reply = QMessageBox.question(self, "Delete Speaker", f"Are you sure you want to delete this speaker from the recording? This will remove all their segments and references.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        # Delete from DB
        success = db_ops.delete_speaker_from_recording(recording_id, speaker_id)
        if not success:
            QMessageBox.critical(self, "Delete Speaker", "Failed to delete speaker from recording.")
            return
        # Update diarized transcript to remove speaker segments
        speaker = db_ops.get_speaker_by_id(speaker_id)
        diarization_label = None
        for s in db_ops.get_speakers_for_recording(recording_id):
            if s['id'] == speaker_id:
                diarization_label = s.get('diarization_label')
                break
        if diarization_label:
            transcript_ok = db_ops.replace_speaker_in_transcript(recording_id, diarization_label, None)
            if not transcript_ok:
                QMessageBox.warning(self, "Transcript Update", "Speaker deleted, but failed to update transcript.")
        # Regenerate meeting notes using Gemini
        try:
            rec = db_ops.get_recording_by_id(recording_id)
            diarized_transcript_path = rec.get("diarized_transcript_path")
            abs_diarized_transcript_path = from_project_relative_path(diarized_transcript_path) if diarized_transcript_path else None
            if abs_diarized_transcript_path and os.path.exists(abs_diarized_transcript_path):
                with open(abs_diarized_transcript_path, 'r', encoding='utf-8') as f:
                    transcript_text = f.read()
                api_key = config_manager.get("gemini_api_key")
                model = config_manager.get("gemini_model", "gemini-2.5-flash-preview-05-20")
                backend = get_llm_backend("gemini", api_key=api_key, model=model)
                mapping, notes = backend.infer_speakers_and_generate_notes(transcript_text)
                notes_entry = db_ops.get_meeting_notes_for_recording(recording_id)
                if notes_entry:
                    db_ops.update_meeting_notes(notes_entry['id'], notes)
                else:
                    db_ops.add_meeting_notes(recording_id, 'gemini', model, notes)
                logger.info("Meeting notes regenerated after speaker deletion.")
                self.status_bar.showMessage("Speaker deleted, transcript and meeting notes updated", 3000)
                self.load_recordings()
                self.load_transcript(recording_id)
                self.handle_meeting_selection_changed()
            else:
                self.status_bar.showMessage("Speaker deleted and transcript updated, but transcript file missing for notes regeneration", 3000)
        except Exception as e:
            logger.error(f"Error regenerating meeting notes after speaker deletion: {e}")
            self.status_bar.showMessage("Speaker deleted and transcript updated, but failed to regenerate meeting notes", 3000)
        QMessageBox.information(self, "Delete Speaker", "Speaker deleted from recording.")
        # Reload panel and table
        rec = db_ops.get_recording_by_id(recording_id)
        self.load_speaker_labels(rec)
        self.load_recordings()
        self.load_transcript(recording_id)

    def handle_merge_speakers(self, recording_id, speakers):
        from nojoin.db import database as db_ops
        selected_ids = list(self._merge_selected)
        if len(selected_ids) < 2:
            QMessageBox.warning(self, "Merge Speakers", "Select at least two speakers to merge.")
            return
        # Prompt user to select target speaker
        speaker_map = {s['id']: s for s in speakers if s['id'] in selected_ids}
        items = [f"{s['name'] or s['diarization_label']} (ID {sid})" for sid, s in speaker_map.items()]
        target_idx, ok = QInputDialog.getItem(self, "Select Target Speaker", "Merge into:", items, 0, False)
        if not ok:
            return
        # Find selected target speaker_id
        for sid, s in speaker_map.items():
            label = f"{s['name'] or s['diarization_label']} (ID {sid})"
            if label == target_idx:
                target_speaker_id = sid
                break
        else:
            QMessageBox.warning(self, "Merge Speakers", "Could not determine target speaker.")
            return
        # Merge in backend
        success = db_ops.merge_speakers_in_recording(recording_id, selected_ids, target_speaker_id)
        if not success:
            QMessageBox.critical(self, "Merge Speakers", "Failed to merge speakers.")
            return
        # Regenerate meeting notes using Gemini
        try:
            rec = db_ops.get_recording_by_id(recording_id)
            diarized_transcript_path = rec.get("diarized_transcript_path")
            abs_diarized_transcript_path = from_project_relative_path(diarized_transcript_path) if diarized_transcript_path else None
            if abs_diarized_transcript_path and os.path.exists(abs_diarized_transcript_path):
                with open(abs_diarized_transcript_path, 'r', encoding='utf-8') as f:
                    transcript_text = f.read()
                api_key = config_manager.get("gemini_api_key")
                model = config_manager.get("gemini_model", "gemini-2.5-flash-preview-05-20")
                backend = get_llm_backend("gemini", api_key=api_key, model=model)
                mapping, notes = backend.infer_speakers_and_generate_notes(transcript_text)
                notes_entry = db_ops.get_meeting_notes_for_recording(recording_id)
                if notes_entry:
                    db_ops.update_meeting_notes(notes_entry['id'], notes)
                else:
                    db_ops.add_meeting_notes(recording_id, 'gemini', model, notes)
                logger.info("Meeting notes regenerated after speaker merge.")
                self.status_bar.showMessage("Speakers merged, transcript and meeting notes updated", 3000)
                self.load_recordings()
                self.load_transcript(recording_id)
                self.handle_meeting_selection_changed()
            else:
                self.status_bar.showMessage("Speakers merged and transcript updated, but transcript file missing for notes regeneration", 3000)
        except Exception as e:
            logger.error(f"Error regenerating meeting notes after speaker merge: {e}")
            self.status_bar.showMessage("Speakers merged and transcript updated, but failed to regenerate meeting notes", 3000)
        QMessageBox.information(self, "Merge Speakers", "Speakers merged successfully.")

    def load_transcript(self, recording_id):
        """Load and display the transcript for the given recording ID, mapping diarization labels to current speaker names."""
        from nojoin.db import database as db_ops
        import re
        recording_data = db_ops.get_recording_by_id(recording_id)
        if not recording_data:
            # self.transcript_text_edit.clear() # transcript_text_edit is being removed
            # self.transcript_text_edit.setPlaceholderText("Select a recording to view transcript...") # transcript_text_edit is being removed
            return "" # Return empty string or handle appropriately if transcript content is needed elsewhere
        
        raw_transcript_path = recording_data.get("raw_transcript_path")
        diarized_transcript_path = recording_data.get("diarized_transcript_path")
        abs_raw_transcript_path = from_project_relative_path(raw_transcript_path) if raw_transcript_path else None
        abs_diarized_transcript_path = from_project_relative_path(diarized_transcript_path) if diarized_transcript_path else None
        
        display_text_lines = [] # Changed to list of lines for easier HTML construction later
        
        # Build diarization label -> name mapping
        speakers = db_ops.get_speakers_for_recording(recording_id)
        label_to_name = {s['diarization_label']: s['name'] for s in speakers if s.get('diarization_label')}

        if abs_diarized_transcript_path and os.path.exists(abs_diarized_transcript_path):
            try:
                with open(abs_diarized_transcript_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        # Match lines like: [timestamp] - <speaker> - text
                        m = re.match(r"(\[.*?\]\s*-\s*)(.+?)(\s*-\s*)(.*)", line)
                        if m:
                            prefix = m.group(1)
                            diarization_label = m.group(2).strip()
                            sep = m.group(3)
                            text_content = m.group(4)
                            # Map label to current name
                            speaker_name = label_to_name.get(diarization_label, diarization_label)
                            import html as html_converter 
                            escaped_text_content = html_converter.escape(text_content)
                            html_line = (f'<span class="meta">{prefix}</span> '
                                         f'<b>{speaker_name}</b>'
                                         f'<span class="meta">{sep}</span>'
                                         f'<span>{escaped_text_content}</span>')
                            display_text_lines.append(html_line)
                        else:
                            import html as html_converter
                            escaped_line = html_converter.escape(line.rstrip('\n'))
                            display_text_lines.append(f'<span>{escaped_line}</span>')
                full_html = "<br>".join(display_text_lines)
                return wrap_html_body(full_html, config_manager.get("theme", "dark"))
            except Exception as e:
                logger.error(f"Failed to load/format diarized transcript {abs_diarized_transcript_path}: {e}")
                return wrap_html_body(f"<p>Error loading diarized transcript.</p>", config_manager.get("theme", "dark"))
        elif abs_raw_transcript_path and os.path.exists(abs_raw_transcript_path):
            try:
                with open(abs_raw_transcript_path, 'r', encoding='utf-8') as f:
                    raw_content = f.read()
                import html as html_converter
                escaped_content = html_converter.escape(raw_content)
                return wrap_html_body(f"<pre>{escaped_content}</pre>", config_manager.get("theme", "dark"))
            except Exception as e:
                logger.error(f"Failed to load raw transcript {abs_raw_transcript_path}: {e}")
                return wrap_html_body(f"<p>Error loading raw transcript.</p>", config_manager.get("theme", "dark"))
        
        # If no transcript, return an appropriate HTML message
        status = recording_data.get("status", "Unknown")
        if status == "Processed":
            return wrap_html_body("<p>Transcript files not found, although status is 'Processed'.</p>", config_manager.get("theme", "dark"))
        elif status == "Processing":
            return wrap_html_body("<p>Recording is currently being processed...</p>", config_manager.get("theme", "dark"))
        elif status == "Error":
            return wrap_html_body("<p>Processing failed for this recording.</p>", config_manager.get("theme", "dark"))
        else:
            return wrap_html_body("<p>Recording has not been processed yet.</p>", config_manager.get("theme", "dark"))

        # self.speakerLabelingScroll.setVisible(show_labeling_panel) # This visibility is handled in handle_recording_selection_changed

    @Slot()
    def on_import_audio_clicked(self):
        """
        Handler for Import Audio button. Opens file dialog, imports, and adds to DB.
        Note: On Windows, you may see 'Failed to initialize COM library (Cannot change thread mode after it is set.)' in the logs.
        This is a benign warning from Qt/PySide6 when using QFileDialog and can be safely ignored as long as dialogs work.
        See: https://github.com/qt/qtbase/blob/dev/src/plugins/platforms/windows/qwindowsdialoghelpers.cpp
        """
        logger.info("Import Audio button clicked. Preparing to show file dialog.")
        file_dialog = QFileDialog(self)
        file_dialog.setFileMode(QFileDialog.ExistingFiles)
        file_dialog.setNameFilters([
            "Audio Files (*.mp3 *.wav *.ogg *.flac *.m4a *.aac)",
            "All Files (*)"
        ])
        file_dialog.setWindowTitle("Import Audio Files")
        file_dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        logger.info("About to execute file dialog for import audio.")
        result = file_dialog.exec()
        logger.info(f"File dialog exec() returned: {result}")
        if result:
            selected_files = file_dialog.selectedFiles()
            logger.info(f"User selected files: {selected_files}")
            if not selected_files:
                logger.info("No files selected in import dialog.")
                return
            # Import files
            results = import_multiple_audio_files(selected_files)
            imported_count = 0
            for result in results:
                if result.success:
                    # Add to DB
                    dt = datetime.now()
                    orig_name = os.path.splitext(os.path.basename(result.rel_path))[0]
                    recording_name = f"Imported - {orig_name} - {dt.strftime('%A %d %B %Y, %H:%M')}"
                    new_id = db_ops.add_recording(
                        name=recording_name,
                        audio_path=result.rel_path,
                        duration=result.duration,
                        size_bytes=result.size,
                        format=result.format or "MP3"
                    )
                    if new_id:
                        imported_count += 1
                else:
                    QMessageBox.warning(self, "Import Failed", result.message)
            if imported_count > 0:
                self.status_bar.showMessage(f"Successfully imported {imported_count} audio file(s).", 4000)
                self.load_recordings()
            else:
                self.status_bar.showMessage("No audio files were imported.", 4000)
        else:
            logger.warning("File dialog for import audio did not execute or was cancelled.")
            QMessageBox.warning(self, "Import Audio", "The file dialog could not be opened or was cancelled.")

    # --- Action Handlers ---
    def process_selected_recording(self, recording_id, recording_data):
        """Initiates processing for a given recording ID using a worker thread and shows progress dialog."""
        if not processing_pipeline:
            QMessageBox.warning(self, "Processing Unavailable", "The processing module could not be loaded. Cannot process recordings.")
            return
        # Only allow one processing worker at a time
        if self.processing_workers:
            QMessageBox.warning(self, "Processing Busy", "Only one recording can be processed at a time. Please wait for the current processing to finish.")
            return
        audio_path = recording_data.get('audio_path')
        if not audio_path or not os.path.exists(from_project_relative_path(audio_path)):
            QMessageBox.critical(self, "Error", f"Audio file not found for recording ID {recording_id} at path: {audio_path}")
            self.update_table_status(recording_id, "Error")
            return

        absolute_audio_path = from_project_relative_path(audio_path)
        logger.info(f"Starting processing worker for recording ID: {recording_id} ({recording_data.get('name')}), Path: {absolute_audio_path}")

        # Create and show the progress dialog
        progress_dialog = ProcessingProgressDialog(recording_data.get('name', f'ID {recording_id}'), self)

        # Create worker
        worker = ProcessingWorker(recording_id, absolute_audio_path)

        # Connect worker signals to dialog and main window slots
        worker.started.connect(lambda rec_id: self.update_table_status(rec_id, "Processing"))
        
        # Connect stage updates to dialog's set_stage method
        worker.stage_changed.connect(progress_dialog.set_stage)
        worker.stage_update.connect(progress_dialog.update_message)
        
        # Connect progress updates to dialog's stage progress method
        worker.progress_update.connect(lambda percent, elapsed, eta: progress_dialog.update_stage_progress(percent))
        
        worker.finished.connect(lambda rec_id: self._handle_processing_completion(rec_id, progress_dialog))
        worker.error.connect(lambda rec_id, msg: self._handle_processing_completion(rec_id, progress_dialog, msg))

        # Connect dialog cancellation to worker cancellation
        progress_dialog.rejected.connect(worker.request_cancel)

        # Store worker (single-processing: only one at a time)
        self.processing_workers[str(recording_id)] = worker
        worker.start()

        # Show the dialog modally
        progress_dialog.exec()

        # Dialog closed (accepted, rejected, or cancelled)
        logger.info(f"Processing dialog closed for recording ID: {recording_id}")
        # Worker might still be finishing up if cancelled, cleanup handled in _handle_processing_completion

    def _handle_processing_completion(self, recording_id, dialog, error_message=None):
        dialog.mark_processing_complete()  # Mark as complete so dialog can close
        dialog.close()
        # Remove worker from processing_workers
        self.processing_workers.pop(str(recording_id), None)
        if error_message:
            self.update_table_status(recording_id, "Error")
            QMessageBox.critical(self, "Processing Error", error_message)
            return
        # Check if the recording still exists and is not cancelled or errored
        recording_data = db_ops.get_recording_by_id(recording_id)
        if not recording_data:
            print(f"[ERROR] Recording not found after processing. recording_id={recording_id}")
            QMessageBox.warning(self, "Processing Complete", f"Recording not found in database. (ID: {recording_id})")
            return
        status = recording_data.get("status", "").lower()
        print(f"[DEBUG] Recording status after processing: {status} (ID: {recording_id})")
        if status == "cancelled":
            self.update_table_status(recording_id, "Cancelled")
            QMessageBox.information(self, "Processing Cancelled", "Recording was cancelled.")
            return
        if status == "error":
            self.update_table_status(recording_id, "Error")
            QMessageBox.critical(self, "Processing Error", f"Recording processing failed. (ID: {recording_id})")
            return
        self.update_table_status(recording_id, "Processed")
        # Prompt user to review/relabel speakers, then generate meeting notes
        self._prompt_relabel_speakers(recording_id)

    def get_meetings_list_qss(self):
        return """
        QListWidget::item {
            background: transparent;
            border: 2px;
            margin: 1px 0px;
            padding: 2px;
        }
        """

    def _remove_tag_from_recording(self, recording_id, tag_name):
        db_ops.unassign_tag_from_recording(recording_id, tag_name)
        self.handle_meeting_selection_changed()

    def _populate_search_cache(self):
        self.all_meetings_cache_for_search = []
        try:
            recordings = db_ops.get_all_recordings()
            for rec in recordings:
                rec_dict = dict(rec) if not isinstance(rec, dict) else rec
                rid = rec_dict['id']
                name = rec_dict.get('name', '')
                notes_entry = db_ops.get_meeting_notes_for_recording(rid)
                notes = notes_entry['notes'] if notes_entry else ''
                tags = [t['name'] for t in db_ops.get_tags_for_recording(rid)]
                speakers = db_ops.get_speakers_for_recording(rid)
                speaker_names = [s.get('name', '') or '' for s in speakers] + [s.get('diarization_label', '') or '' for s in speakers]
                searchable_text = ' '.join([str(name), str(notes)] + tags + speaker_names).lower()
                self.all_meetings_cache_for_search.append({'id': rid, 'original_data': rec_dict, 'searchable_text': searchable_text})
        except Exception as e:
            logger.error(f"Error populating search cache: {e}")

    def _on_search_text_changed(self, text):
        self.search_timer.start()

    def _perform_search(self):
        query = self.search_bar_widget.get_text()
        if not self.all_meetings_cache_for_search:
            self._populate_search_cache()
        if not query:
            filtered = [m['original_data'] for m in self.all_meetings_cache_for_search]
        else:
            filtered = self.search_engine.search(query, self.all_meetings_cache_for_search)
        self._update_meetings_list(filtered)

    def _clear_search(self):
        self.search_bar_widget.set_text("")
        self._perform_search()

    def _update_meetings_list(self, meetings_to_display):
        self.meetings_list_widget.clear()
        for rec in meetings_to_display:
            list_item = QListWidgetItem()
            list_item.setData(Qt.UserRole, rec['id'] if isinstance(rec, dict) else rec[0])
            custom_widget = MeetingListItemWidget(rec, getattr(self, 'current_theme', 'dark'))
            list_item.setSizeHint(custom_widget.sizeHint())
            self.meetings_list_widget.addItem(list_item)
            self.meetings_list_widget.setItemWidget(list_item, custom_widget)
        if not meetings_to_display:
            self.handle_meeting_selection_changed()
        self.meetings_list_widget.setTextElideMode(Qt.ElideNone)
        self.meetings_list_widget.setWordWrap(True)

    def _handle_send_chat_message(self):
        if self._chat_request_in_progress:
            self.logger.info("Chat request already in progress, ignoring new request.")
            return
        user_question = self.chat_input.text().strip()
        if not user_question:
            return
        from datetime import datetime
        import json
        # --- New: Append user message as HTML to chat_display_area ---
        import markdown2
        html = markdown2.markdown(user_question)
        timestamp = datetime.now().strftime("%H:%M")
        user_html = f'<div class="chat-message user"><b>You</b> <span class="timestamp">{timestamp}</span><div class="content">{html}</div></div>'
        self.chat_display_area.append(user_html)
        self.current_chat_history.append({"role": "user", "parts": [{"text": user_question}]})
        # --- Save chat history to DB ---
        selected_items = self.meetings_list_widget.selectedItems()
        if selected_items:
            recording_id = selected_items[0].data(Qt.UserRole)
            try:
                db_ops.set_chat_history_for_recording(recording_id, json.dumps(self.current_chat_history))
            except Exception as e:
                self.logger.error(f"Failed to save chat history: {e}")
        self.chat_input.clear()
        self.chat_input.setEnabled(False)
        # --- New: Replace send button with spinner ---
        self.chat_send_button.setVisible(False)
        if not hasattr(self, 'chat_spinner'):
            self.chat_spinner = QProgressBar()
            self.chat_spinner.setRange(0, 0)
            self.chat_spinner.setFixedSize(40, 40)
            self.chat_spinner.setTextVisible(False)
        self.chat_panel.layout().addWidget(self.chat_spinner)
        self._chat_request_in_progress = True
        selected_items = self.meetings_list_widget.selectedItems()
        if not selected_items:
            # --- New: System message to chat_display_area ---
            sys_html = '<div class="chat-message system"><i>No meeting selected.</i></div>'
            self.chat_display_area.append(sys_html)
            self.chat_input.setEnabled(True)
            self.chat_send_button.setVisible(True)
            if hasattr(self, 'chat_spinner'):
                self.chat_panel.layout().removeWidget(self.chat_spinner)
                self.chat_spinner.setParent(None)
            self._chat_request_in_progress = False
            return
        recording_id = selected_items[0].data(Qt.UserRole)
        rec = db_ops.get_recording_by_id(recording_id)
        notes_entry = db_ops.get_meeting_notes_for_recording(recording_id)
        meeting_notes = notes_entry['notes'] if notes_entry else ""
        diarized_transcript_path = rec.get("diarized_transcript_path")
        abs_diarized_transcript_path = from_project_relative_path(diarized_transcript_path) if diarized_transcript_path else None
        if not abs_diarized_transcript_path or not os.path.exists(abs_diarized_transcript_path):
            sys_html = '<div class="chat-message system"><i>No transcript available for chat.</i></div>'
            self.chat_display_area.append(sys_html)
            self.chat_input.setEnabled(True)
            self.chat_send_button.setVisible(True)
            if hasattr(self, 'chat_spinner'):
                self.chat_panel.layout().removeWidget(self.chat_spinner)
                self.chat_spinner.setParent(None)
            self._chat_request_in_progress = False
            return
        with open(abs_diarized_transcript_path, 'r', encoding='utf-8') as f:
            diarized_transcript = f.read()
        api_key = config_manager.get("gemini_api_key")
        if not api_key:
            sys_html = '<div class="chat-message system"><i>Gemini API key not set.</i></div>'
            self.chat_display_area.append(sys_html)
            self.chat_input.setEnabled(True)
            self.chat_send_button.setVisible(True)
            if hasattr(self, 'chat_spinner'):
                self.chat_panel.layout().removeWidget(self.chat_spinner)
                self.chat_spinner.setParent(None)
            self._chat_request_in_progress = False
            return
        # Use provider/model/api_key from config
        provider = config_manager.get("llm_provider", "gemini")
        api_key = config_manager.get(f"{provider}_api_key")
        model = config_manager.get(f"{provider}_model", "gemini-2.5-flash-preview-05-20")
        if not api_key:
            sys_html = f'<div class="chat-message system"><i>No API key provided for {provider.title()}. Meeting chat is disabled.</i></div>'
            self.chat_display_area.append(sys_html)
            self.chat_input.setEnabled(False)
            self.chat_send_button.setEnabled(False)
            if hasattr(self, 'chat_spinner'):
                self.chat_panel.layout().removeWidget(self.chat_spinner)
                self.chat_spinner.setParent(None)
            self._chat_request_in_progress = False
            return
        backend = get_llm_backend(provider, api_key=api_key, model=model)
        import threading
        def handle_response():
            self.logger.info("[Chat] Gemini API call started.")
            try:
                response = backend.ask_question_about_meeting(
                    user_question=user_question,
                    meeting_notes=meeting_notes,
                    diarized_transcript=diarized_transcript,
                    conversation_history=self.current_chat_history[:-1],
                    timeout=60,
                    recording_id=recording_id  # Pass recording_id for mapped transcript
                )
                self.logger.info("[Chat] Gemini API call finished.")
                self.chat_response_signal.emit(response)
            except Exception as e:
                self.logger.error(f"[Chat] Gemini API error: {e}")
                self.chat_error_signal.emit(str(e))
        t = threading.Thread(target=handle_response, daemon=True)
        t.start()
        self._chat_threads.append(t)

    @Slot(QPoint)
    def show_meeting_context_menu(self, pos):
        item = self.meetings_list_widget.itemAt(pos)
        if not item:
            return
        recording_id = item.data(Qt.UserRole)
        recording_data = db_ops.get_recording_by_id(recording_id)
        if not recording_data:
            return
        theme_name = getattr(self, 'current_theme', 'dark')
        global_pos = self.meetings_list_widget.viewport().mapToGlobal(pos)
        context_menu = AnimatedMenu(self, start_pos=global_pos, theme_name=theme_name)
        # Content Actions
        view_diarized_transcript_action = QAction("View Diarized Transcript", self)
        view_diarized_transcript_action.triggered.connect(lambda: self._context_show_diarized_transcript_dialog(recording_id, recording_data))
        diarized_transcript_path = recording_data.get("diarized_transcript_path")
        abs_diarized_transcript_path = from_project_relative_path(diarized_transcript_path) if diarized_transcript_path else None
        view_diarized_transcript_action.setEnabled(bool(abs_diarized_transcript_path and os.path.exists(abs_diarized_transcript_path)))
        context_menu.addAction(view_diarized_transcript_action)
        # Regenerate Meeting Notes
        regenerate_notes_action = QAction("Regenerate Meeting Notes", self)
        regenerate_notes_action.triggered.connect(lambda checked, rid=recording_id, rdata=recording_data: self.on_regenerate_meeting_notes_clicked(rid, rdata))
        api_key_available = bool(config_manager.get("gemini_api_key"))
        transcript_exists = bool(abs_diarized_transcript_path and os.path.exists(abs_diarized_transcript_path))
        regenerate_notes_action.setEnabled(api_key_available and transcript_exists)
        context_menu.addAction(regenerate_notes_action)
        # --- New: Manage Participants ---
        manage_participants_action = QAction("Manage Participants", self)
        manage_participants_action.triggered.connect(lambda: self._open_participants_dialog(recording_id, recording_data))
        manage_participants_action.setEnabled(transcript_exists)
        context_menu.addAction(manage_participants_action)
        context_menu.addSeparator()
        # Processing Actions
        process_action = QAction("Process (Transcribe and Diarize)", self)
        process_action.triggered.connect(lambda: self.process_selected_recording(recording_id, recording_data))
        current_status = recording_data.get('status', 'Unknown').lower()
        process_action.setEnabled(current_status != 'processing')
        context_menu.addAction(process_action)
        # Add Reset Status action for stuck 'processing' recordings
        if current_status == 'processing':
            reset_status_action = QAction("Reset Status to Error", self)
            def reset_status():
                db_ops.update_recording_status(recording_id, 'Error')
                self.load_recordings()
            reset_status_action.triggered.connect(reset_status)
            context_menu.addAction(reset_status_action)
        context_menu.addSeparator()
        # Management Actions
        rename_action = QAction("Rename Recording", self)
        rename_action.triggered.connect(lambda: self.rename_recording_dialog(recording_id, recording_data))
        context_menu.addAction(rename_action)
        delete_action = QAction("Delete Recording", self)
        delete_action.triggered.connect(lambda: self.delete_selected_recording(recording_id, recording_data))
        context_menu.addAction(delete_action)
        context_menu.exec(global_pos)

    def _open_participants_dialog(self, recording_id, recording_data):
        dlg = ParticipantsDialog(recording_id, recording_data, parent=self)
        dlg.participants_updated.connect(self._on_participants_changed)
        dlg.exec()

    def _on_participants_changed(self, recording_id):
        self.handle_meeting_selection_changed()
        self.on_regenerate_meeting_notes_clicked(recording_id, db_ops.get_recording_by_id(recording_id))

    @staticmethod
    def format_time(seconds):
        minutes, seconds = divmod(int(seconds), 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def update_seek_time_label(self, current_time, total_time):
        self.seek_time_label.setText(f"{self.format_time(current_time)} / {self.format_time(total_time)}")

    def _handle_volume_changed(self, value):
        self.playback_controller.set_volume(value / 100.0)
        self.volume_label.setText(f"{value}%")

    def closeEvent(self, event):
        # Attempt to clean up any running chat threads
        for t in getattr(self, '_chat_threads', []):
            if t.is_alive():
                try:
                    t.join(timeout=0.5)
                except Exception:
                    pass
        event.accept()

    def _on_chat_response(self, response):
        self.logger.info("[Chat] Updating UI with Gemini response.")
        import markdown2  # Local import for performance
        from datetime import datetime
        import json
        html = markdown2.markdown(response)
        timestamp = datetime.now().strftime("%H:%M")
        ai_html = f'<div class="chat-message assistant"><b>Assistant</b> <span class="timestamp">{timestamp}</span><div class="content">{html}</div></div>'
        self.chat_display_area.append(ai_html)
        self.current_chat_history.append({"role": "model", "parts": [{"text": response}]})
        # --- Save chat history to DB ---
        selected_items = self.meetings_list_widget.selectedItems()
        if selected_items:
            recording_id = selected_items[0].data(Qt.UserRole)
            try:
                db_ops.set_chat_history_for_recording(recording_id, json.dumps(self.current_chat_history))
            except Exception as e:
                self.logger.error(f"Failed to save chat history: {e}")
        self.chat_input.setEnabled(True)
        self.chat_send_button.setVisible(True)
        if hasattr(self, 'chat_spinner'):
            self.chat_panel.layout().removeWidget(self.chat_spinner)
            self.chat_spinner.setParent(None)
        self._chat_request_in_progress = False

    def _on_chat_error(self, error_msg):
        self.logger.info("[Chat] Updating UI with Gemini error.")
        err_html = f'<div class="chat-message system"><i>Error: {error_msg}</i></div>'
        self.chat_display_area.append(err_html)
        # --- Save chat history to DB (optional: only if you want to persist errors) ---
        # selected_items = self.meetings_list_widget.selectedItems()
        # if selected_items:
        #     recording_id = selected_items[0].data(Qt.UserRole)
        #     try:
        #         db_ops.set_chat_history_for_recording(recording_id, json.dumps(self.current_chat_history))
        #     except Exception as e:
        #         self.logger.error(f"Failed to save chat history: {e}")
        self.chat_input.setEnabled(True)
        self.chat_send_button.setVisible(True)
        if hasattr(self, 'chat_spinner'):
            self.chat_panel.layout().removeWidget(self.chat_spinner)
            self.chat_spinner.setParent(None)
        self._chat_request_in_progress = False

    def _prompt_relabel_speakers(self, recording_id):
        recording_data = db_ops.get_recording_by_id(recording_id)
        if not recording_data:
            QMessageBox.warning(self, "Relabel Speakers", "Recording not found in database. Cannot relabel speakers.")
            return
        dlg = ParticipantsDialog(recording_id, recording_data, parent=self)
        dlg.exec()
        self._generate_meeting_notes_after_relabel(recording_id)

    def _generate_meeting_notes_after_relabel(self, recording_id):
        # Generate meeting notes using the latest speaker mapping
        rec = db_ops.get_recording_by_id(recording_id)
        diarized_transcript_path = rec.get("diarized_transcript_path")
        abs_diarized_transcript_path = from_project_relative_path(diarized_transcript_path) if diarized_transcript_path else None
        if abs_diarized_transcript_path and os.path.exists(abs_diarized_transcript_path):
            with open(abs_diarized_transcript_path, 'r', encoding='utf-8') as f:
                transcript_text = f.read()
            llm_provider = config_manager.get("llm_provider", "gemini")
            api_key = config_manager.get(f"{llm_provider}_api_key")
            model = config_manager.get(f"{llm_provider}_model", "gpt-4.1-mini-2025-04-14")
            try:
                backend = get_llm_backend(llm_provider, api_key=api_key, model=model)
            except Exception as e:
                QMessageBox.warning(self, "Meeting Notes", f"Unknown LLM provider: {llm_provider}\n{e}")
                return
            # Get latest speaker mapping
            speakers = db_ops.get_speakers_for_recording(recording_id)
            label_to_name = {s['diarization_label']: s['name'] for s in speakers}
            try:
                notes = backend.generate_meeting_notes(transcript_text, label_to_name)
                db_ops.add_meeting_notes(recording_id, llm_provider, model, notes)
                QMessageBox.information(self, "Meeting Notes", "Meeting notes have been generated.")
            except Exception as e:
                logger.error(f"Failed to generate meeting notes: {e}")
                QMessageBox.warning(self, "Meeting Notes", f"Failed to generate meeting notes: {e}")
        else:
            QMessageBox.warning(self, "Meeting Notes", "Diarized transcript file missing. Cannot generate meeting notes.")
        # Always refresh the UI to show the latest notes
        self.handle_meeting_selection_changed()

    def clear_chat_for_selected_meeting(self):
        """Clears the chat history for the selected meeting, both in UI and DB, with confirmation."""
        selected_items = self.meetings_list_widget.selectedItems()
        if not selected_items:
            return
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.question(
            self,
            "Clear Chat History",
            "Are you sure you want to clear the chat history for this meeting? This action cannot be undone.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        recording_id = selected_items[0].data(Qt.UserRole)
        try:
            self.current_chat_history = []
            if hasattr(self, 'chat_display_area'):
                self.chat_display_area.clear()
            import json
            db_ops.set_chat_history_for_recording(recording_id, json.dumps([]))
            self.status_bar.showMessage("Chat history cleared.", 3000)
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Clear Chat Error", f"Failed to clear chat history: {e}")

    def handle_recording_discarded(self, message):
        self.is_recording = False
        self.recording_timer.stop()
        self.record_button.setText("Start Meeting")
        self.status_indicator.setText("Status: Idle")
        self.timer_label.setText("00:00:00")
        self.record_button.setEnabled(True)
        self.status_bar.showMessage(message)
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(self, "Recording Discarded", message)

    def _on_notes_edited_autosave(self):
        # Start/restart debounce timer
        self._notes_autosave_timer.stop()
        self._notes_autosave_timer.start()
        self.notes_have_been_edited = True
        # Optionally, update UI to show unsaved changes

    def _autosave_meeting_notes(self):
        selected_items = self.meetings_list_widget.selectedItems()
        if not selected_items:
            return
        recording_id = selected_items[0].data(Qt.UserRole)
        html_content = self.meeting_notes_edit.toHtml()
        # Only save if changed
        if html_content != self._notes_last_saved_content:
            try:
                notes_entry = db_ops.get_meeting_notes_for_recording(recording_id)
                if notes_entry:
                    db_ops.update_meeting_notes(notes_entry['id'], html_content)
                else:
                    db_ops.add_meeting_notes(recording_id, 'manual', '', html_content)
                self._notes_last_saved_content = html_content
                self.status_bar.showMessage("Meeting notes autosaved.", 1500)
                self.notes_have_been_edited = False
            except Exception as e:
                self.status_bar.showMessage(f"Failed to autosave notes: {e}", 3000)

    # Connect timer timeout to autosave
    def _setup_notes_autosave(self):
        self._notes_autosave_timer.timeout.connect(self._autosave_meeting_notes)

    def _show_notes_context_menu(self, pos):
        menu = QMenu(self.meeting_notes_edit)
        cursor = self.meeting_notes_edit.textCursor()
        has_selection = cursor.hasSelection()
        # Formatting actions
        bold_action = QAction("Bold", self)
        bold_action.setShortcut("Ctrl+B")
        bold_action.triggered.connect(lambda: self._format_notes_selection("bold"))
        italic_action = QAction("Italic", self)
        italic_action.setShortcut("Ctrl+I")
        italic_action.triggered.connect(lambda: self._format_notes_selection("italic"))
        underline_action = QAction("Underline", self)
        underline_action.setShortcut("Ctrl+U")
        underline_action.triggered.connect(lambda: self._format_notes_selection("underline"))
        bullet_action = QAction("Bullet List", self)
        bullet_action.triggered.connect(lambda: self._format_notes_selection("bullet"))
        numbered_action = QAction("Numbered List", self)
        numbered_action.triggered.connect(lambda: self._format_notes_selection("numbered"))
        # Only enable if text is selected (except lists)
        bold_action.setEnabled(has_selection)
        italic_action.setEnabled(has_selection)
        underline_action.setEnabled(has_selection)
        # Add actions
        menu.addAction(bold_action)
        menu.addAction(italic_action)
        menu.addAction(underline_action)
        menu.addSeparator()
        menu.addAction(bullet_action)
        menu.addAction(numbered_action)
        menu.addSeparator()
        # Add default actions (copy/paste/etc.)
        menu.addActions(self.meeting_notes_edit.createStandardContextMenu().actions())
        menu.exec(self.meeting_notes_edit.mapToGlobal(pos))

    def _format_notes_selection(self, fmt):
        cursor = self.meeting_notes_edit.textCursor()
        if fmt == "bold":
            fmt_obj = cursor.charFormat()
            fmt_obj.setFontWeight(QFont.Bold if fmt_obj.fontWeight() != QFont.Bold else QFont.Normal)
            cursor.mergeCharFormat(fmt_obj)
        elif fmt == "italic":
            fmt_obj = cursor.charFormat()
            fmt_obj.setFontItalic(not fmt_obj.fontItalic())
            cursor.mergeCharFormat(fmt_obj)
        elif fmt == "underline":
            fmt_obj = cursor.charFormat()
            fmt_obj.setFontUnderline(not fmt_obj.fontUnderline())
            cursor.mergeCharFormat(fmt_obj)
        elif fmt == "bullet":
            cursor.beginEditBlock()
            cursor.createList(QTextListFormat.ListDisc)
            cursor.endEditBlock()
        elif fmt == "numbered":
            cursor.beginEditBlock()
            cursor.createList(QTextListFormat.ListDecimal)
            cursor.endEditBlock()
        self.meeting_notes_edit.setTextCursor(cursor)

    def _check_audio_levels(self):
        """Check current audio levels from the recorder."""
        if not self.is_recording:
            return
            
        # Get audio levels from the recorder
        input_level, output_level = self._get_current_audio_levels()
        
        # Consider audio detected if either input or output has signal
        threshold = 0.01  # Adjust this threshold as needed
        audio_detected = input_level > threshold or output_level > threshold
        
        if audio_detected:
            self.audio_detected_recently = True
            self.no_audio_timer.stop()
            self.no_audio_timer.start()  # Restart the 10-second timer
            if self.audio_warning_banner.isVisible():
                self.audio_warning_banner.setVisible(False)
        
        self.last_input_level = input_level
        self.last_output_level = output_level
    
    def _get_current_audio_levels(self):
        """Get current audio levels from the recording pipeline."""
        try:
            if hasattr(self, 'recording_pipeline') and hasattr(self.recording_pipeline, 'recorder'):
                # Access the current audio levels from the recorder
                if hasattr(self.recording_pipeline.recorder, 'get_current_levels'):
                    return self.recording_pipeline.recorder.get_current_levels()
                else:
                    # Fallback: estimate from recent frames if available
                    if hasattr(self.recording_pipeline.recorder, 'frames') and self.recording_pipeline.recorder.frames:
                        recent_frames = self.recording_pipeline.recorder.frames[-10:]  # Last 10 frames
                        if recent_frames:
                            import numpy as np
                            combined = np.concatenate(recent_frames, axis=0)
                            # Calculate RMS (root mean square) as a simple level indicator
                            rms = np.sqrt(np.mean(combined**2))
                            # Assume mixed signal, so same level for both
                            return float(rms), float(rms)
            return 0.0, 0.0
        except Exception as e:
            logger.debug(f"Error getting audio levels: {e}")
            return 0.0, 0.0
    
    def _show_audio_warning(self):
        """Show the audio warning banner."""
        if not self.is_recording:
            return
            
        # Determine which type of audio is missing
        if self.last_input_level <= 0.01 and self.last_output_level <= 0.01:
            message = "No audio detected from microphone or speakers"
        elif self.last_input_level <= 0.01:
            message = "No audio detected from microphone"
        elif self.last_output_level <= 0.01:
            message = "No audio detected from speakers"
        else:
            return  # Audio is actually being detected
            
        self.audio_warning_label.setText(message)
        self.audio_warning_banner.setVisible(True)
        logger.info(f"Audio warning shown: {message}")

# --- Tag Chip Widget ---
class TagChip(QLabel):
    """A label styled as a tag chip, optionally removable."""
    def __init__(self, text, removable=True, parent=None):
        super().__init__(text, parent)
        self.setProperty('tagchip', True)
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.removable = removable
        if removable:
            self.setText(f"{text}  ✕")

# --- Tag Editor Dialog ---
class TagEditorDialog(QDialog):
    """Dialog for editing tags on a recording, with chips and autocomplete."""
    def __init__(self, parent, recording_id, initial_tags):
        super().__init__(parent)
        self.setWindowTitle("Edit Tags")
        self.setMinimumWidth(400)
        self.recording_id = recording_id
        self.layout = QVBoxLayout(self)
        self.tag_input = QLineEdit()
        self.tag_input.setPlaceholderText("Add tag...")
        self.layout.addWidget(self.tag_input)
        self.chip_layout = QHBoxLayout()
        self.chip_layout.setSpacing(4)
        self.layout.addLayout(self.chip_layout)
        self.button_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.layout.addWidget(self.button_box)
        self.button_box.accepted.connect(self._on_accept)
        self.button_box.rejected.connect(self.reject)
        self.tags = set(initial_tags)
        self.refresh_chips()
        # Autocomplete
        all_tags = [t['name'] for t in db_ops.get_tags()]
        self.completer = QCompleter(all_tags)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.tag_input.setCompleter(self.completer)
        self.tag_input.returnPressed.connect(self.add_tag_from_input)
    def add_tag_from_input(self):
        tag = self.tag_input.text().strip()
        if tag and tag not in self.tags:
            self.tags.add(tag)
            import logging; logging.getLogger(__name__).info(f"[TagEditorDialog] Added tag on accept: {tag}")
            self.refresh_chips()
        self.tag_input.clear()
    def refresh_chips(self):
        # Remove old chips
        while self.chip_layout.count():
            item = self.chip_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for tag in sorted(self.tags, key=str.lower):
            chip = TagChip(tag)
            chip.mousePressEvent = lambda e, t=tag: self.remove_tag(t)
            self.chip_layout.addWidget(chip)
    def remove_tag(self, tag):
        self.tags.discard(tag)
        import logging; logging.getLogger(__name__).info(f"[TagEditorDialog] Removed tag: {tag}")
        self.refresh_chips()
    def get_tags(self):
        import logging; logging.getLogger(__name__).info(f"[TagEditorDialog] get_tags called, returning: {list(self.tags)}")
        return list(self.tags)
    def _on_accept(self):
        # Add any pending tag in the input field before accepting
        tag = self.tag_input.text().strip()
        if tag and tag not in self.tags:
            self.tags.add(tag)
            import logging; logging.getLogger(__name__).info(f"[TagEditorDialog] Added tag on accept: {tag}")
            self.refresh_chips()
        import logging; logging.getLogger(__name__).info(f"[TagEditorDialog] Dialog accepted, tags: {list(self.tags)}")
        self.accept()

# --- Tag Delegate for Table ---
class TagChipDelegate(QStyledItemDelegate):
    """Custom delegate to paint tags as chips in the table view."""
    def paint(self, painter, option, index):
        tags = index.data(Qt.DisplayRole)
        if not tags or not isinstance(tags, list):
            super().paint(painter, option, index)
            return
        # tags is a list of dicts: [{"id": int, "name": str}, ...]
        x = option.rect.x() + 4
        y = option.rect.y() + 4
        max_x = option.rect.right() - 4
        chip_height = option.rect.height() - 8
        font_metrics = painter.fontMetrics()
        chip_padding = 24
        chip_spacing = 6
        chips_drawn = 0
        overflowed = False
        for tag in tags:
            tag_name = tag.get('name', '')
            chip_width = font_metrics.horizontalAdvance(tag_name) + chip_padding
            if x + chip_width > max_x:
                overflowed = True
                break
            chip_rect = QRect(x, y, chip_width, chip_height)
            painter.save()
            painter.setBrush(QColor("#232323"))
            painter.setPen(QColor("#ff9800"))
            painter.drawRoundedRect(chip_rect, 8, 8)
            painter.setPen(QColor("#ff9800"))
            painter.drawText(chip_rect.adjusted(8, 0, -8, 0), Qt.AlignVCenter, tag_name)
            painter.restore()
            x += chip_width + chip_spacing
            chips_drawn += 1
        if overflowed:
            ellipsis_rect = QRect(x, y, font_metrics.horizontalAdvance("...") + chip_padding, chip_height)
            painter.save()
            painter.setBrush(QColor("#232323"))
            painter.setPen(QColor("#ff9800"))
            painter.drawRoundedRect(ellipsis_rect, 8, 8)
            painter.setPen(QColor("#ff9800"))
            painter.drawText(ellipsis_rect.adjusted(8, 0, -8, 0), Qt.AlignVCenter, "...")
            painter.restore()
    def sizeHint(self, option, index):
        tags = index.data(Qt.DisplayRole)
        if not tags or not isinstance(tags, list):
            return super().sizeHint(option, index)
        font_metrics = option.fontMetrics
        chip_padding = 24
        chip_spacing = 6
        width = 4
        for tag in tags:
            tag_name = tag.get('name', '')
            width += font_metrics.horizontalAdvance(tag_name) + chip_padding + chip_spacing
        width += 4
        return QSize(width, font_metrics.height() + 12)

# --- Speaker Chip Delegate ---
class SpeakerChipDelegate(QStyledItemDelegate):
    """Custom delegate to paint speakers as chips in the table view."""
    def paint(self, painter, option, index):
        speakers = index.data(Qt.DisplayRole)
        if not speakers or not isinstance(speakers, list):
            super().paint(painter, option, index)
            return
        # speakers is a list of dicts: [{"id": int, "name": str, "diarization_label": str, ...}, ...]
        x = option.rect.x() + 4
        y = option.rect.y() + 4
        max_x = option.rect.right() - 4
        chip_height = option.rect.height() - 8
        font_metrics = painter.fontMetrics()
        chip_padding = 24
        chip_spacing = 6
        chips_drawn = 0
        overflowed = False
        for speaker in speakers:
            name = speaker.get('name') or speaker.get('diarization_label', '')
            chip_width = font_metrics.horizontalAdvance(name) + chip_padding
            if x + chip_width > max_x:
                overflowed = True
                break
            chip_rect = QRect(x, y, chip_width, chip_height)
            painter.save()
            painter.setBrush(QColor("#232323"))
            painter.setPen(QColor("#2196f3"))  # Blue border for speakers
            painter.drawRoundedRect(chip_rect, 8, 8)
            painter.setPen(QColor("#2196f3"))
            painter.drawText(chip_rect.adjusted(8, 0, -8, 0), Qt.AlignVCenter, name)
            painter.restore()
            x += chip_width + chip_spacing
            chips_drawn += 1
        if overflowed:
            ellipsis_rect = QRect(x, y, font_metrics.horizontalAdvance("...") + chip_padding, chip_height)
            painter.save()
            painter.setBrush(QColor("#232323"))
            painter.setPen(QColor("#2196f3"))
            painter.drawRoundedRect(ellipsis_rect, 8, 8)
            painter.setPen(QColor("#2196f3"))
            painter.drawText(ellipsis_rect.adjusted(8, 0, -8, 0), Qt.AlignVCenter, "...")
            painter.restore()
    def sizeHint(self, option, index):
        speakers = index.data(Qt.DisplayRole)
        if not speakers or not isinstance(speakers, list):
            return super().sizeHint(option, index)
        font_metrics = option.fontMetrics
        chip_padding = 24
        chip_spacing = 6
        width = 4
        for speaker in speakers:
            name = speaker.get('name') or speaker.get('diarization_label', '')
            width += font_metrics.horizontalAdvance(name) + chip_padding + chip_spacing
        width += 4
        return QSize(width, font_metrics.height() + 12)

# --- AnimatedMenu class for animated expansion ---
class AnimatedMenu(QMenu):
    def __init__(self, parent=None, start_pos=None, theme_name="dark"):
        super().__init__(parent)
        from PySide6 import QtWidgets
        self.setStyle(QtWidgets.QStyleFactory.create("Fusion"))
        self._start_pos = start_pos
        self._animation = QPropertyAnimation(self, b"geometry")
        self._animation.setDuration(150)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._final_geometry = None
        self._theme_name = theme_name
        # Drop shadow
        effect = QGraphicsDropShadowEffect(self)
        effect.setBlurRadius(16)
        effect.setOffset(0, 4)
        self.setGraphicsEffect(effect)
        # Apply modern menu QSS
        try:
            from nojoin.utils.theme_utils import get_modern_menu_qss
            self.setStyleSheet(get_modern_menu_qss(theme_name))
        except Exception:
            pass

    def showEvent(self, event):
        # Re-apply QSS in case theme changed
        try:
            from nojoin.utils.theme_utils import get_modern_menu_qss
            self.setStyleSheet(get_modern_menu_qss(self._theme_name))
        except Exception:
            pass
        if self._start_pos:
            # Calculate final geometry
            final_geom = self.geometry()
            self._final_geometry = final_geom
            w, h = final_geom.width(), final_geom.height()
            # Start from a small rect at the click point
            start_rect = QRect(self._start_pos.x(), self._start_pos.y(), 10, 10)
            self.setGeometry(start_rect)
            self._animation.setStartValue(start_rect)
            self._animation.setEndValue(final_geom)
            self._animation.start()
        super().showEvent(event)

# --- Speaker Name QLineEdit with focus/click playback ---
class SpeakerNameLineEdit(QLineEdit):
    def __init__(self, *args, speaker_id=None, recording_id=None, play_callback=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.speaker_id = speaker_id
        self.recording_id = recording_id
        self.play_callback = play_callback
    def focusInEvent(self, event):
        super().focusInEvent(event)
        if callable(self.play_callback) and self.speaker_id is not None and self.recording_id is not None:
            self.play_callback(self.speaker_id, self.recording_id)
    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if callable(self.play_callback) and self.speaker_id is not None and self.recording_id is not None:
            self.play_callback(self.speaker_id, self.recording_id)

# --- Simple Spinner Dialog for Saving ---
class SpeakerNameSaveSpinnerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Saving Speaker Name...")
        self.setModal(True)
        self.setFixedSize(180, 80)
        layout = QVBoxLayout(self)
        label = QLabel("Saving speaker name...")
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
        spinner = QProgressBar(self)
        spinner.setRange(0, 0)  # Indeterminate
        layout.addWidget(spinner)

class MeetingListItemWidget(QWidget):
    def __init__(self, recording_data: dict, theme_name: str, parent=None):
        super().__init__(parent)
        self.recording_data = recording_data
        self.theme_name = theme_name
        self._init_ui()

    def _init_ui(self):
        self.title_label = QLabel()
        self.title_label.setObjectName("MeetingListItemTitleLabel")
        self.title_label.setWordWrap(True)
        self.datetime_label = QLabel()
        self.datetime_label.setObjectName("MeetingListItemDateTimeLabel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(2)
        layout.addWidget(self.title_label)
        layout.addWidget(self.datetime_label)
        self.update_content(self.recording_data, self.theme_name)

    def update_content(self, recording_data: dict, theme_name: str):
        self.recording_data = recording_data
        self.theme_name = theme_name
        meeting_title = self.recording_data.get("name", "Untitled Meeting")
        self.title_label.setText(meeting_title)
        created_at = self.recording_data.get("created_at")
        import datetime
        date_str_f = "Unknown date"
        time_str_f = ""
        day_str_f = ""
        formatted_datetime_string = "Date/Time N/A"
        dt = None
        if created_at:
            if isinstance(created_at, datetime.datetime):
                dt = created_at
            elif isinstance(created_at, str):
                try:
                    dt = datetime.datetime.fromisoformat(created_at)
                except Exception:
                    try:
                        dt = datetime.datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        dt = None
            if dt and dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            local_tz = None
            try:
                from zoneinfo import ZoneInfo
                import tzlocal
                import os
                local_tz = tzlocal.get_localzone()
            except Exception:
                try:
                    local_tz = ZoneInfo(os.environ.get('TZ', 'Europe/London'))
                except Exception:
                    local_tz = datetime.timezone.utc
            if dt:
                dt_local = dt.astimezone(local_tz)
                day_str_f = dt_local.strftime("%a")
                date_str_f = dt_local.strftime("%d %b")
                time_str_f = dt_local.strftime("%H:%M")
                formatted_datetime_string = f"{day_str_f} {date_str_f} - {time_str_f}"
            else:
                formatted_datetime_string = "Date/Time N/A"
        else:
            formatted_datetime_string = "Date/Time N/A"
        self.datetime_label.setText(formatted_datetime_string)

    def sizeHint(self):
        return QSize(280, 48)

# --- Chat Message Widget ---
class ChatMessageWidget(QWidget):
    def __init__(self, sender, message, theme_name, is_user=False, timestamp=None, parent=None):
        super().__init__(parent)
        self.sender = sender
        self.message = message
        self.is_user = is_user
        self.theme_name = theme_name
        self.timestamp = timestamp
        self.setObjectName("ChatMessageWidget")
        self.setProperty("is_user", str(self.is_user).lower())
        self._init_ui()
        self.update_theme(theme_name)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(2)
        # Sender label (optional)
        if self.sender:
            sender_label = QLabel(self.sender)
            sender_label.setObjectName("ChatSenderLabel")
            sender_label.setAlignment(Qt.AlignRight if self.is_user else Qt.AlignLeft)
            layout.addWidget(sender_label)
        # Message content (QLabel instead of QTextEdit for tight fit)
        from nojoin.utils.theme_utils import wrap_html_body
        import markdown2
        html = markdown2.markdown(self.message)
        self.content = QLabel()
        self.content.setTextFormat(Qt.RichText)
        self.content.setWordWrap(True)
        self.content.setText(wrap_html_body(html, self.theme_name))
        self.content.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Minimum)
        layout.addWidget(self.content)
        # Timestamp (optional)
        if self.timestamp:
            ts_label = QLabel(self.timestamp)
            ts_label.setObjectName("ChatTimestampLabel")
            ts_label.setAlignment(Qt.AlignRight if self.is_user else Qt.AlignLeft)
            layout.addWidget(ts_label)
        # Alignment
        layout.setAlignment(Qt.AlignRight if self.is_user else Qt.AlignLeft)
        self.setLayout(layout)

    def update_theme(self, theme_name):
        from nojoin.utils.theme_utils import apply_theme_to_widget, wrap_html_body
        self.theme_name = theme_name
        apply_theme_to_widget(self, theme_name)
        import markdown2
        html = markdown2.markdown(self.message)
        self.content.setText(wrap_html_body(html, self.theme_name))

# --- Typing Indicator Widget ---
class TypingIndicatorWidget(QWidget):
    def __init__(self, theme_name, parent=None):
        super().__init__(parent)
        self.theme_name = theme_name
        self._init_ui()
        self.update_theme(theme_name)

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        # Spinner (simple animated dots)
        self.spinner_label = QLabel("● ● ●")
        self.spinner_label.setObjectName("TypingSpinnerLabel")
        layout.addWidget(self.spinner_label)
        self.setLayout(layout)

    def update_theme(self, theme_name):
        from nojoin.utils.theme_utils import apply_theme_to_widget
        self.theme_name = theme_name
        apply_theme_to_widget(self, theme_name)
