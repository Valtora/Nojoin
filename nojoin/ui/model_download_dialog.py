import logging
import time
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton, QDialogButtonBox, QMessageBox, QHBoxLayout
)
from PySide6.QtCore import Qt, Signal, QTimer, QThread
from nojoin.utils.theme_utils import apply_theme_to_widget
from nojoin.utils.config_manager import config_manager
from nojoin.utils.model_utils import get_whisper_model_size_mb
from nojoin.utils.progress_manager import get_progress_manager

logger = logging.getLogger(__name__)


class ModelDownloadWorker(QThread):
    """Enhanced worker thread for downloading Whisper models with unified progress system."""
    progress = Signal(int)  # percent
    finished = Signal()
    error = Signal(str)

    def __init__(self, model_size, device):
        super().__init__()
        self.model_size = model_size
        self.device = device
        self.progress_manager = get_progress_manager()

    def run(self):
        """Download the model using the unified progress system."""
        try:
            self._download_with_progress()
        except Exception as e:
            self._handle_download_error(e)

    def _download_with_progress(self):
        """Download model with progress tracking through unified system."""
        logger.info(f"Starting model download: {self.model_size} on {self.device}")
        
        # Log system health before download
        health_status = self.progress_manager.monitor_health()
        logger.info(f"System health before download: {health_status['status']}")
        if health_status['issues']:
            logger.warning(f"Pre-download issues detected: {health_status['issues']}")
        
        # Create progress callback that emits to UI
        def progress_callback(percent):
            self.progress.emit(percent)
            logger.debug(f"Download progress: {percent}% for model {self.model_size}")
        
        # Use unified progress system for download
        with self.progress_manager.create_download_context(progress_callback) as context:
            try:
                import whisper
                
                # Check for conflicts before proceeding
                conflicts = self.progress_manager.detect_tqdm_conflicts()
                if conflicts:
                    logger.warning(f"TQDM conflicts detected during download: {conflicts}")
                    # Log debug info for troubleshooting
                    self.progress_manager.log_debug_info()
                
                # Log download start
                logger.info(f"Initiating whisper.load_model for {self.model_size} on {self.device}")
                
                # Load the model (this triggers download if needed)
                # The progress context handles TQDM patching automatically
                model = whisper.load_model(self.model_size, device=self.device)
                
                # Ensure we report 100% completion
                context.emit_progress(100, 100)
                self.progress.emit(100)
                
                # Log completion with statistics
                stats = self.progress_manager.get_progress_statistics()
                logger.info(f"Model download completed successfully: {self.model_size}")
                logger.info(f"Download statistics: {stats}")
                
                self.finished.emit()
                
            except Exception as e:
                logger.error(f"Error during model download: {e}", exc_info=True)
                
                # Log additional debug information on error
                logger.error("=== Download Error Debug Information ===")
                self.progress_manager.log_debug_info()
                
                # Check system health after error
                health_status = self.progress_manager.monitor_health()
                logger.error(f"System health after error: {health_status}")
                
                raise

    def _handle_download_error(self, error: Exception):
        """Handle download errors with detailed logging and user feedback."""
        error_msg = str(error)
        
        # Categorize common errors for better user feedback
        if "tqdm" in error_msg.lower():
            user_msg = (f"Progress tracking error during model download: {error_msg}\n\n"
                       f"This may be due to conflicting progress systems. "
                       f"The download may still succeed in the background.")
            logger.error(f"TQDM-related error in model download: {error}")
            
            # Try fallback download without progress tracking
            self._attempt_fallback_download()
            return
            
        elif "network" in error_msg.lower() or "connection" in error_msg.lower():
            user_msg = (f"Network error during model download: {error_msg}\n\n"
                       f"Please check your internet connection and try again.")
            logger.error(f"Network error in model download: {error}")
            
        elif "disk" in error_msg.lower() or "space" in error_msg.lower():
            user_msg = (f"Disk space error during model download: {error_msg}\n\n"
                       f"Please free up disk space and try again.")
            logger.error(f"Disk space error in model download: {error}")
            
        elif "permission" in error_msg.lower():
            user_msg = (f"Permission error during model download: {error_msg}\n\n"
                       f"Please check file permissions and try running as administrator if needed.")
            logger.error(f"Permission error in model download: {error}")
            
        else:
            user_msg = f"Model download failed: {error_msg}"
            logger.error(f"General error in model download: {error}")
        
        self.error.emit(user_msg)
        
    def _attempt_fallback_download(self):
        """Attempt download without progress tracking as fallback."""
        try:
            logger.info(f"Attempting fallback download for model: {self.model_size}")
            
            # Use fallback progress reporter
            from nojoin.utils.progress_manager import FallbackProgressReporter
            
            def fallback_progress(percent):
                self.progress.emit(percent)
                
            fallback_reporter = FallbackProgressReporter(fallback_progress)
            
            # Simple download without TQDM patching
            import whisper
            model = whisper.load_model(self.model_size, device=self.device)
            
            fallback_reporter.complete()
            logger.info(f"Fallback download completed successfully: {self.model_size}")
            self.finished.emit()
            
        except Exception as fallback_error:
            logger.error(f"Fallback download also failed: {fallback_error}")
            self.error.emit(f"Both primary and fallback downloads failed: {str(fallback_error)}")


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