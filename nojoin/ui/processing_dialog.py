import logging
import time
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton, QDialogButtonBox, QMessageBox, QHBoxLayout
)
from PySide6.QtCore import Qt, Slot, QTimer
from nojoin.utils.theme_utils import apply_theme_to_widget, THEME_PALETTE
from nojoin.utils.config_manager import config_manager

logger = logging.getLogger(__name__)

class ProcessingProgressDialog(QDialog):
    """A modern progress dialog for audio processing with unified progress tracking."""
    
    # Progress stage ranges (as percentages of total)
    STAGE_RANGES = {
        'vad': (0, 10),        # Voice Activity Detection: 0-10%
        'transcription': (10, 50),  # Transcription: 10-50%
        'diarization': (50, 100)    # Diarization: 50-100%
    }
    
    def __init__(self, recording_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Processing Recording")
        self.setMinimumWidth(400)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        # Apply theme
        apply_theme_to_widget(self, config_manager.get("theme", "dark"))
        
        # Initialize state
        self._current_stage = None
        self._stage_progress = 0
        self._overall_progress = 0
        self._start_time = time.time()
        self._was_cancelled = False
        self._processing_complete = False
        
        # Setup UI
        self._setup_ui(recording_name)
        
        # Setup timer for elapsed time updates
        self._elapsed_timer = QTimer()
        self._elapsed_timer.timeout.connect(self._update_elapsed_time)
        self._elapsed_timer.start(1000)  # Update every second

    def _setup_ui(self, recording_name):
        """Setup the dialog UI components."""
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Title
        title_label = QLabel(f"Processing: {recording_name}")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title_label)
        
        # Status label
        self.status_label = QLabel("Initializing...")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("font-size: 12px;")
        layout.addWidget(self.status_label)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #ccc;
                border-radius: 5px;
                text-align: center;
                height: 25px;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                border-radius: 4px;
            }
        """)
        layout.addWidget(self.progress_bar)
        
        # Time info layout
        time_layout = QHBoxLayout()
        time_layout.setSpacing(20)
        
        self.elapsed_label = QLabel("Elapsed: 00:00")
        self.elapsed_label.setStyleSheet("color: #666;")
        time_layout.addWidget(self.elapsed_label)
        
        time_layout.addStretch()
        
        self.stage_info_label = QLabel("")
        self.stage_info_label.setStyleSheet("color: #666;")
        time_layout.addWidget(self.stage_info_label)
        
        layout.addLayout(time_layout)
        
        # Buttons
        layout.addSpacing(10)
        button_box = QDialogButtonBox()
        self.cancel_button = button_box.addButton("Cancel", QDialogButtonBox.RejectRole)
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        layout.addWidget(button_box)

    def _update_elapsed_time(self):
        """Update the elapsed time display."""
        if not self._processing_complete and not self._was_cancelled:
            elapsed = int(time.time() - self._start_time)
            minutes = elapsed // 60
            seconds = elapsed % 60
            self.elapsed_label.setText(f"Elapsed: {minutes:02d}:{seconds:02d}")

    def _on_cancel_clicked(self):
        """Handle cancel button click."""
        logger.info("Cancel requested in progress dialog")
        self._was_cancelled = True
        self.cancel_button.setEnabled(False)
        self.status_label.setText("Cancelling...")
        self.rejected.emit()

    def set_stage(self, stage: str, stage_name: str = None):
        """Set the current processing stage."""
        if stage not in self.STAGE_RANGES:
            logger.warning(f"Unknown stage: {stage}")
            return
            
        self._current_stage = stage
        self._stage_progress = 0
        
        # Update status label
        stage_names = {
            'vad': 'Detecting voice activity',
            'transcription': 'Transcribing audio',
            'diarization': 'Identifying speakers'
        }
        
        name = stage_name or stage_names.get(stage, stage.title())
        self.status_label.setText(f"{name}...")
        self.stage_info_label.setText(f"Stage: {stage.title()}")
        
        # Update overall progress to start of new stage
        start_percent, _ = self.STAGE_RANGES[stage]
        self._update_overall_progress(start_percent)

    @Slot(int)
    def update_stage_progress(self, stage_percent: int):
        """Update progress within the current stage (0-100%)."""
        if self._current_stage is None:
            return
            
        # Clamp to valid range
        stage_percent = max(0, min(100, stage_percent))
        self._stage_progress = stage_percent
        
        # Map stage progress to overall progress
        start, end = self.STAGE_RANGES[self._current_stage]
        stage_range = end - start
        overall_progress = start + (stage_range * stage_percent / 100)
        
        self._update_overall_progress(overall_progress)

    def _update_overall_progress(self, percent: float):
        """Update the overall progress bar."""
        self._overall_progress = int(max(0, min(100, percent)))
        self.progress_bar.setValue(self._overall_progress)
        self.progress_bar.setFormat(f"{self._overall_progress}%")

    @Slot(str)
    def update_message(self, message: str):
        """Update the status message."""
        self.status_label.setText(message)

    @Slot(int, float, float)
    def update_progress(self, percent: int, elapsed: float = 0, eta: float = 0):
        """Legacy progress update method for compatibility."""
        self.update_stage_progress(percent)

    def was_cancelled(self):
        """Check if the dialog was cancelled."""
        return self._was_cancelled

    def mark_processing_complete(self):
        """Mark the processing as complete."""
        self._processing_complete = True
        self._elapsed_timer.stop()
        self._update_overall_progress(100)
        self.status_label.setText("Processing complete!")
        self.cancel_button.setText("Close")
        self.cancel_button.setEnabled(True)
        self.cancel_button.clicked.disconnect()
        self.cancel_button.clicked.connect(self.accept)

    def closeEvent(self, event):
        """Handle window close event."""
        if not self._processing_complete and not self._was_cancelled:
            reply = QMessageBox.question(
                self, 
                "Cancel Processing?",
                "Processing is still in progress. Are you sure you want to cancel?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.Yes:
                self._on_cancel_clicked()
            else:
                event.ignore()
                return
                
        self._elapsed_timer.stop()
        event.accept() 