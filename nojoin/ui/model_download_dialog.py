import logging
import time
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton, QDialogButtonBox, QMessageBox, QHBoxLayout
)
from PySide6.QtCore import Qt, Signal, QTimer, QThread
from nojoin.utils.theme_utils import apply_theme_to_widget
from nojoin.utils.config_manager import config_manager
from nojoin.utils.model_utils import get_whisper_model_size_mb

logger = logging.getLogger(__name__)


class ModelDownloadWorker(QThread):
    """Worker thread for downloading Whisper models with progress tracking."""
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
            import whisper
            import whisper.transcribe
            
            orig_tqdm = tqdm.tqdm
            worker = self
            
            class QtTqdm(orig_tqdm):
                def update(self2, n):
                    super().update(n)
                    percent = int((self2.n / self2.total) * 100) if self2.total else 0
                    worker.progress.emit(percent)
            
            # Patch tqdm
            whisper.transcribe.tqdm.tqdm = QtTqdm
            
            # Actually load the model (triggers download if needed)
            whisper.load_model(self.model_size, device=self.device)
            
            # Restore tqdm
            whisper.transcribe.tqdm.tqdm = orig_tqdm
            
            self.progress.emit(100)
            self.finished.emit()
            
        except Exception as e:
            logger.error(f"Model download failed: {e}", exc_info=True)
            self.error.emit(str(e))


class ModelDownloadDialog(QDialog):
    """A dedicated dialog for downloading Whisper models with clean progress display."""
    
    def __init__(self, model_size, device="cpu", parent=None):
        super().__init__(parent)
        self.model_size = model_size
        self.device = device
        self.download_worker = None
        self._start_time = time.time()
        self._was_cancelled = False
        self._download_complete = False
        
        self.setWindowTitle("Downloading Whisper Model")
        
        # Use scaled minimum width
        from nojoin.utils.ui_scale_manager import get_ui_scale_manager
        ui_scale_manager = get_ui_scale_manager()
        min_width = ui_scale_manager.scale_value(450)
        self.setMinimumWidth(min_width)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        
        # Apply theme
        apply_theme_to_widget(self, config_manager.get("theme", "dark"))
        
        # Setup UI
        self._setup_ui()
        
        # Setup timer for elapsed time updates
        self._elapsed_timer = QTimer()
        self._elapsed_timer.timeout.connect(self._update_elapsed_time)
        self._elapsed_timer.start(1000)  # Update every second

    def _setup_ui(self):
        """Setup the dialog UI components."""
        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(30, 30, 30, 30)
        
        # Title
        title_label = QLabel(f"Downloading Whisper Model: {self.model_size}")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title_label)
        
        # Model info
        model_size_mb = get_whisper_model_size_mb(self.model_size)
        if model_size_mb:
            info_text = f"Model size: ~{model_size_mb} MB"
        else:
            info_text = "Preparing download..."
        
        self.info_label = QLabel(info_text)
        self.info_label.setAlignment(Qt.AlignCenter)
        self.info_label.setStyleSheet("font-size: 12px; color: #666;")
        layout.addWidget(self.info_label)
        
        # Status label
        self.status_label = QLabel("Initializing download...")
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
        
        # Centered elapsed time
        self.elapsed_label = QLabel("Elapsed: 00:00")
        self.elapsed_label.setAlignment(Qt.AlignCenter)
        self.elapsed_label.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.elapsed_label)
        
        # Buttons
        layout.addSpacing(10)
        button_box = QDialogButtonBox()
        self.cancel_button = button_box.addButton("Cancel", QDialogButtonBox.RejectRole)
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        layout.addWidget(button_box)

    def _update_elapsed_time(self):
        """Update the elapsed time display."""
        if not self._download_complete and not self._was_cancelled:
            elapsed = int(time.time() - self._start_time)
            minutes = elapsed // 60
            seconds = elapsed % 60
            self.elapsed_label.setText(f"Elapsed: {minutes:02d}:{seconds:02d}")

    def _on_cancel_clicked(self):
        """Handle cancel button click."""
        logger.info("Model download cancel requested")
        self._was_cancelled = True
        self.cancel_button.setEnabled(False)
        self.status_label.setText("Cancelling download...")
        
        if self.download_worker and self.download_worker.isRunning():
            self.download_worker.terminate()
            self.download_worker.wait()
        
        self.reject()

    def start_download(self):
        """Start the model download process."""
        logger.info(f"Starting download for Whisper model: {self.model_size}")
        
        self.status_label.setText("Downloading model...")
        
        # Create and start worker
        self.download_worker = ModelDownloadWorker(self.model_size, self.device)
        self.download_worker.progress.connect(self._on_progress_update)
        self.download_worker.finished.connect(self._on_download_finished)
        self.download_worker.error.connect(self._on_download_error)
        self.download_worker.start()

    def _on_progress_update(self, percent):
        """Handle progress updates from worker."""
        if not self._was_cancelled:
            self.progress_bar.setValue(percent)
            self.progress_bar.setFormat(f"{percent}%")
            
            if percent > 0:
                self.status_label.setText(f"Downloading... {percent}%")

    def _on_download_finished(self):
        """Handle successful download completion."""
        logger.info(f"Whisper model {self.model_size} download completed successfully")
        
        self._download_complete = True
        self._elapsed_timer.stop()
        
        self.progress_bar.setValue(100)
        self.status_label.setText("Download complete!")
        self.cancel_button.setText("Close")
        self.cancel_button.clicked.disconnect()
        self.cancel_button.clicked.connect(self.accept)

    def _on_download_error(self, error_message):
        """Handle download errors."""
        logger.error(f"Whisper model download error: {error_message}")
        
        self._download_complete = True
        self._elapsed_timer.stop()
        
        self.status_label.setText("Download failed!")
        
        QMessageBox.critical(
            self, 
            "Download Failed", 
            f"Failed to download Whisper model '{self.model_size}':\n\n{error_message}\n\n"
            f"You can try again later or download the model during your first transcription."
        )
        
        self.reject()

    def was_cancelled(self):
        """Check if the download was cancelled."""
        return self._was_cancelled

    def closeEvent(self, event):
        """Handle window close event."""
        if not self._download_complete and not self._was_cancelled:
            reply = QMessageBox.question(
                self, 
                "Cancel Download?",
                "Model download is still in progress. Are you sure you want to cancel?",
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