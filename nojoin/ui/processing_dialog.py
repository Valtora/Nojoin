import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton, QDialogButtonBox, QMessageBox
)
from PySide6.QtCore import Qt, Slot

logger = logging.getLogger(__name__)

class ProcessingProgressDialog(QDialog):
    def __init__(self, recording_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Processing Recording")
        self.setMinimumWidth(350)
        self.setModal(True)

        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(10)

        self.message_label = QLabel(f"Starting processing for:\n<b>{recording_name}</b>")
        self.message_label.setAlignment(Qt.AlignCenter)
        self.message_label.setWordWrap(True)
        self.layout.addWidget(self.message_label)

        # Initialising spinner and label
        self.init_label = QLabel("Initialising...")
        self.init_label.setAlignment(Qt.AlignCenter)
        self.layout.addWidget(self.init_label)
        self.init_spinner = QProgressBar()
        self.init_spinner.setRange(0, 0)  # Indeterminate
        self.init_spinner.setTextVisible(False)
        self.layout.addWidget(self.init_spinner)

        # Unified Progress Bar (hidden until progress starts)
        self.main_progress_bar = QProgressBar()
        self.main_progress_bar.setRange(0, 100)
        self.main_progress_bar.setValue(0)
        self.main_progress_bar.setTextVisible(True)
        self.main_progress_bar.setVisible(False)
        self.layout.addWidget(self.main_progress_bar)

        self.button_box = QDialogButtonBox()
        self.cancel_button = self.button_box.addButton("Cancel", QDialogButtonBox.RejectRole)
        self.layout.addWidget(self.button_box)

        self.cancel_button.clicked.connect(self.cancel_clicked)

        self._was_cancelled = False
        self._processing_complete = False
        self._stage = None  # Track current stage for unified progress
        self._seen_progress = False

    def cancel_clicked(self):
        logger.info("Cancel button clicked in progress dialog.")
        self._was_cancelled = True
        self.cancel_button.setEnabled(False)
        self.message_label.setText("Cancellation requested...")
        self.rejected.emit()

    @Slot(str)
    def update_message(self, message):
        self.message_label.setText(message)
        # Optionally update stage for progress mapping
        if "transcrib" in message.lower():
            self._stage = "transcription"
        elif "diariz" in message.lower():
            self._stage = "diarization"
        else:
            self._stage = None

    @Slot(int, float, float)
    def update_progress(self, percent, elapsed, eta):
        # On first progress, hide spinner and show progress bar
        if not self._seen_progress:
            self.init_label.setVisible(False)
            self.init_spinner.setVisible(False)
            self.main_progress_bar.setVisible(True)
            self._seen_progress = True
        # Map progress to unified bar: 0-50% for transcription, 50-100% for diarization
        if self._stage == "diarization":
            mapped_percent = 50 + (percent / 2)
        else:  # Default to transcription if unknown
            mapped_percent = percent / 2
        mapped_percent = min(100, max(0, int(mapped_percent)))
        self.main_progress_bar.setValue(mapped_percent)
        self.main_progress_bar.setFormat(f"{mapped_percent}%")
        self.main_progress_bar.setTextVisible(True)

    def was_cancelled(self):
        return self._was_cancelled

    def closeEvent(self, event):
        if not self._processing_complete and not self._was_cancelled:
            logger.warning("Attempted to close processing dialog externally.")
            event.ignore()
        else:
            event.accept()

    def mark_processing_complete(self):
        self._processing_complete = True

class GeminiNotesSpinnerDialog(QDialog):
    def __init__(self, parent=None, message="Generating meeting notes with Gemini..."):
        super().__init__(parent)
        self.setWindowTitle("Generating Meeting Notes")
        self.setMinimumWidth(320)
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        self.spinner_label = QLabel()
        self.spinner_label.setAlignment(Qt.AlignCenter)
        self.spinner = QProgressBar()
        self.spinner.setRange(0, 0)
        self.spinner.setTextVisible(False)
        layout.addWidget(self.spinner_label)
        layout.addWidget(self.spinner)
        self.message_label = QLabel(message)
        self.message_label.setAlignment(Qt.AlignCenter)
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)
        self.setLayout(layout)

    def set_message(self, message):
        self.message_label.setText(message) 