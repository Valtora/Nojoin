import sys
import os
import logging
from datetime import datetime, timedelta
import time
import threading
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
from PySide6.QtGui import QStandardItemModel, QStandardItem, QAction, QIcon, QPixmap, QPainter, QBrush, QColor, QFont, QTextListFormat, QKeySequence, QShortcut, QTextDocument # Added QTextDocument
from PySide6 import QtWidgets
from nojoin.utils.theme_utils import THEME_PALETTE, FONT_HIERARCHY, wrap_html_body, get_notes_font_size
# Attempt to import Recorder, handle potential errors
try:
    from ..audio.recorder import AudioRecorder
except ImportError:
    try:
        from nojoin.audio.recorder import AudioRecorder
    except ImportError as e:
        print(f"FATAL: Could not import AudioRecorder: {e}")
        sys.exit(1)

# Import database functions
try:
    from ..db import database as db_ops
except ImportError:
    try:
        from nojoin.db import database as db_ops
    except ImportError as e:
        print(f"FATAL: Could not import database module: {e}")
        sys.exit(1)

# Import Processing Pipeline function
try:
    from ..processing import pipeline as processing_pipeline
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
    get_available_themes, # Import theme getter
)

from nojoin.utils.theme_utils import apply_theme_to_widget, get_theme_qss, get_menu_qss, get_border_color, wrap_html_body, get_notes_font_size

from .playback_controller import PlaybackController
from nojoin.processing.recording_pipeline import RecordingPipeline
from .settings_dialog import SettingsDialog
from .processing_dialog import ProcessingProgressDialog
from .meeting_notes_progress_dialog import MeetingNotesProgressDialog
from nojoin.audio.importer import import_multiple_audio_files
from nojoin.utils.speaker_label_manager import SpeakerLabelManager
from nojoin.search.search_logic import SearchEngine
from .participants_dialog import ParticipantsDialog
from .search_bar_widget import SearchBarWidget
from nojoin.processing.LLM_Services import get_llm_backend
from .meeting_notes_worker import MeetingNotesWorker
from .find_replace_dialog import FindReplaceDialog
from .update_dialog import check_for_updates_on_startup

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

# Note: ModelDownloadWorker moved to model_download_dialog.py

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
        self.center_panel_view_mode = "notes"  # "notes" or "transcript"
        self.view_toggle_button = None
        self.notes_undo_button = None
        self.notes_redo_button = None
        self.currently_selected_recording_id = None # Initialize the attribute
        # --- End robust early attribute init ---
        self.setWindowTitle("Nojoin")
        self.setGeometry(100, 100, 1200, 800)
        
        # --- Initialize UI Scale Manager ---
        from nojoin.utils.ui_scale_manager import get_ui_scale_manager
        self.ui_scale_manager = get_ui_scale_manager()
        
        # Set minimum size based on screen resolution
        min_sizes = self.ui_scale_manager.get_scaled_minimum_sizes()
        min_width, min_height = min_sizes['main_window']
        self.setMinimumSize(min_width, min_height)

        # --- Base Spacing Unit (scaled) ---
        self.BASE_SPACING = self.ui_scale_manager.get_scaled_base_spacing(8)

        # Set application icon
        icon_path = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "NojoinLogo.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        else:
            logger.warning(f"Application icon not found at: {icon_path}")

        # --- Apply Theme (loaded from config) ---
        theme = config_manager.get("theme", "dark")
        font_scale = self.ui_scale_manager.get_font_scale_factor()
        self.apply_theme(theme, font_scale)

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

        # Connect to UI scale changes
        self.ui_scale_manager.scale_changed.connect(self._on_ui_scale_changed)

        # In MainWindow.__init__ (or as a class attribute):
        self._meeting_notes_worker = None

        # Ensure theme is applied as the very last step of __init__ to catch all UI elements
        if hasattr(self, 'current_theme') and self.current_theme:
            self.apply_theme(self.current_theme)
        else:
            # Fallback if current_theme isn't set yet (should be by setup_ui)
            self.apply_theme(config_manager.get("theme", "dark"))
        
        # Check for updates on startup (after UI is fully initialized)
        QTimer.singleShot(2000, self._check_for_updates_on_startup)  # 2 second delay
        
        # Check for first-run model download prompt (after UI is shown)
        QTimer.singleShot(500, self._check_first_run_model_download)

    def _configure_notes_toolbar_button(self, button: QPushButton, icon_file_prefix: str, tooltip_text: str, theme_name: str, scale_immune: bool = False):
        """Configures a notes toolbar button with a theme-aware icon and style."""
        # Corrected path to include 'icons' subdirectory and match new naming convention
        file_name = f"{icon_file_prefix}{theme_name.capitalize()}Mode.png"
        icon_path = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "icons", file_name)
        
        if os.path.exists(icon_path):
            button.setIcon(QIcon(icon_path))
            # Use fixed icon size for scale-immune buttons, scaled for others
            if scale_immune:
                button.setIconSize(QSize(26, 26))  # Fixed size regardless of scaling
            else:
                button.setIconSize(QSize(26, 26))  # Currently keeping same icon size for both
        else:
            button.setIcon(QIcon()) # Clear icon if not found
            self.logger.warning(f"Icon not found for {tooltip_text} ({theme_name}): {icon_path}")
        
        button.setText("") # No text label
        
        # Set button size - use fixed size for scale-immune buttons
        if scale_immune:
            # Use fixed 32x32 size regardless of UI scaling
            button.setFixedSize(32, 32)
        else:
            # Use scaled size based on BASE_SPACING
            button.setFixedSize(int(self.BASE_SPACING * 4), int(self.BASE_SPACING * 4))
        
        button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed) # Explicitly set size policy
        button.setToolTip(tooltip_text)

    def apply_theme(self, theme_name, font_scale_factor: float = 1.0):
        self.current_theme = theme_name  # Track the current theme for context menus
        apply_theme_to_widget(self, theme_name, font_scale_factor)
        # Re-apply to settings dialog if open
        if hasattr(self, 'settings_dialog') and self.settings_dialog:
            apply_theme_to_widget(self.settings_dialog, theme_name, font_scale_factor)
        # Update settings button accent color
        if hasattr(self, 'settings_button'):
            self._set_settings_button_accent()
        
        # --- Meeting Notes/Transcript Area: Apply theme ---
        if hasattr(self, 'meeting_notes_edit'):
            self._update_center_panel_content() # This ensures notes/transcript is re-rendered with new theme

        # Apply theme to meeting context widgets
        if hasattr(self, 'meeting_context_container'):
            apply_theme_to_widget(self.meeting_context_container, theme_name, font_scale_factor)
        if hasattr(self, 'editable_meeting_name'):
            apply_theme_to_widget(self.editable_meeting_name, theme_name, font_scale_factor)
        if hasattr(self, 'meeting_metadata_display'):
            apply_theme_to_widget(self.meeting_metadata_display, theme_name, font_scale_factor)
        # Speaker Relabelling: apply border to panel, not scroll area
        if hasattr(self, 'speakerLabelingPanel'):
            self.speakerLabelingPanel.setStyleSheet("")
        if hasattr(self, 'speakerLabelingScroll'):
            self.speakerLabelingScroll.setStyleSheet("")
        # Apply theme to panels
        for panel_name in ["MainPanelLeft", "MainPanelCenter", "MainPanelRight"]:
            panel = self.findChild(QFrame, panel_name)
            if panel:
                apply_theme_to_widget(panel, theme_name, font_scale_factor)
                panel.setStyleSheet("")  # Remove any direct stylesheet that could override QSS
        # 1. Theme-responsive 'Participants' title
        # if hasattr(self, 'speaker_label_title'):
        #     apply_theme_to_widget(self.speaker_label_title, theme_name)
        # 2. Theme-responsive 'Add Label' button
        if hasattr(self, 'add_label_btn'):
            apply_theme_to_widget(self.add_label_btn, theme_name, font_scale_factor)
        # --- New: Apply theme to chat header and chat display area ---
        if hasattr(self, 'chat_header_label'):
            apply_theme_to_widget(self.chat_header_label, theme_name, font_scale_factor)
        if hasattr(self, 'chat_display_area'):
            apply_theme_to_widget(self.chat_display_area, theme_name, font_scale_factor)
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
            apply_theme_to_widget(self.audio_warning_banner, theme_name, font_scale_factor)
        # Update slider style for theme
        if hasattr(self, 'seek_slider') and hasattr(self, 'volume_slider'):
            if theme_name == "dark":
                slider_groove = "#444444"
                slider_handle = "#ffb74d"
            else:
                slider_groove = "#cccccc"
                slider_handle = "#007aff"
            slider_qss = f"""
            QSlider::groove:horizontal {{
                border: 1px solid {slider_groove};
                height: 4px;
                background: {slider_groove};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {slider_handle};
                border: 1px solid {slider_handle};
                width: 14px;
                border-radius: 7px;
            }}
            """
            self.seek_slider.setStyleSheet(slider_qss)
            self.volume_slider.setStyleSheet(slider_qss)

        # --- Refresh Meeting List Item Widgets with new theme ---
        if hasattr(self, 'meetings_list_widget'):
            for i in range(self.meetings_list_widget.count()):
                item = self.meetings_list_widget.item(i)
                widget = self.meetings_list_widget.itemWidget(item)
                if isinstance(widget, MeetingListItemWidget): # Check instance type
                    # Assuming widget.recording_data holds the necessary data to refresh
                    if hasattr(widget, 'recording_data') and widget.recording_data is not None:
                         widget.update_content(widget.recording_data, theme_name)
                    else:
                        # Fallback: if recording_data is not directly on widget, try to get from item
                        # This might be necessary if the widget's state isn't self-contained
                        # For now, log a warning if this happens, as it implies a design detail
                        logger.warning(f"MeetingListItemWidget at index {i} does not have recording_data attribute for theme refresh.")

        # --- Update notes toolbar button icons on theme change ---
        if hasattr(self, 'notes_undo_button') and self.notes_undo_button is not None:
            self._configure_notes_toolbar_button(self.notes_undo_button, "Undo", "Undo", theme_name, scale_immune=True)
        if hasattr(self, 'notes_redo_button') and self.notes_redo_button is not None:
            self._configure_notes_toolbar_button(self.notes_redo_button, "Redo", "Redo", theme_name, scale_immune=True)
        if hasattr(self, 'copy_notes_button') and self.copy_notes_button is not None: # Ensure this attribute name matches setup_ui
            self._configure_notes_toolbar_button(self.copy_notes_button, "CopyToClip", "CopyToClip", theme_name, scale_immune=True)
        if hasattr(self, 'find_replace_button') and self.find_replace_button is not None:
            self._configure_notes_toolbar_button(self.find_replace_button, "Search", "Find and Replace", theme_name, scale_immune=True)

    def _set_settings_button_accent(self):
        theme = config_manager.get("theme", "dark")
        if theme == "dark":
            accent_val = "#ff9800"
            accent2_val = "#ff6f00"
            hover_accent_val = "#ffac33"  # Lighter orange for hover
            text_val = "#181818"
        else:
            accent_val = "#007acc"
            accent2_val = "#005f9e"
            hover_accent_val = "#3394cc"  # Lighter blue for hover
            text_val = "#ffffff"

        # Corrected QSS template:
        # - QSS blocks use {{ and }}
        # - .format() placeholders use {key}
        qss_template = """
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {p_accent_stop0}, stop:1 {p_accent_stop1});
                color: {p_text_color};
                border: none;
                border-radius: 6px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {p_hover_accent_stop0}, stop:1 {p_hover_accent_stop1});
            }}
            QPushButton:pressed {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {p_pressed_accent_stop0}, stop:1 {p_pressed_accent_stop1});
            }}
        """
        
        final_qss = qss_template.format(
            p_accent_stop0=accent_val,
            p_accent_stop1=accent2_val,
            p_text_color=text_val,
            p_hover_accent_stop0=hover_accent_val,
            p_hover_accent_stop1=accent_val,
            p_pressed_accent_stop0=accent2_val,
            p_pressed_accent_stop1=accent_val
        )
        self.settings_button.setStyleSheet(final_qss)

    def setup_ui(self):

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
        self.transcribe_button.setToolTip("Transcribe the selected recording")
        self.transcribe_button.setEnabled(False)
        self.transcribe_button.setMinimumWidth(self.BASE_SPACING * 14) # Give text some space
        self.transcribe_button.setFixedHeight(self.BASE_SPACING * 5)
        self.transcribe_button.clicked.connect(self.on_transcribe_clicked)
        self.transcribe_button.setVisible(False)
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
        # --- Explicitly set slider style for theme awareness ---
        from nojoin.utils.config_manager import config_manager
        theme = config_manager.get("theme", "dark")
        if theme == "dark":
            slider_groove = "#444444"
            slider_handle = "#ffb74d"
        else:
            slider_groove = "#cccccc"
            slider_handle = "#007aff"
        slider_qss = f"""
        QSlider::groove:horizontal {{
            border: 1px solid {slider_groove};
            height: 4px;
            background: {slider_groove};
            border-radius: 2px;
        }}
        QSlider::handle:horizontal {{
            background: {slider_handle};
            border: 1px solid {slider_handle};
            width: 14px;
            border-radius: 7px;
        }}
        """
        self.seek_slider.setStyleSheet(slider_qss)
        self.volume_slider.setStyleSheet(slider_qss)
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
        playback_widget.setMinimumWidth(self.ui_scale_manager.scale_value(450)) # Minimum width for playback controls + slider

        top_controls_layout.addWidget(playback_widget)
        top_controls_layout.addStretch() # Add stretch *before* settings button
        
        # --- New Global Speakers Button ---
        self.global_speakers_button = QPushButton("Global Speakers")
        self.global_speakers_button.setMinimumWidth(self.BASE_SPACING * 18) # Match record button width
        self.global_speakers_button.setFixedHeight(self.BASE_SPACING * 5)
        self.global_speakers_button.setToolTip("Manage the Global Speaker Library")
        self.global_speakers_button.clicked.connect(self._open_global_speakers_dialog)
        top_controls_layout.addWidget(self.global_speakers_button)
        # --- End New Global Speakers Button ---
        
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
        main_display_layout.setSpacing(1)  # No horizontal spacing between panes

        # --- Left: Meetings List (Card Style) ---
        left_panel = QFrame()
        left_panel.setObjectName("MainPanelLeft")
        left_panel.setStyleSheet("padding: 5px;")
        min_left_width, _ = self.ui_scale_manager.get_scaled_minimum_sizes()['left_panel']
        left_panel.setMinimumWidth(min_left_width)
        left_panel.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)
        left_panel.setContentsMargins(0, 0, 0, 0)

        # --- Search Bar Area (now a separate widget) ---
        self.search_bar_widget = SearchBarWidget()
        self.search_bar_widget.setMinimumWidth(min_left_width)
        self.search_bar_widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        if hasattr(self.search_bar_widget, 'layout') and self.search_bar_widget.layout() is not None:
            self.search_bar_widget.layout().setContentsMargins(0, 4, 0, 4)
        self.search_bar_widget.text_changed.connect(self._on_search_text_changed)
        self.search_bar_widget.cleared.connect(self._clear_search)
        left_layout.addWidget(self.search_bar_widget)

        self.meetings_list_widget = QListWidget()
        self.meetings_list_widget.setSelectionMode(QListWidget.SingleSelection)
        self.meetings_list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.meetings_list_widget.setMinimumWidth(min_left_width)
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
        min_center_width, _ = self.ui_scale_manager.get_scaled_minimum_sizes()['center_panel']
        center_panel.setMinimumWidth(min_center_width)
        center_panel.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        meeting_notes_layout = QVBoxLayout(center_panel)
        meeting_notes_layout.setContentsMargins(0, 0, 0, 0) # Reverted to 0 margins
        meeting_notes_layout.setSpacing(0)
        
        # Create meeting context container with editable name and metadata
        self.meeting_context_container = QWidget()
        self.meeting_context_container.setObjectName("MeetingContextContainer")
        self.meeting_context_container.setMaximumHeight(110)
        context_layout = QVBoxLayout(self.meeting_context_container)
        context_layout.setContentsMargins(8, 8, 8, 8)
        context_layout.setSpacing(4)
        
        # Editable meeting name
        self.editable_meeting_name = EditableMeetingName()
        self.editable_meeting_name.setObjectName("EditableMeetingName")
        self.editable_meeting_name.name_changed.connect(self._on_meeting_name_changed)
        context_layout.addWidget(self.editable_meeting_name)
        
        # Non-editable metadata display
        self.meeting_metadata_display = QLabel()
        self.meeting_metadata_display.setObjectName("MeetingMetadataDisplay")
        self.meeting_metadata_display.setWordWrap(True)
        self.meeting_metadata_display.setAlignment(Qt.AlignTop)
        context_layout.addWidget(self.meeting_metadata_display)
        
        context_layout.addStretch(1)  # Push content to top
        meeting_notes_layout.addWidget(self.meeting_context_container)
        self.meeting_tags_widget = QWidget()
        self.meeting_tags_layout = QHBoxLayout(self.meeting_tags_widget)
        self.meeting_tags_layout.setContentsMargins(2, 5, 2, 5)
        self.meeting_tags_layout.setSpacing(6)
        self.meeting_tags_widget.setStyleSheet("")
        meeting_notes_layout.addWidget(self.meeting_tags_widget)
        # --- Meeting Notes Toolbar ---
        self.meeting_notes_toolbar = QWidget()
        self.meeting_notes_toolbar.setObjectName("MeetingNotesToolbar") # Added object name
        self.meeting_notes_toolbar.setFixedHeight(int(self.BASE_SPACING * 5 * 1.1)) # Increased height by 10%
        # self.meeting_notes_toolbar.setStyleSheet("border: 2px solid yellow;") # DEBUG: Visual bound check REMOVED
        toolbar_layout = QHBoxLayout(self.meeting_notes_toolbar)
        toolbar_layout.setContentsMargins(3, 3, 3, 3) # Adjusted internal padding
        toolbar_layout.setSpacing(4)
        toolbar_layout.setAlignment(Qt.AlignVCenter) # Align widgets vertically centered

        # --- New Toggle Button for Notes/Transcript ---
        self.view_toggle_button = QPushButton("View Transcript")
        self.view_toggle_button.setFixedHeight(int(self.BASE_SPACING * 4)) # Set height to BASE_SPACING * 4
        self.view_toggle_button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.view_toggle_button.setToolTip("Toggle between meeting notes and transcript view")
        # self.view_toggle_button.clicked.connect(self._toggle_center_panel_view) # Connection will be added later
        toolbar_layout.addWidget(self.view_toggle_button)

        toolbar_layout.addStretch(1)  # Align buttons to the right
        # Undo
        self.notes_undo_button = QPushButton() # Initialize without text
        self.notes_undo_button.clicked.connect(lambda: self.meeting_notes_edit.undo())
        toolbar_layout.addWidget(self.notes_undo_button)
        # Redo
        self.notes_redo_button = QPushButton() # Initialize without text
        self.notes_redo_button.clicked.connect(lambda: self.meeting_notes_edit.redo())
        toolbar_layout.addWidget(self.notes_redo_button)
        # Copy to Clipboard
        self.copy_notes_button = QPushButton() # Initialize without text
        # self.copy_notes_button.setStyleSheet("background-color: green;") # DEBUG: Visual bound check REMOVED
        def copy_notes_to_clipboard():
            from PySide6.QtWidgets import QApplication
            clipboard = QApplication.clipboard()
            clipboard.setText(self.meeting_notes_edit.toPlainText())
        self.copy_notes_button.clicked.connect(copy_notes_to_clipboard)
        toolbar_layout.addWidget(self.copy_notes_button)
        
        # Find/Replace
        self.find_replace_button = QPushButton() # Initialize without text
        self.find_replace_button.setToolTip("Find and Replace")
        self.find_replace_button.clicked.connect(self._open_find_replace_dialog)
        toolbar_layout.addWidget(self.find_replace_button)
        
        meeting_notes_layout.addWidget(self.meeting_notes_toolbar)
        # --- Meeting Notes Edit ---
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
        # Find/Replace shortcut
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._open_find_replace_dialog)
        # --- Debounced autosave setup ---
        self._notes_autosave_timer = QTimer(self)
        self._notes_autosave_timer.setSingleShot(True)
        self._notes_autosave_timer.setInterval(1500)  # 1.5 seconds debounce
        self.meeting_notes_edit.textChanged.connect(self._on_notes_edited_autosave)
        self.meeting_notes_edit.textChanged.connect(self._on_notes_text_changed)
        self._notes_last_saved_content = ""
        # Track if the current content is a placeholder
        self._is_placeholder_content = False
        meeting_notes_layout.addWidget(self.meeting_notes_edit, 3)
        main_display_layout.addWidget(center_panel, 2)

        # --- Right: Meeting Chat ---
        chat_panel = QFrame()
        chat_panel.setObjectName("MainPanelRight")
        chat_panel.setStyleSheet("padding: 4px;")
        min_right_width, _ = self.ui_scale_manager.get_scaled_minimum_sizes()['right_panel']
        chat_panel.setMinimumWidth(min_right_width)
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
        self.chat_send_button.setToolTip("Send a message to the AI")
        self.chat_send_button.setText("Send") # Changed from icon to text
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
        self._update_center_panel_content() # Call at end of setup_ui for initial state

        # --- Configure notes toolbar buttons with initial theme ---
        current_theme = config_manager.get("theme", "dark")
        self._configure_notes_toolbar_button(self.notes_undo_button, "Undo", "Undo", current_theme, scale_immune=True)
        self._configure_notes_toolbar_button(self.notes_redo_button, "Redo", "Redo", current_theme, scale_immune=True)
        self._configure_notes_toolbar_button(self.copy_notes_button, "CopyToClip", "Copy to Clipboard", current_theme, scale_immune=True)
        self._configure_notes_toolbar_button(self.find_replace_button, "Search", "Find and Replace", current_theme, scale_immune=True)

        # Connect the toggle button after all UI elements it might affect are initialized
        if self.view_toggle_button: # Ensure it was created
            self.view_toggle_button.clicked.connect(self._toggle_center_panel_view)
        
        # Apply initial compact mode adaptations
        self._apply_compact_mode_adaptations()

    def _on_ui_scale_changed(self, new_scale_factor):
        """Handle UI scale factor changes."""
        logger.info(f"UI scale changed to: {new_scale_factor}")
        
        # Update BASE_SPACING
        self.BASE_SPACING = self.ui_scale_manager.get_scaled_base_spacing(8)
        
        # Update minimum sizes
        min_sizes = self.ui_scale_manager.get_scaled_minimum_sizes()
        
        # Update main window minimum size
        min_width, min_height = min_sizes['main_window']
        self.setMinimumSize(min_width, min_height)
        
        # Update panel minimum widths
        if hasattr(self, 'meetings_list_widget'):
            left_width, _ = min_sizes['left_panel']
            self.meetings_list_widget.setMinimumWidth(left_width)
            if hasattr(self, 'search_bar_widget'):
                self.search_bar_widget.setMinimumWidth(left_width)
        
        # Find and update panels by object name
        for panel_name, (min_w, min_h) in [
            ('MainPanelLeft', min_sizes['left_panel']),
            ('MainPanelCenter', min_sizes['center_panel']), 
            ('MainPanelRight', min_sizes['right_panel'])
        ]:
            panel = self.findChild(QFrame, panel_name)
            if panel and min_w > 0:
                panel.setMinimumWidth(min_w)
        
        # Update playback widget if it exists
        playback_widgets = self.findChildren(QWidget)
        for widget in playback_widgets:
            if hasattr(widget, 'layout') and widget.layout():
                # Look for playback controls layout signature
                layout = widget.layout()
                if (isinstance(layout, QHBoxLayout) and 
                    layout.count() > 5 and 
                    any(isinstance(layout.itemAt(i).widget(), QSlider) for i in range(layout.count()) if layout.itemAt(i) and layout.itemAt(i).widget())):
                    widget.setMinimumWidth(self.ui_scale_manager.scale_value(450))
                    break
        
        # Apply compact mode adaptations
        self._apply_compact_mode_adaptations()
        
        # Re-apply theme with new font scaling
        if hasattr(self, 'current_theme'):
            font_scale = self.ui_scale_manager.get_font_scale_factor()
            self.apply_theme(self.current_theme, font_scale)
        
        logger.info("UI scaling update completed")

    def _apply_compact_mode_adaptations(self):
        """Apply UI adaptations for compact screen mode."""
        is_compact = self.ui_scale_manager.is_compact_mode()
        
        # In compact mode, make certain UI elements more space-efficient
        if is_compact:
            # Reduce seek slider width in compact mode
            if hasattr(self, 'seek_slider'):
                self.seek_slider.setFixedWidth(self.ui_scale_manager.scale_value(200))  # Reduced from 300
            
            # Hide less critical buttons or text in compact mode
            if hasattr(self, 'timer_label'):
                self.timer_label.setVisible(True)  # Keep timer visible as it's useful
            
            # Reduce button text or use icons only in extreme compact cases
            screen_width = self.ui_scale_manager.get_screen_info()['width']
            if screen_width < 1300:  # Very narrow screens
                # Optionally switch to icon-only buttons for some controls
                if hasattr(self, 'global_speakers_button'):
                    self.global_speakers_button.setText("Speakers")  # Shorter text
                if hasattr(self, 'import_audio_button'):
                    self.import_audio_button.setText("Import")  # Shorter text
        else:
            # Restore normal sizes for comfortable mode
            if hasattr(self, 'seek_slider'):
                self.seek_slider.setFixedWidth(300)  # Normal width
            
            # Restore full button text
            if hasattr(self, 'global_speakers_button'):
                self.global_speakers_button.setText("Global Speakers")
            if hasattr(self, 'import_audio_button'):
                self.import_audio_button.setText("Import Audio")



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
        
        # Disable UI elements during recording
        self._disable_ui_during_recording()

    def handle_recording_finished(self, recording_id: str, filename: str, duration: float, size: int):
        self.is_recording = False
        self.recording_timer.stop()
        self.record_button.setText("Start Meeting")
        self.status_indicator.setText("Status: Idle")
        self.timer_label.setText("00:00:00")
        self.record_button.setEnabled(True)
        base_filename = os.path.basename(filename)
        self.status_bar.showMessage(f"Recording saved: {base_filename} ({duration:.1f}s)")
        logger.info(f"UI: Recording finished: {filename}, Duration: {duration}, Size: {size}, ID: {recording_id}")
        
        # Stop audio level monitoring
        self.audio_level_timer.stop()
        self.no_audio_timer.stop()
        self.audio_warning_banner.setVisible(False)
        
        # Re-enable UI elements after recording
        self._enable_ui_after_recording()
        
        # Database entry is now handled by RecordingPipeline
        # Remove the following block:
        # import datetime
        # from nojoin.db import database as db_ops
        # start_time = None
        # end_time = None
        # if self.recording_start_time is not None:
        #     start_dt = datetime.datetime.fromtimestamp(self.recording_start_time)
        #     end_dt = datetime.datetime.now()
        #     start_time = start_dt.isoformat(sep=" ", timespec="seconds")
        #     end_time = end_dt.isoformat(sep=" ", timespec="seconds")
        # recording_name = f"Meeting - {base_filename}"
        # db_ops.add_recording(
        #     name=recording_name,
        #     audio_path=filename,
        #     duration=duration,
        #     size_bytes=size,
        #     format="MP3",
        #     start_time=start_time,
        #     end_time=end_time
        # )

        self.load_recordings()

        # --- Auto-transcribe if enabled --- 
        # This now uses the recording_id passed from RecordingPipeline
        if config_manager.get("auto_transcribe_on_recording_finish", True):  # Changed default to True
            from nojoin.db import database as db_ops # Keep import for get_recording_by_id
            # Fetch the specific recording data using the provided recording_id
            recording_data_for_processing = db_ops.get_recording_by_id(recording_id)
            if recording_data_for_processing:
                # Convert Row to dict if necessary, and ensure audio_path is correct
                recording_dict = dict(recording_data_for_processing)
                audio_path_from_db = recording_dict.get('audio_path')
                # Ensure the path from DB is used and is valid
                if audio_path_from_db and os.path.exists(from_project_relative_path(audio_path_from_db)):
                    logger.info(f"Auto-transcribe enabled: starting processing for recording ID {recording_id} (Name: {recording_dict.get('name')})")
                    self.process_selected_recording(recording_id, recording_dict)
                else:
                    logger.warning(f"Auto-transcribe: audio file not found or path missing in DB for recording ID {recording_id}. Path from DB: {audio_path_from_db}")
            else:
                logger.warning(f"Auto-transcribe: recording data not found in DB for ID {recording_id}")

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
        
        # Re-enable UI elements after recording error
        self._enable_ui_after_recording()
        
        QMessageBox.warning(self, "Recording Error", error_message)
        print(f"UI: Recording error: {error_message}")

    def _disable_ui_during_recording(self):
        """Disable UI elements that should not be accessible during recording."""
        # Disable playback controls
        if hasattr(self, 'play_button'):
            self.play_button.setEnabled(False)
        if hasattr(self, 'pause_button'):
            self.pause_button.setEnabled(False)
        if hasattr(self, 'stop_button'):
            self.stop_button.setEnabled(False)
        if hasattr(self, 'seek_slider'):
            self.seek_slider.setEnabled(False)
        if hasattr(self, 'volume_slider'):
            self.volume_slider.setEnabled(False)
        
        # Disable transcribe button if visible
        if hasattr(self, 'transcribe_button'):
            self.transcribe_button.setEnabled(False)
        
        logger.info("UI elements disabled during recording")

    def _enable_ui_after_recording(self):
        """Re-enable UI elements after recording finishes."""
        # Re-enable playback controls (but only if a recording is selected)
        selected_items = self.meetings_list_widget.selectedItems()
        has_selection = bool(selected_items)
        
        if hasattr(self, 'play_button'):
            self.play_button.setEnabled(has_selection)
        if hasattr(self, 'pause_button'):
            self.pause_button.setEnabled(False)  # Initially disabled until playing
        if hasattr(self, 'stop_button'):
            self.stop_button.setEnabled(False)   # Initially disabled until playing
        if hasattr(self, 'seek_slider'):
            self.seek_slider.setEnabled(has_selection)
        if hasattr(self, 'volume_slider'):
            self.volume_slider.setEnabled(True)
        
        # Re-enable transcribe button if visible and recording selected
        if hasattr(self, 'transcribe_button') and has_selection:
            self.transcribe_button.setEnabled(True)
        
        logger.info("UI elements re-enabled after recording")

    def _on_notes_text_changed(self):
        """Handle text changes in meeting notes to manage placeholder read-only state."""
        if not hasattr(self, 'meeting_notes_edit'):
            return
        
        # Check if the current content is a placeholder by comparing with known placeholder texts
        current_content = self.meeting_notes_edit.document().toPlainText().strip()
        placeholder_texts = [
            "Select a meeting to view notes or transcript.",
            "No meeting notes available. Right-click the recording to generate notes, or switch to transcript view.",
            "No meeting notes available. Right-click the recording to re-transcribe or generate notes.",
            "Meeting notes cannot be generated right now.",
            "Error loading meeting data."
        ]
        
        # Check if current content is empty or matches a placeholder pattern
        is_placeholder = (not current_content or 
                         any(placeholder in current_content for placeholder in placeholder_texts) or
                         current_content.startswith("_") and current_content.endswith("_"))
        
        # Update read-only state based on content
        if is_placeholder != self._is_placeholder_content:
            self._is_placeholder_content = is_placeholder
            # Make read-only if showing placeholder, editable if showing real content
            self.meeting_notes_edit.setReadOnly(is_placeholder)

    def _set_placeholder_content(self, placeholder_text):
        """Set placeholder content and make it read-only."""
        if not hasattr(self, 'meeting_notes_edit'):
            return
        
        doc = self.meeting_notes_edit.document()
        # Apply current theme
        theme_palette = THEME_PALETTE.get(self.current_theme, THEME_PALETTE["dark"])
        text_color = theme_palette['html_text']
        css = f"body {{ color: {text_color}; background-color: transparent; }}"
        doc.setDefaultStyleSheet(css)
        base_font = QFont("Segoe UI", get_notes_font_size())
        doc.setDefaultFont(base_font)
        
        # Set the placeholder as markdown
        doc.setMarkdown(f"_{placeholder_text}_", QTextDocument.MarkdownDialectGitHub)
        self._is_placeholder_content = True
        self.meeting_notes_edit.setReadOnly(True)

    def update_recording_timer_display(self):
        if self.is_recording and self.recording_start_time is not None:
            elapsed_seconds = int(time.monotonic() - self.recording_start_time)
            # Format as HH:MM:SS
            hours, remainder = divmod(elapsed_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            elapsed_time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
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
                    self._load_recordings_with_preserved_selection(recording_id)
                else:
                    QMessageBox.warning(self, "Rename Failed", "Could not rename the recording in the database.")
            elif new_name == current_name:
                self.status_bar.showMessage("Recording name unchanged.", 2000)
            else: # Empty new name after strip
                QMessageBox.warning(self, "Invalid Name", "Recording name cannot be empty.")
        elif ok and not new_name.strip(): # User entered only spaces or nothing
             QMessageBox.warning(self, "Invalid Name", "Recording name cannot be empty.")

    def delete_selected_recording(self, recording_id, recording_data):
        logger.info(f"delete_selected_recording called for ID: {recording_id}, Data: {recording_data}")
        reply = QMessageBox.question(self, "Delete Recording", 
                                     f"Are you sure you want to delete the recording: '{recording_data.get('name', recording_id)}'?",
                                     QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            # Stop playback if this recording is currently playing or loaded
            current_audio_path = self.playback_controller.audio_path
            db_audio_path_relative = recording_data.get('audio_path')
            
            logger.info(f"Current playback audio path: {current_audio_path}")
            logger.info(f"Recording to delete audio path (relative): {db_audio_path_relative}")

            if db_audio_path_relative:
                db_audio_path_abs = from_project_relative_path(db_audio_path_relative)
                logger.info(f"Recording to delete audio path (absolute): {db_audio_path_abs}")
                if current_audio_path and os.path.normpath(current_audio_path) == os.path.normpath(db_audio_path_abs):
                    logger.info(f"Recording {recording_id} is currently loaded in playback_controller. Stopping playback.")
                    self.playback_controller.stop() # Ensure playback is stopped
                    logger.info(f"Playback_controller stopped for {recording_id}.")
            else:
                    logger.info(f"Recording {recording_id} is NOT currently loaded in playback_controller. No stop needed for main player.")
        else:
                logger.warning(f"No audio_path found in recording_data for {recording_id} during delete operation.")

        logger.info(f"Attempting to delete recording {recording_id} from database and file system.")
        success = db_ops.delete_recording(recording_id)
        if success:
                logger.info(f"Successfully deleted recording {recording_id}.")
                QMessageBox.information(self, "Success", "Recording deleted successfully.")
                self.load_recordings()  # Refresh the list
                
                # Check if the deleted recording was the one displayed
                if self.currently_selected_recording_id == recording_id:
                    self._clear_meeting_details() # Clear details panel
                    self.currently_selected_recording_id = None # No recording is selected now
                
                # If meetings list is now empty, ensure no selection state
                if self.meetings_list_widget.count() == 0:
                    self._clear_meeting_details() # Clear details panel
                    self.currently_selected_recording_id = None
                    # Explicitly disable playback controls and other relevant UI elements
                    self.play_button.setEnabled(False)
                    self.pause_button.setEnabled(False)
                    self.stop_button.setEnabled(False)
                    self.seek_slider.setEnabled(False)
                    self.seek_slider.setValue(0)
                    self.update_seek_time_label(0, 0)
                    self.selected_audio_path = None # Clear selected audio path
                    self.chat_input.setEnabled(False)
                    self.chat_send_button.setEnabled(False)
                    self.clear_chat_button.setEnabled(False)
                elif not self.meetings_list_widget.selectedItems():
                    # If list is not empty, but nothing is selected (e.g. after deleting the only item)
                    # The handle_meeting_selection_changed should take care of resetting UI
                    # but explicitly setting currently_selected_recording_id is good practice.
                    self.currently_selected_recording_id = None

        else:
                logger.error(f"Failed to delete recording {recording_id}.")
                QMessageBox.critical(self, "Error", "Failed to delete recording. Check logs for details.")

    def _context_view_edit_meeting_notes(self, recording_id, recording_data):
        # This function is primarily for when user right-clicks and wants to ensure notes are visible.
        # The actual loading and styling is handled by handle_meeting_selection_changed.
        # We just need to make sure the selection triggers that if not already selected.
        
        # Check if the item is already selected
        current_selection = self.meetings_list_widget.selectedItems()
        is_already_selected = False
        if current_selection:
            if current_selection[0].data(Qt.UserRole) == recording_id:
                is_already_selected = True

        if not is_already_selected:
            # Find and select the item to trigger handle_meeting_selection_changed
            for i in range(self.meetings_list_widget.count()):
                item = self.meetings_list_widget.item(i)
                if item.data(Qt.UserRole) == recording_id:
                    # item.setSelected(True) # This might trigger selectionChanged if not blocked
                    self.meetings_list_widget.setCurrentItem(item, QItemSelectionModel.ClearAndSelect)
                    break
        else:
            # If already selected, ensure notes are visible (handle_meeting_selection_changed should have loaded them)
             pass


        # The old logic for manually setting HTML is removed, as handle_meeting_selection_changed now does it with setMarkdown
        # notes_entry = db_ops.get_meeting_notes_for_recording(recording_id)
        # from nojoin.utils.theme_utils import wrap_html_body # No longer needed here
        # theme = config_manager.get("theme", "dark") # No longer needed here
        # if notes_entry:
        #     try:
        #         import markdown2  # Local import for performance
        #         html_notes = markdown2.markdown(notes_entry['notes'])
        #         # Strip <html> and <body> tags if present
        #         import re
        #         html_notes = re.sub(r'<\/?(html|body)[^>]*>', '', html_notes, flags=re.IGNORECASE)
        #         themed_html = wrap_html_body(html_notes, theme) # No longer needed
        #         self.meeting_notes_edit.setHtml(themed_html) # No longer needed
        #     except Exception as e:
        #         self.meeting_notes_edit.setPlainText(f"Error displaying notes: {e}\n{notes_entry['notes']}")
        # else:
        #    self.meeting_notes_edit.setPlainText("No meeting notes available. Right-click the recording to generate notes.")
        
        self.meeting_notes_edit.setVisible(True) # Ensure it's visible
        # self.notes_have_been_edited = False # This is reset by handle_meeting_selection_changed

    def _context_show_diarized_transcript_dialog(self, recording_id, recording_data):
        """Shows the diarized transcript in a new dialog window."""
        logger.info(f"Showing diarized transcript dialog for recording ID: {recording_id}")
        
        # This method is no longer used as transcript is shown in main panel.
        # Kept for reference during refactor, can be removed.
        # diarized_transcript_path = recording_data.get("diarized_transcript_path")
        # abs_diarized_transcript_path = from_project_relative_path(diarized_transcript_path) if diarized_transcript_path else None
        # if not abs_diarized_transcript_path or not os.path.exists(abs_diarized_transcript_path):
        #     QMessageBox.warning(self, "Transcript Missing", "Diarized transcript file not found for this recording.")
        #     return
        # dialog_title = f"Diarized Transcript - {recording_data.get('name', f'ID {recording_id}')}"
        # from .transcript_dialog import TranscriptViewDialog # Import removed
        # dialog = TranscriptViewDialog(window_title=dialog_title, parent=self, recording_id=recording_id)
        # dialog.exec()
        pass # Method no longer used

    def on_generate_meeting_notes_clicked(self, recording_id=None, recording_data=None):
        if recording_id is None or recording_data is None:
            selected_items = self.meetings_list_widget.selectedItems()
            if not selected_items:
                QMessageBox.information(self, "No Selection", "Please select a recording to generate meeting notes.")
                return
            item = selected_items[0]
            recording_id = item.data(Qt.UserRole)
            recording_data = db_ops.get_recording_by_id(recording_id)
        # Get transcript from database first, then fallback to file
        from nojoin.utils.transcript_store import TranscriptStore
        transcript = TranscriptStore.get(recording_id, "diarized")
        
        if not transcript:
            # Fallback to file-based approach for legacy recordings
            diarized_transcript_path = recording_data.get("diarized_transcript_path")
            abs_diarized_transcript_path = from_project_relative_path(diarized_transcript_path) if diarized_transcript_path else None
            if abs_diarized_transcript_path and os.path.exists(abs_diarized_transcript_path):
                try:
                    with open(abs_diarized_transcript_path, 'r', encoding='utf-8') as f:
                        transcript = f.read()
                except Exception as e:
                    logger.error(f"Failed to read transcript file for recording {recording_id}: {e}")
                    transcript = None
        
        provider = config_manager.get("llm_provider", "gemini")
        api_key = config_manager.get(f"{provider}_api_key")
        model = config_manager.get(f"{provider}_model", "gemini-2.5-flash-preview-05-20")
        if not api_key or not transcript:
            # Display transcript if API key missing, using setMarkdown
            transcript_html_content = self.load_transcript(recording_id) # This returns HTML
            # We need to convert this HTML to Markdown for setMarkdown, or find a way to display HTML directly
            # For now, let's try to set the HTML directly and see if defaultStyleSheet handles it.
            # This part is tricky as load_transcript returns themed HTML.
            # A better approach would be for load_transcript to return raw content or Markdown.
            # For now, show a simple message as Markdown.
            
            # Fallback message in Markdown
            error_markdown = (f"**Meeting notes generation failed.**\n\n"
                              f"No API key provided for {provider.title()} or transcript is missing.\n\n"
                              f"You can try viewing the raw diarized transcript via the context menu if available.")
            if hasattr(self, 'meeting_notes_edit'):
                doc = self.meeting_notes_edit.document()
                # Apply theme stylesheet before setting content
                theme_palette = THEME_PALETTE.get(self.current_theme, THEME_PALETTE["dark"])
                text_color = theme_palette['html_text']
                css = f"body {{ color: {text_color}; background-color: transparent; }}" # Simplified for this case
                doc.setDefaultStyleSheet(css)
                base_font = QFont("Segoe UI", get_notes_font_size())
                doc.setDefaultFont(base_font)
                doc.setMarkdown(error_markdown, QTextDocument.MarkdownDialectGitHub)

            self.meeting_notes_edit.setVisible(True)
            self.notes_have_been_edited = False
            self.status_bar.showMessage(f"Meeting notes not generated. No API key for {provider.title()}.", 3000)
            return
        spinner = MeetingNotesProgressDialog(self)
        spinner.show()
        backend = get_llm_backend(provider, api_key=api_key, model=model)
        worker = MeetingNotesWorker(backend, transcript)
        def on_success(notes):
            try:
                import markdown2
                html = markdown2.markdown(notes)
                import re
                html = re.sub(r'<\/?(html|body)[^>]*>', '', html, flags=re.IGNORECASE)
                from nojoin.utils.theme_utils import wrap_html_body
                theme = config_manager.get("theme", "dark")
                themed_html = wrap_html_body(html, theme)
                self.meeting_notes_edit.setHtml(themed_html)
            except Exception:
                self.meeting_notes_edit.setPlainText(notes)
            self.meeting_notes_edit.setVisible(True)
            self.notes_have_been_edited = False
            self.status_bar.showMessage("Meeting notes generated.", 3000)
            db_ops.add_meeting_notes(recording_id, provider, model, notes)
            if spinner.isVisible():
                spinner.close()
        def on_error(error):
            logger.error(f"Failed to generate meeting notes: {error}")
            placeholder = (f"Meeting notes cannot be generated right now.\nPlease check your {provider.title()} API key in settings or view the raw diarized transcript via the context menu.")
            self.meeting_notes_edit.setPlainText(placeholder)
            self.meeting_notes_edit.setVisible(True)
            self.notes_have_been_edited = False
            self.status_bar.showMessage("Failed to generate meeting notes.", 3000)
            if spinner.isVisible():
                spinner.close()
        def cleanup_worker():
            self._meeting_notes_worker = None
            if spinner.isVisible():
                spinner.close()
        worker.success.connect(on_success)
        worker.error.connect(on_error)
        worker.finished.connect(cleanup_worker)
        worker.start()
        self._meeting_notes_worker = worker

    def on_regenerate_meeting_notes_clicked(self, recording_id=None, recording_data=None):
        if recording_id is None or recording_data is None:
            selected_items = self.meetings_list_widget.selectedItems()
            if not selected_items:
                QMessageBox.information(self, "No Selection", "Please select a recording to regenerate meeting notes.")
                return
            item = selected_items[0]
            recording_id = item.data(Qt.UserRole)
            recording_data = db_ops.get_recording_by_id(recording_id)
        # Get transcript from database first, then fallback to file
        from nojoin.utils.transcript_store import TranscriptStore
        transcript = TranscriptStore.get(recording_id, "diarized")
        
        if not transcript:
            # Fallback to file-based approach for legacy recordings
            diarized_transcript_path = recording_data.get("diarized_transcript_path")
            abs_diarized_transcript_path = from_project_relative_path(diarized_transcript_path) if diarized_transcript_path else None
            if abs_diarized_transcript_path and os.path.exists(abs_diarized_transcript_path):
                try:
                    with open(abs_diarized_transcript_path, 'r', encoding='utf-8') as f:
                        transcript = f.read()
                except Exception as e:
                    logger.error(f"Failed to read transcript file for recording {recording_id}: {e}")
                    transcript = None
        
        provider = config_manager.get("llm_provider", "gemini")
        api_key = config_manager.get(f"{provider}_api_key")
        model = config_manager.get(f"{provider}_model", "gemini-2.5-flash-preview-05-20")
        if not api_key or not transcript:
            # Fallback message in Markdown
            error_markdown = (f"**Meeting notes regeneration failed.**\n\n"
                              f"No API key provided for {provider.title()} or transcript is missing.\n\n"
                              f"You can try viewing the raw diarized transcript via the context menu if available.")
            if hasattr(self, 'meeting_notes_edit'):
                doc = self.meeting_notes_edit.document()
                theme_palette = THEME_PALETTE.get(self.current_theme, THEME_PALETTE["dark"])
                text_color = theme_palette['html_text']
                css = f"body {{ color: {text_color}; background-color: transparent; }}"
                doc.setDefaultStyleSheet(css)
                base_font = QFont("Segoe UI", get_notes_font_size())
                doc.setDefaultFont(base_font)
                doc.setMarkdown(error_markdown, QTextDocument.MarkdownDialectGitHub)
            
            self.meeting_notes_edit.setVisible(True)
            self.notes_have_been_edited = False
            self.status_bar.showMessage(f"Meeting notes not generated. No API key for {provider.title()}.", 3000)
            return
        reply = QMessageBox.question(self, "Regenerate Notes", "Regenerating will overwrite any unsaved edits. Continue?", QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        spinner = MeetingNotesProgressDialog(self)
        spinner.show()
        backend = get_llm_backend(provider, api_key=api_key, model=model)
        worker = MeetingNotesWorker(backend, transcript)
        def on_success(notes):
            try:
                import markdown2
                html = markdown2.markdown(notes)
                import re
                html = re.sub(r'<\/?(html|body)[^>]*>', '', html, flags=re.IGNORECASE)
                from nojoin.utils.theme_utils import wrap_html_body
                theme = config_manager.get("theme", "dark")
                themed_html = wrap_html_body(html, theme)
                self.meeting_notes_edit.setHtml(themed_html)
            except Exception:
                self.meeting_notes_edit.setPlainText(notes)
            self.meeting_notes_edit.setVisible(True)
            self.notes_have_been_edited = False
            self.status_bar.showMessage("Meeting notes generated.", 3000)
            db_ops.add_meeting_notes(recording_id, provider, model, notes)
            if spinner.isVisible():
                spinner.close()
        def on_error(error):
            logger.error(f"Failed to regenerate meeting notes: {error}")
            placeholder = (f"Meeting notes cannot be generated right now.\nPlease check your {provider.title()} API key in settings or view the raw diarized transcript via the context menu.")
            self.meeting_notes_edit.setPlainText(placeholder)
            self.meeting_notes_edit.setVisible(True)
            self.notes_have_been_edited = False
            self.status_bar.showMessage("Failed to generate meeting notes.", 3000)
            if spinner.isVisible():
                spinner.close()
        def cleanup_worker():
            self._meeting_notes_worker = None
            if spinner.isVisible():
                spinner.close()
        worker.success.connect(on_success)
        worker.error.connect(on_error)
        worker.finished.connect(cleanup_worker)
        worker.start()
        self._meeting_notes_worker = worker

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
        self._load_recordings_with_preserved_selection()

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
            self._clear_meeting_details() # Use the centralized clearing method
            # self.currently_selected_recording_id = None # Already set in _clear_meeting_details
            # self.meeting_context_display.clear() # Handled by _clear_meeting_details
            # self._update_center_panel_content() # Handled by _clear_meeting_details
            # # --- Clear chat display and history on no selection --- # Handled by _clear_meeting_details
            # self.current_chat_history = [] # Handled by _clear_meeting_details
            # if hasattr(self, 'chat_display_area'): # Handled by _clear_meeting_details
            #     self.chat_display_area.clear() # Handled by _clear_meeting_details
            # # --- Disable Clear Chat button when no meeting selected --- # Handled by _clear_meeting_details
            # if hasattr(self, 'clear_chat_button'): # Handled by _clear_meeting_details
            #     self.clear_chat_button.setEnabled(False) # Handled by _clear_meeting_details
            return
        item = selected_items[0]
        recording_id = item.data(Qt.UserRole)
        self.currently_selected_recording_id = recording_id # Set the new selected ID
        recording_data = db_ops.get_recording_by_id(recording_id)
        if not recording_data:
            self._clear_meeting_details() # Use the centralized clearing method if data fetch fails
            # self.currently_selected_recording_id = None # Reset if data fetch fails - handled by _clear_meeting_details
            # self.meeting_context_display.clear() # Handled by _clear_meeting_details
            # self._update_center_panel_content() # Handles notes/transcript area - Handled by _clear_meeting_details
            # # --- Clear chat display and history on invalid selection --- # Handled by _clear_meeting_details
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
        # Update editable meeting name
        if hasattr(self, 'editable_meeting_name'):
            self.editable_meeting_name.set_meeting_data(recording_id, meeting_title)
        
        # Update metadata display
        if hasattr(self, 'meeting_metadata_display'):
            metadata_text = ""
            if day_str or date_str:
                metadata_text += f"{day_str}{', ' if day_str and date_str else ''}{date_str}"
            if time_str:
                if metadata_text:
                    metadata_text += "\n"
                metadata_text += f"{time_str} {tz_str if tz_str else ''}".strip()
            self.meeting_metadata_display.setText(metadata_text)
        
        # --- Meeting Notes: Load using setMarkdown and apply theme ---
        if hasattr(self, 'meeting_notes_edit'):
            doc = self.meeting_notes_edit.document()
            
            # Apply current theme's stylesheet to the document
            theme_palette = THEME_PALETTE.get(self.current_theme, THEME_PALETTE["dark"])
            text_color = theme_palette['html_text']
            css = f"""
                body {{ color: {text_color}; background-color: transparent; }}
                p {{ color: {text_color}; }}
                h1, h2, h3, h4, h5, h6 {{ color: {text_color}; }}
                li {{ color: {text_color}; }}
                span, div, strong, em, u, s, sub, sup, font, blockquote, code, pre {{ 
                    color: {text_color} !important; 
                    background-color: transparent !important;
                }}
                a {{ color: {theme_palette['accent']}; }}
                a:visited {{ color: {theme_palette['accent2']}; }}
            """
            doc.setDefaultStyleSheet(css)
            base_font = QFont("Segoe UI", get_notes_font_size())
            doc.setDefaultFont(base_font)

            notes_entry = db_ops.get_meeting_notes_for_recording(recording_id)
            if notes_entry and notes_entry['notes']:
                # Assuming notes are stored as Markdown
                doc.setMarkdown(notes_entry['notes'], QTextDocument.MarkdownDialectGitHub)
            else:
                # Set placeholder or clear if no notes.
                self._set_placeholder_content("No meeting notes available. Right-click the recording to re-transcribe or generate notes.")
            
            self._notes_last_saved_content = doc.toMarkdown(QTextDocument.MarkdownDialectGitHub) # For autosave comparison
            self.notes_have_been_edited = False # Reset edit flag

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
        add_label_btn = QPushButton("+ Label")
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
            self.transcribe_button.setToolTip("This meeting is currently being processed.")
        elif not audio_exists:
            self.transcribe_button.setEnabled(False)
            self.transcribe_button.setToolTip("Audio file for this meeting is missing.")
        else:
            self.transcribe_button.setEnabled(True)
            self.transcribe_button.setToolTip("Re-transcribe the selected meeting")
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
        # --- New: Apply theme to search bar widget ---
        if hasattr(self, 'search_bar_widget'):
            from nojoin.utils.theme_utils import get_border_color
            border_color = get_border_color(self.current_theme)
            self.search_bar_widget.set_theme(border_color)
        # --- Apply theme to audio warning banner ---
        if hasattr(self, 'audio_warning_banner'):
            apply_theme_to_widget(self.audio_warning_banner, self.current_theme)
        # Update slider style for theme
        if hasattr(self, 'seek_slider') and hasattr(self, 'volume_slider'):
            if self.current_theme == "dark":
                slider_groove = "#444444"
                slider_handle = "#ffb74d"
            else:
                slider_groove = "#cccccc"
                slider_handle = "#007aff"
            slider_qss = f"""
            QSlider::groove:horizontal {{
                border: 1px solid {slider_groove};
                height: 4px;
                background: {slider_groove};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {slider_handle};
                border: 1px solid {slider_handle};
                width: 14px;
                border-radius: 7px;
            }}
            """
            self.seek_slider.setStyleSheet(slider_qss)
            self.volume_slider.setStyleSheet(slider_qss)

    def open_settings_dialog(self):
        if not hasattr(self, 'settings_dialog') or not self.settings_dialog:
            self.settings_dialog = SettingsDialog(self)
            self.settings_dialog.settings_saved.connect(self._handle_settings_saved)

        # Apply current main window palette to ensure consistency
        self.settings_dialog.setPalette(self.palette())
        self.settings_dialog.exec()

    def _handle_settings_saved(self):
        # Reload theme from config and apply it with current font scaling
        new_theme = config_manager.get("theme", "dark")
        font_scale = self.ui_scale_manager.get_font_scale_factor()
        self.apply_theme(new_theme, font_scale)
        # Update center panel content to apply new font size
        # Optional: Show confirmation or perform other updates if needed
        self.status_bar.showMessage("Settings saved successfully.", 3000)
    
    def _load_recordings_with_preserved_selection(self, target_recording_id: str = None):
        """Helper method to refresh meetings list while preserving selection."""
        # Remember the currently selected recording if not provided
        if target_recording_id is None:
            selected_items = self.meetings_list_widget.selectedItems()
            target_recording_id = selected_items[0].data(Qt.UserRole) if selected_items else None
        
        # Refresh the meetings list
        self.load_recordings()
        
        # Re-select the same meeting after refresh
        if target_recording_id:
            for i in range(self.meetings_list_widget.count()):
                item = self.meetings_list_widget.item(i)
                if item.data(Qt.UserRole) == target_recording_id:
                    self.meetings_list_widget.setCurrentItem(item)
                    break

    def _on_meeting_name_changed(self, recording_id: str, new_name: str):
        """Handle when the meeting name is changed via the editable widget."""
        # Refresh the meetings list and preserve selection
        self._load_recordings_with_preserved_selection(recording_id)
        # Show success message
        self.status_bar.showMessage(f"Meeting renamed to: {new_name}", 3000)

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
        # Don't regenerate meeting notes here - will be done when user explicitly requests it
        self.status_bar.showMessage("Speaker deleted and transcript updated", 3000)
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
        # Don't regenerate meeting notes here - will be done when user explicitly requests it
        self.status_bar.showMessage("Speakers merged and transcript updated", 3000)
        self.load_recordings()
        self.load_transcript(recording_id)
        QMessageBox.information(self, "Merge Speakers", "Speakers merged successfully.")

    def load_transcript(self, recording_id):
        """Load and display the transcript for the given recording ID, mapping diarization labels to current speaker names."""
        from nojoin.db import database as db_ops
        from nojoin.utils.transcript_store import TranscriptStore
        import re
        recording_data = db_ops.get_recording_by_id(recording_id)
        if not recording_data:
            return "" # Return empty string or handle appropriately if transcript content is needed elsewhere
        
        display_text_lines = [] # Changed to list of lines for easier HTML construction later
        
        # Build diarization label -> name mapping
        speakers = db_ops.get_speakers_for_recording(recording_id)
        label_to_name = {s['diarization_label']: s['name'] for s in speakers if s.get('diarization_label')}

        # First try to get diarized transcript from database
        diarized_transcript_text = TranscriptStore.get(recording_id, "diarized")
        if diarized_transcript_text:
            try:
                for line in diarized_transcript_text.split('\n'):
                    if not line.strip():
                        continue
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
                logger.error(f"Failed to format diarized transcript from database for recording {recording_id}: {e}")
                return wrap_html_body(f"<p>Error loading diarized transcript.</p>", config_manager.get("theme", "dark"))
        
        # Fallback: Try to get raw transcript from database
        raw_transcript_text = TranscriptStore.get(recording_id, "raw")
        if raw_transcript_text:
            try:
                import html as html_converter
                escaped_content = html_converter.escape(raw_transcript_text)
                return wrap_html_body(f"<pre>{escaped_content}</pre>", config_manager.get("theme", "dark"))
            except Exception as e:
                logger.error(f"Failed to format raw transcript from database for recording {recording_id}: {e}")
                return wrap_html_body(f"<p>Error loading raw transcript.</p>", config_manager.get("theme", "dark"))
        
        # Legacy fallback: Try to read from files if database doesn't have transcript text
        raw_transcript_path = recording_data.get("raw_transcript_path")
        diarized_transcript_path = recording_data.get("diarized_transcript_path")
        abs_raw_transcript_path = from_project_relative_path(raw_transcript_path) if raw_transcript_path else None
        abs_diarized_transcript_path = from_project_relative_path(diarized_transcript_path) if diarized_transcript_path else None

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
            for idx, result in enumerate(results):
                if result.success:
                    # Use the original file path for the name
                    orig_name = os.path.splitext(os.path.basename(selected_files[idx]))[0]
                    recording_name = orig_name
                    new_id = db_ops.add_recording(
                        name=recording_name,
                        audio_path=result.rel_path,
                        duration=result.duration,
                        size_bytes=result.size,
                        format=result.format or "MP3",
                        start_time=None,
                        end_time=None
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
        logger.info(f"_handle_processing_completion called for ID: {recording_id}. Error: {error_message}")
        recording_data_check = db_ops.get_recording_by_id(recording_id)
        logger.info(f"Data for {recording_id} in _handle_processing_completion (before dialog close): {dict(recording_data_check) if recording_data_check else 'None'}")
        logger.info(f"Diarized path for {recording_id} in _handle_processing_completion: {recording_data_check.get('diarized_transcript_path') if recording_data_check else 'N/A'}")

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
        # Show meeting notes progress dialog here
        from .meeting_notes_progress_dialog import MeetingNotesProgressDialog
        notes_dialog = MeetingNotesProgressDialog(self)
        notes_dialog.show()
        QApplication.processEvents()
        try:
            self._prompt_relabel_speakers(recording_id)
        finally:
            notes_dialog.close()

    def get_meetings_list_qss(self):
        return """
        QListWidget::item {
            background: transparent;
            border: 2px;
            margin: 1px 0px;
            padding: 0px;
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
        # Set selected property for QSS
        for i in range(self.meetings_list_widget.count()):
            item = self.meetings_list_widget.item(i)
            widget = self.meetings_list_widget.itemWidget(item)
            is_selected = item.isSelected()
            if widget:
                MeetingListItemWidget.set_selected_state(widget, is_selected)
        self.meetings_list_widget.itemSelectionChanged.connect(self._update_meeting_card_selection_states)

    def _update_meeting_card_selection_states(self):
        for i in range(self.meetings_list_widget.count()):
            item = self.meetings_list_widget.item(i)
            widget = self.meetings_list_widget.itemWidget(item)
            is_selected = item.isSelected()
            if widget:
                MeetingListItemWidget.set_selected_state(widget, is_selected)

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
        
        # Get transcript from database first, then fallback to file
        from nojoin.utils.transcript_store import TranscriptStore
        diarized_transcript = TranscriptStore.get(recording_id, "diarized")
        
        if not diarized_transcript:
            # Fallback to file-based approach for legacy recordings
            diarized_transcript_path = rec.get("diarized_transcript_path")
            abs_diarized_transcript_path = from_project_relative_path(diarized_transcript_path) if diarized_transcript_path else None
            if abs_diarized_transcript_path and os.path.exists(abs_diarized_transcript_path):
                try:
                    with open(abs_diarized_transcript_path, 'r', encoding='utf-8') as f:
                        diarized_transcript = f.read()
                except Exception as e:
                    self.logger.error(f"Failed to read transcript file for chat: {e}")
                    diarized_transcript = None
        
        if not diarized_transcript:
            sys_html = '<div class="chat-message system"><i>No transcript available for chat.</i></div>'
            self.chat_display_area.append(sys_html)
            self.chat_input.setEnabled(True)
            self.chat_send_button.setVisible(True)
            if hasattr(self, 'chat_spinner'):
                self.chat_panel.layout().removeWidget(self.chat_spinner)
                self.chat_spinner.setParent(None)
            self._chat_request_in_progress = False
            return
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
        # The "View Diarized Transcript" action is removed as per new design.
        # User can toggle via the button in the notes/transcript panel.
        diarized_transcript_path = recording_data.get("diarized_transcript_path") # Still needed for other actions
        abs_diarized_transcript_path = from_project_relative_path(diarized_transcript_path) if diarized_transcript_path else None
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
        process_action = QAction("Re-transcribe", self)
        process_action.triggered.connect(lambda: self.process_selected_recording(recording_id, recording_data))
        current_status = recording_data.get('status', 'Unknown').lower()
        process_action.setEnabled(current_status != 'processing')
        context_menu.addAction(process_action)
        # Add Reset Status action for stuck 'processing' recordings
        if current_status == 'processing':
            reset_status_action = QAction("Reset Status to Error", self)
            def reset_status():
                db_ops.update_recording_status(recording_id, 'Error')
                self._load_recordings_with_preserved_selection(recording_id)
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
        dlg.regenerate_notes_requested.connect(lambda rec_id: self.on_regenerate_meeting_notes_clicked(rec_id, db_ops.get_recording_by_id(rec_id)))
        dlg.exec()

    def _on_participants_changed(self, recording_id: str):
        self._load_recordings_with_preserved_selection(recording_id)
        # Don't automatically regenerate notes here - this will be done only when user explicitly requests it

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

        iso_timestamp_now = datetime.now().isoformat()
        display_timestamp = datetime.fromisoformat(iso_timestamp_now).strftime("%d %b %H:%M")
        
        html = markdown2.markdown(response)
        ai_html = f'<div class="chat-message assistant"><b>Assistant</b> <span class="timestamp">{display_timestamp}</span><div class="content">{html}</div></div>'
        self.chat_display_area.append(ai_html)
        
        self.current_chat_history.append({"role": "model", "parts": [{"text": response}], "timestamp": iso_timestamp_now})
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
        logger.info(f"_prompt_relabel_speakers called for ID: {recording_id}")
        recording_data = db_ops.get_recording_by_id(recording_id)
        logger.info(f"Data for {recording_id} in _prompt_relabel_speakers: {dict(recording_data) if recording_data else 'None'}")
        logger.info(f"Diarized path for {recording_id} in _prompt_relabel_speakers: {recording_data.get('diarized_transcript_path') if recording_data else 'N/A'}")

        if not recording_data:
            QMessageBox.warning(self, "Relabel Speakers", "Recording not found in database. Cannot relabel speakers.")
            return
        
        # Track if notes were regenerated
        notes_regenerated = [False]  # Use list to allow mutation in lambda
        
        def on_regenerate_requested(rec_id: str): # Ensure rec_id type hint is str
            notes_regenerated[0] = True
            # Ensure rec_id is passed, not a potentially stale recording_data
            self._generate_meeting_notes_after_relabel(rec_id) 
        
        dlg = ParticipantsDialog(recording_id, recording_data, parent=self)
        dlg.participants_updated.connect(self._on_participants_changed)
        dlg.regenerate_notes_requested.connect(on_regenerate_requested)
        result = dlg.exec()
        
        # If dialog was accepted and notes weren't regenerated through the dialog, ask if they want to generate notes
        if result == QDialog.Accepted and not notes_regenerated[0]:
            reply = QMessageBox.question(
                self, 
                "Generate Meeting Notes",
                "Would you like to generate meeting notes for this recording?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if reply == QMessageBox.Yes:
                self._generate_meeting_notes_after_relabel(recording_id)

    def _generate_meeting_notes_after_relabel(self, recording_id):
        logger.info(f"Attempting to generate notes for recording_id: {recording_id}")
        rec = db_ops.get_recording_by_id(recording_id)

        if rec is None:
            logger.error(f"CRITICAL: Recording with ID {recording_id} not found in database within _generate_meeting_notes_after_relabel. Cannot generate notes.")
            QMessageBox.critical(self, "Notes Generation Error", f"Failed to retrieve data for recording ID {recording_id}. Meeting notes cannot be generated.")
            return
        
        logger.info(f"Found recording data for ID {recording_id}: {dict(rec) if rec else 'None'}")

        # Get transcript from database first, then fallback to file
        from nojoin.utils.transcript_store import TranscriptStore
        transcript_text = TranscriptStore.get(recording_id, "diarized")
        
        if not transcript_text:
            # Fallback to file-based approach for legacy recordings
            logger.info(f"No transcript found in database for recording {recording_id}, trying file fallback...")
            diarized_transcript_path = rec.get("diarized_transcript_path")
            if diarized_transcript_path:
                abs_diarized_transcript_path = from_project_relative_path(diarized_transcript_path)
                if abs_diarized_transcript_path and os.path.exists(abs_diarized_transcript_path):
                    try:
                        with open(abs_diarized_transcript_path, 'r', encoding='utf-8') as f:
                            transcript_text = f.read()
                        logger.info(f"Successfully read transcript from file for recording {recording_id}")
                    except Exception as e:
                        logger.error(f"Failed to read transcript file for recording {recording_id}: {e}")
                        transcript_text = None
        
        if not transcript_text:
            logger.error(f"CRITICAL: No diarized transcript found for recording ID {recording_id} (Name: {rec.get('name')}) in _generate_meeting_notes_after_relabel.")
            QMessageBox.critical(self, "Notes Generation Error", f"Diarized transcript is missing for recording '{rec.get('name')}'. Meeting notes cannot be generated.")
            return

        logger.info(f"Proceeding with notes generation for ID {recording_id}. Transcript found: {len(transcript_text)} characters")
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
        notes_dialog = MeetingNotesProgressDialog(self)
        notes_dialog.show()
        worker = MeetingNotesWorker(backend, transcript_text, label_to_name)
        def on_success(notes):
            try:
                import markdown2
                html = markdown2.markdown(notes)
                import re
                html = re.sub(r'<\/?(html|body)[^>]*>', '', html, flags=re.IGNORECASE)
                from nojoin.utils.theme_utils import wrap_html_body
                theme = config_manager.get("theme", "dark")
                themed_html = wrap_html_body(html, theme)
                self.meeting_notes_edit.setHtml(themed_html)
            except Exception:
                self.meeting_notes_edit.setPlainText(notes)
            self.meeting_notes_edit.setVisible(True)
            self.notes_have_been_edited = False
            self.status_bar.showMessage("Meeting notes generated.", 3000)
            db_ops.add_meeting_notes(recording_id, llm_provider, model, notes)
            notes_dialog.close()
            QMessageBox.information(self, "Meeting Notes", "Meeting notes have been generated.")
        def on_error(error):
            logger.error(f"Failed to generate meeting notes: {error}")
            placeholder = (f"Meeting notes cannot be generated right now.\nPlease check your {llm_provider.title()} API key in settings or view the raw diarized transcript via the context menu.")
            self.meeting_notes_edit.setPlainText(placeholder)
            self.meeting_notes_edit.setVisible(True)
            self.notes_have_been_edited = False
            self.status_bar.showMessage("Failed to generate meeting notes.", 3000)
            notes_dialog.close()
            QMessageBox.warning(self, "Meeting Notes", f"Failed to generate meeting notes: {error}")
        def cleanup_worker():
            self._meeting_notes_worker = None
            if notes_dialog.isVisible():
                notes_dialog.close()
        worker.success.connect(on_success)
        worker.error.connect(on_error)
        worker.finished.connect(cleanup_worker)
        worker.start()
        self._meeting_notes_worker = worker
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

    def _autosave_content(self):
        """Autosave meeting notes or transcript content based on current view mode."""
        selected_items = self.meetings_list_widget.selectedItems()
        if not selected_items:
            return
        recording_id = selected_items[0].data(Qt.UserRole)
        
        if not hasattr(self, 'meeting_notes_edit'):
            return

        doc = self.meeting_notes_edit.document()

        if self.center_panel_view_mode == "notes":
            # Save meeting notes as Markdown
            markdown_content = doc.toMarkdown(QTextDocument.MarkdownDialectGitHub)
            
            # Only save if changed
            if markdown_content != self._notes_last_saved_content:
                try:
                    notes_entry = db_ops.get_meeting_notes_for_recording(recording_id)
                    if notes_entry:
                        db_ops.update_meeting_notes(notes_entry['id'], markdown_content)
                    else:
                        db_ops.add_meeting_notes(recording_id, 'manual_markdown', '', markdown_content)
                    
                    self._notes_last_saved_content = markdown_content
                    self.status_bar.showMessage("Meeting notes autosaved.", 1500)
                    self.notes_have_been_edited = False
                except Exception as e:
                    self.logger.error(f"Failed to autosave notes: {e}", exc_info=True)
                    self.status_bar.showMessage(f"Failed to autosave notes: {e}", 3000)
                    
        elif self.center_panel_view_mode == "transcript":
            # Save transcript as plain text to the database
            # Convert HTML back to plain text format suitable for transcript files
            plain_content = doc.toPlainText()
            
            # Only save if changed
            if hasattr(self, '_transcript_last_saved_content') and plain_content != self._transcript_last_saved_content:
                try:
                    from nojoin.utils.transcript_store import TranscriptStore
                    
                    # Save the plain text content back to the database
                    success = TranscriptStore.set(recording_id, plain_content, "diarized")
                    if success:
                        self._transcript_last_saved_content = plain_content
                        self.status_bar.showMessage("Transcript autosaved.", 1500)
                        self.logger.info(f"Transcript autosaved to database for recording {recording_id}")
                    else:
                        self.logger.warning(f"Failed to autosave transcript to database for recording {recording_id}")
                        self.status_bar.showMessage("Failed to autosave transcript to database.", 3000)
                        
                except Exception as e:
                    self.logger.error(f"Failed to autosave transcript: {e}", exc_info=True)
                    self.status_bar.showMessage(f"Failed to autosave transcript: {e}", 3000)

    # Connect timer timeout to autosave
    def _setup_notes_autosave(self):
        self._notes_autosave_timer.timeout.connect(self._autosave_content)

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

    # --- New methods for toggling view and updating content ---
    def _update_center_panel_content(self):
        # self.logger.info(f"_update_center_panel_content called. Current mode: {self.center_panel_view_mode}") # Log mode # REMOVED DEBUG_PRINT
        selected_items = self.meetings_list_widget.selectedItems()
        
        if not hasattr(self, 'meeting_notes_edit'): # Should always exist if UI is setup
            self.logger.error("_update_center_panel_content called but meeting_notes_edit does not exist.")
            return

        doc = self.meeting_notes_edit.document()
        theme_name = self.current_theme 
        
        if not hasattr(self, 'current_theme') or not self.current_theme:
             self.logger.warning("current_theme not set in _update_center_panel_content. Defaulting to dark.")
             theme_name = "dark" 

        theme_palette = THEME_PALETTE.get(theme_name, THEME_PALETTE["dark"])
        text_color = theme_palette['html_text']
        
        css = f"""
            body {{ color: {text_color}; background-color: transparent; }}
            p {{ color: {text_color}; }}
            h1, h2, h3, h4, h5, h6 {{ color: {text_color}; }}
            li {{ color: {text_color}; }}
            span, div, strong, em, u, s, sub, sup, font, blockquote, code, pre {{ 
                color: {text_color} !important; 
                background-color: transparent !important;
            }}
            a {{ color: {theme_palette['accent']}; }}
            a:visited {{ color: {theme_palette['accent2']}; }}
        """
        doc.setDefaultStyleSheet(css)
        base_font = QFont("Segoe UI", get_notes_font_size())
        doc.setDefaultFont(base_font)

        if not selected_items:
            if self.view_toggle_button:
                self.view_toggle_button.setText("View Transcript")
            if self.notes_undo_button: self.notes_undo_button.setVisible(True)
            if self.notes_redo_button: self.notes_redo_button.setVisible(True)
            # Set a generic placeholder if nothing is selected
            self._set_placeholder_content("Select a meeting to view notes or transcript.")
            return

        item = selected_items[0]
        recording_id = item.data(Qt.UserRole)
        recording_data = db_ops.get_recording_by_id(recording_id)

        if not recording_data:
            # Set a specific placeholder if recording data is missing
            self._set_placeholder_content("Error loading meeting data.")
            return
        
        if self.center_panel_view_mode == "notes":
            if self.view_toggle_button:
                self.view_toggle_button.setText("View Transcript")
            self.meeting_notes_edit.setReadOnly(False)
            if self.notes_undo_button: self.notes_undo_button.setVisible(True)
            if self.notes_redo_button: self.notes_redo_button.setVisible(True)
            
            doc.clear() # Explicitly clear the document before setting new markdown content
            notes_entry = db_ops.get_meeting_notes_for_recording(recording_id)
            if notes_entry and notes_entry['notes']:
                doc.setMarkdown(notes_entry['notes'], QTextDocument.MarkdownDialectGitHub)
                self._is_placeholder_content = False
                self.meeting_notes_edit.setReadOnly(False)  # Allow editing of real content
                self._notes_last_saved_content = doc.toMarkdown(QTextDocument.MarkdownDialectGitHub) # For autosave
            else:
                self._set_placeholder_content("No meeting notes available. Right-click the recording to generate notes, or switch to transcript view.")
                self._notes_last_saved_content = ""
            self.notes_have_been_edited = False # Reset edit flag
        
        elif self.center_panel_view_mode == "transcript":
            self.logger.info(f"Loading transcript for recording_id: {recording_id}") # Log transcript load
            if self.view_toggle_button:
                self.view_toggle_button.setText("View Meeting Notes")
            # Remove setReadOnly(True) to allow find/replace operations in transcript view
            # self.meeting_notes_edit.setReadOnly(True)  # Commented out to enable editing
            if self.notes_undo_button: self.notes_undo_button.setVisible(False)
            if self.notes_redo_button: self.notes_redo_button.setVisible(False)
            
            # load_transcript is expected to return HTML string already wrapped by wrap_html_body
            transcript_html_content = self.load_transcript(recording_id) 
            doc.setHtml(transcript_html_content) 
            
            # Enable autosave for transcript changes
            self._transcript_last_saved_content = doc.toPlainText()  # Track content for autosave
            self.notes_have_been_edited = False  # Reset edit flag

        self.meeting_notes_edit.setVisible(True)
        self.logger.info(f"_update_center_panel_content finished. Mode: {self.center_panel_view_mode}, Button text: {self.view_toggle_button.text() if self.view_toggle_button else 'N/A'}")

    def _toggle_center_panel_view(self):
        self.logger.info(f"_toggle_center_panel_view: CALLED. Current mode before toggle: {self.center_panel_view_mode}")
        if self.center_panel_view_mode == "notes":
            self.center_panel_view_mode = "transcript"
        else:
            self.center_panel_view_mode = "notes"
        self.logger.info(f"_toggle_center_panel_view: Mode AFTER toggle: {self.center_panel_view_mode}")
        self._update_center_panel_content() # This will refresh the view
        
    # --- Playback Controller Slots ---
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

    def _setup_notes_autosave(self):
        self._notes_autosave_timer.timeout.connect(self._autosave_content)

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

    def _open_global_speakers_dialog(self):
        from .global_speakers_dialog import GlobalSpeakersManagementDialog # Lazy import
        # Check if an instance already exists and is visible (optional, good for non-modal)
        # For a modal dialog, creating a new one each time is fine.
        dialog = GlobalSpeakersManagementDialog(self)
        dialog.global_speakers_updated.connect(self._handle_global_speakers_updated)
        dialog.exec() # Modal execution

    def _handle_global_speakers_updated(self):
        # This is where we would ideally notify any open ParticipantsDialog instances.
        # For now, this signal exists. If ParticipantsDialog is made aware of this signal
        # or MainWindow tracks ParticipantsDialog instances, it can refresh its cache.
        logger.info("Global speakers library was updated. ParticipantsDialog instances (if open and connected) should refresh their caches.")
        # A simple way to achieve this if ParticipantsDialog is always a child of MainWindow:
        for child_widget in self.findChildren(QDialog): # Potentially too broad
            if hasattr(child_widget, '_load_global_speakers_cache') and callable(getattr(child_widget, '_load_global_speakers_cache')):
                if child_widget.isVisible(): # Only if it's an active dialog
                    try:
                        # Verify it is indeed a ParticipantsDialog to be safer
                        from .participants_dialog import ParticipantsDialog
                        if isinstance(child_widget, ParticipantsDialog):
                            logger.info(f"Found open ParticipantsDialog, requesting cache reload: {child_widget}")
                            child_widget._load_global_speakers_cache()                            
                            # Also need to update the completers in that dialog
                            if hasattr(child_widget, 'speaker_widgets') and child_widget.speaker_widgets:
                                for sp_id, widgets_dict in child_widget.speaker_widgets.items():
                                    name_edit_widget = widgets_dict.get('name_edit')
                                    if name_edit_widget and name_edit_widget.completer():
                                        name_edit_widget.completer().model().setStringList(child_widget._global_speakers_cache)
                                        logger.debug(f"Refreshed completer for speaker_id {sp_id} in ParticipantsDialog.")
                    except ImportError:
                        logger.warning("Could not import ParticipantsDialog for type checking in _handle_global_speakers_updated.")
                    except Exception as e:
                        logger.error(f"Error trying to refresh ParticipantsDialog cache: {e}")

    def _clear_meeting_details(self):
        """Clears all UI elements related to a selected meeting."""
        logger.info("_clear_meeting_details called")
        self.currently_selected_recording_id = None

        # Clear context display
        if hasattr(self, 'editable_meeting_name'):
            self.editable_meeting_name.set_meeting_data("", "")
        if hasattr(self, 'meeting_metadata_display'):
            self.meeting_metadata_display.clear()

        # Clear tags display
        if hasattr(self, 'meeting_tags_layout'):
            while self.meeting_tags_layout.count():
                item = self.meeting_tags_layout.takeAt(0)
                widget = item.widget()
                if widget:
                    widget.deleteLater()
        
        # Clear/Reset notes/transcript panel (this will show placeholders)
        # _update_center_panel_content handles this based on currently_selected_recording_id being None
        self._update_center_panel_content()

        # Clear chat history and display
        self.current_chat_history = []
        if hasattr(self, 'chat_display_area'):
            self.chat_display_area.clear()
        if hasattr(self, 'chat_input'): # Ensure chat_input exists
            self.chat_input.clear()
            self.chat_input.setEnabled(False)
        if hasattr(self, 'chat_send_button'):
            self.chat_send_button.setEnabled(False)
        if hasattr(self, 'clear_chat_button'):
            self.clear_chat_button.setEnabled(False)

        # Stop playback and reset playback controls
        if hasattr(self, 'playback_controller'):
            self.playback_controller.stop() # This should emit signals that reset buttons
        
        # Explicitly reset playback UI elements for robustness
        self.selected_audio_path = None
        self._playback_duration = 0.0
        if hasattr(self, 'play_button'): self.play_button.setEnabled(False)
        if hasattr(self, 'pause_button'): self.pause_button.setEnabled(False)
        if hasattr(self, 'stop_button'): self.stop_button.setEnabled(False)
        if hasattr(self, 'seek_slider'): 
            self.seek_slider.setEnabled(False)
            self.seek_slider.setValue(0)
        if hasattr(self, 'seek_time_label'): self.update_seek_time_label(0,0)
        
        # Disable transcribe button
        if hasattr(self, 'transcribe_button'):
            self.transcribe_button.setEnabled(False)
            self.transcribe_button.setToolTip("Select a recording to transcribe")

        logger.info("_clear_meeting_details finished")

    def _open_find_replace_dialog(self):
        """Open the find and replace dialog."""
        current_theme = config_manager.get("theme", "dark")
        
        # Use meeting_notes_edit for both notes and transcript views since it's the same widget
        text_edit = self.meeting_notes_edit if hasattr(self, 'meeting_notes_edit') else None
        
        # Get current recording ID
        selected_items = self.meetings_list_widget.selectedItems()
        recording_id = selected_items[0].data(Qt.UserRole) if selected_items else None

        dialog = FindReplaceDialog(
            parent=self,
            text_edit=text_edit,
            theme_name=current_theme,
            recording_id=recording_id
        )
        
        # Connect to bulk operation completion signal to refresh current view
        dialog.bulk_operation_completed.connect(self._refresh_current_meeting_view)
        
        # Pre-populate search text if there's a selection
        if text_edit and text_edit.textCursor().hasSelection():
            selected_text = text_edit.textCursor().selectedText()
            if selected_text and len(selected_text) < 100:  # Only for reasonable selections
                dialog.set_search_text(selected_text)
        
        # Show the dialog
        dialog.exec()
    
    def _check_for_updates_on_startup(self):
        """Check for updates on application startup."""
        try:
            check_for_updates_on_startup(self)
        except Exception as e:
            logger.error(f"Error checking for updates on startup: {e}")
    
    def _check_first_run_model_download(self):
        """Check if we should prompt user to download default Whisper model on first run."""
        try:
            from nojoin.utils.model_utils import should_prompt_for_first_run_download, check_default_model_availability
            from nojoin.ui.model_download_dialog import ModelDownloadDialog
            
            if should_prompt_for_first_run_download():
                is_available, model_size = check_default_model_availability()
                
                reply = QMessageBox.question(
                    self,
                    "Download Whisper Model",
                    f"The default Whisper model '{model_size}' is not available locally.\n\n"
                    f"Would you like to download it now? This will enable audio transcription.\n\n"
                    f"You can also download it later during your first transcription.",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes
                )
                
                if reply == QMessageBox.Yes:
                    device = config_manager.get("processing_device", "cpu")
                    dialog = ModelDownloadDialog(model_size, device, self)
                    dialog.start_download()
                    dialog.exec()
                    
                    if not dialog.was_cancelled():
                        logger.info(f"First-run download of model '{model_size}' completed successfully")
                        QMessageBox.information(
                            self,
                            "Model Downloaded",
                            f"Whisper model '{model_size}' has been downloaded successfully!\n\n"
                            f"You can now transcribe audio recordings."
                        )
                    else:
                        logger.info(f"First-run download of model '{model_size}' was cancelled by user")
                        
        except Exception as e:
            logger.error(f"Error checking for first-run model download: {e}", exc_info=True)
        
    def _refresh_current_meeting_view(self):
        """Refresh the currently displayed meeting notes/transcript after bulk operations."""
        # If a meeting is selected and we're viewing notes, reload them from database
        selected_items = self.meetings_list_widget.selectedItems()
        if selected_items and self.center_panel_view_mode == "notes":
            recording_id = selected_items[0].data(Qt.UserRole)
            
            # Reload meeting notes from database
            notes_entry = db_ops.get_meeting_notes_for_recording(recording_id)
            if notes_entry and hasattr(self, 'meeting_notes_edit'):
                doc = self.meeting_notes_edit.document()
                doc.setMarkdown(notes_entry['notes'])
                self._notes_last_saved_content = notes_entry['notes']
                self.notes_have_been_edited = False

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

class MeetingListItemWidget(QFrame):
    def __init__(self, recording_data: dict, theme_name: str, parent=None):
        super().__init__(parent)
        self.recording_data = recording_data
        self.theme_name = theme_name
        self.setObjectName("MeetingListItemCard") 
        self._init_ui()

    def _init_ui(self):
        self.card_label = QLabel()
        self.card_label.setTextFormat(Qt.RichText)
        self.card_label.setWordWrap(True)
        self.card_label.setObjectName("MeetingCardContentLabel") # Specific name for the label
        self.card_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.card_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 1, 1, 10)
        layout.setSpacing(0)
        layout.addWidget(self.card_label)
        self.setLayout(layout)
        self.update_content(self.recording_data, self.theme_name)

    def update_content(self, recording_data: dict, theme_name: str):
        import datetime
        import html
        from nojoin.utils.config_manager import config_manager
        from nojoin.utils.theme_utils import THEME_PALETTE, FONT_HIERARCHY # Import THEME_PALETTE & FONT_HIERARCHY
        self.recording_data = recording_data
        self.theme_name = theme_name
        # --- Get Theme Specific Palette ---
        current_theme_name = config_manager.get("theme", "dark")
        palette = THEME_PALETTE[current_theme_name]
        accent_color = palette['accent'] # Already used for border
        card_background_color = palette['panel_bg']
        title_color = palette['accent'] # Title uses accent color
        metadata_color = palette['muted_text']
        chip_text_color = palette['chip_text']
        chip_border_color = palette['chip_border']
        # --- Apply QSS to the QFrame (self) for border, background, margin ---
        qss = f"""
            QFrame#MeetingListItemCard {{
                border-radius: 18px;
                border: 2.5px solid {accent_color};
                background-color: {card_background_color};
                margin-bottom: 5px;
            }}
            QLabel#MeetingCardContentLabel {{
                background-color: transparent; 
                border: none; 
                padding: 0px;
                margin: 0px;
            }}
        """
        self.setStyleSheet(qss)
        # --- Title ---
        meeting_title = html.escape(self.recording_data.get("name", "Untitled Meeting"))
        # --- Date/Time/Duration ---
        start_time_str = self.recording_data.get("start_time")
        end_time_str = self.recording_data.get("end_time")
        duration_seconds = self.recording_data.get("duration_seconds") or self.recording_data.get("duration", 0)
        created_at = self.recording_data.get("created_at")
        date_str = "?"
        time_str = "--:--"
        duration_min = 0
        tooltip_date_time = ""
        try:
            dt = None
            if start_time_str:
                dt = datetime.datetime.fromisoformat(start_time_str)
            elif created_at:
                try:
                    dt = datetime.datetime.fromisoformat(created_at)
                except Exception:
                    try:
                        dt = datetime.datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        dt = None
            if dt:
                date_str = dt.strftime("%d %b %Y")
                time_str = dt.strftime("%H:%M")
                tooltip_date_time = dt.strftime("%Y-%m-%d %H:%M:%S")
            if start_time_str and end_time_str:
                start_dt = datetime.datetime.fromisoformat(start_time_str)
                end_dt = datetime.datetime.fromisoformat(end_time_str)
                duration_min = int((end_dt - start_dt).total_seconds() // 60)
            else:
                duration_min = int(duration_seconds // 60)
        except Exception:
            duration_min = int(duration_seconds // 60)
        duration_str = f"{duration_min}mins"
        # --- Participants ---
        participants = self.recording_data.get("participants")
        if participants is None:
            try:
                from nojoin.db import database as db_ops
                speakers = db_ops.get_speakers_for_recording(self.recording_data.get("id"))
                logger.debug(f"MeetingListItemWidget update_content for ID {self.recording_data.get('id')}: Fetched speakers from DB: {speakers}") # ADDED LOGGING
                participants = [s.get("name") or s.get("diarization_label") for s in speakers if s.get("name") or s.get("diarization_label")]
            except Exception as e: # ADDED EXCEPTION LOGGING
                logger.error(f"MeetingListItemWidget update_content for ID {self.recording_data.get('id')}: Error fetching speakers: {e}", exc_info=True)
                participants = []
        else: # ADDED LOGGING FOR CACHED PARTICIPANTS
            logger.debug(f"MeetingListItemWidget update_content for ID {self.recording_data.get('id')}: Using pre-loaded participants: {participants}")
        
        # Join participant names with a comma and space for display on the card
        escaped_participants = [html.escape(p) for p in participants if p] # Ensure p is not None or empty
        if escaped_participants:
            # participants_html = "".join([f'<span style="{chip_style}">{p}</span>' for p in escaped_participants]) # Old chip display
            participants_display_string = ", ".join(escaped_participants)
            # Display as plain text, styled like other metadata
            participants_html = f'<span style="color:{metadata_color}; font-size:13px;">{participants_display_string}</span>'
        else:
            participants_html = "" # No participants to display

        # --- Status as colored text ---
        status = self.recording_data.get("status", "").capitalize()
        # Status specific colors remain, as they are semantic (green for processed, red for error etc)
        status_semantic_color = {
            "Processing": "#ff9800", # Orange
            "Processed": "#4caf50",  # Green
            "Error": "#f44336",     # Red
            "Cancelled": "#bdbdbd"  # Grey
        }.get(status, palette['muted_text']) # Default to muted if status is unknown
        status_html = f'<span style="color:{status_semantic_color}; font-weight:bold; margin-left:6px; background:transparent;">{status}</span>' if status else ""
        # --- Card HTML (for QLabel content, no outer div with border/bg) ---
        card_content_html = f'''
          <div style="font-size:1.15em; font-weight:bold; color:{title_color}; background:transparent;">{meeting_title}</div>
          <div style="color:{metadata_color}; margin-top:4px; font-size:13px; background:transparent;">
            <span title="{tooltip_date_time}" style="background:transparent;">🗓 {date_str}</span> &nbsp;|&nbsp;
            <span style="background:transparent;">⏰ {time_str}</span> &nbsp;|&nbsp;
            <span style="background:transparent;">{duration_str}</span> &nbsp;|&nbsp;
            {status_html} 
          </div>
          <div style="margin-top:7px; background:transparent;">{participants_html}</div>
        '''
        self.card_label.setText(card_content_html)

    @staticmethod
    def set_selected_state(widget, selected):
        widget.setProperty("selected", selected)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

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

class EditableMeetingName(QLineEdit):
    """Custom QLineEdit for editing meeting names with preserved formatting and auto-save."""
    
    name_changed = Signal(str, str)  # Signal emitted when name changes (recording_id, new_name)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.recording_id = None
        self.original_name = ""
        self.setReadOnly(False)
        self.setFrame(False)  # Remove border to blend with context
        self.setAttribute(Qt.WA_MacShowFocusRect, False)  # Remove focus ring on macOS
        
        # Connect signals for auto-save
        self.editingFinished.connect(self._on_editing_finished)
        self.returnPressed.connect(self._on_return_pressed)
        
        # Theme-based styling will be applied via QSS from theme_utils
    
    def set_meeting_data(self, recording_id: str, meeting_name: str):
        """Set the meeting data for this widget."""
        self.recording_id = recording_id
        self.original_name = meeting_name
        self.setText(meeting_name)
        self.setCursorPosition(0)  # Reset cursor to start
    
    def _on_editing_finished(self):
        """Handle when editing is finished (focus lost)."""
        if self.recording_id and self.text().strip() != self.original_name:
            self._save_name_change()
    
    def _on_return_pressed(self):
        """Handle when return/enter key is pressed."""
        if self.recording_id and self.text().strip() != self.original_name:
            self._save_name_change()
        self.clearFocus()  # Remove focus after saving
    
    def _save_name_change(self):
        """Save the name change to the database."""
        new_name = self.text().strip()
        if not new_name:
            # Revert to original name if empty
            self.setText(self.original_name)
            return
        
        if new_name != self.original_name:
            from ..db import database as db_ops
            success = db_ops.update_recording_name(self.recording_id, new_name)
            if success:
                self.original_name = new_name
                self.name_changed.emit(self.recording_id, new_name)
                logger.info(f"Updated meeting name to: {new_name}")
            else:
                # Revert to original name if save failed
                self.setText(self.original_name)
                logger.error(f"Failed to update meeting name to: {new_name}")
    
    def keyPressEvent(self, event):
        """Handle key press events."""
        if event.key() == Qt.Key_Escape:
            # Revert changes and lose focus on Escape
            self.setText(self.original_name)
            self.clearFocus()
        else:
            super().keyPressEvent(event)
