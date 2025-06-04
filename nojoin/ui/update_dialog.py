"""
Update Dialog for Nojoin
Shows available updates and handles download/installation
"""

import sys
import logging
from pathlib import Path
from datetime import datetime

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
    QTextEdit, QProgressBar, QGroupBox, QCheckBox, QSpacerItem,
    QSizePolicy, QFrame, QMessageBox
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QFont, QPixmap, QIcon

from ..utils.version_manager import VersionManager
from ..utils.theme_utils import apply_theme_to_widget
from ..utils.config_manager import config_manager

logger = logging.getLogger(__name__)

class UpdateDownloadWorker(QThread):
    """Worker thread for downloading updates"""
    
    progress = Signal(int)  # Progress percentage
    finished = Signal(str)  # Installer path
    error = Signal(str)     # Error message
    
    def __init__(self, version_manager, update_info):
        super().__init__()
        self.version_manager = version_manager
        self.update_info = update_info
        
    def run(self):
        try:
            installer_path = self.version_manager.download_update(
                self.update_info, 
                progress_callback=self.progress.emit
            )
            
            if installer_path:
                self.finished.emit(str(installer_path))
            else:
                self.error.emit("Download failed - please try again later")
                
        except Exception as e:
            logger.error(f"Update download error: {e}", exc_info=True)
            self.error.emit(f"Download error: {str(e)}")

class UpdateDialog(QDialog):
    """Dialog for handling application updates"""
    
    update_installed = Signal()  # Emitted when update is installed
    
    def __init__(self, update_info, parent=None):
        super().__init__(parent)
        self.update_info = update_info
        self.version_manager = VersionManager()
        self.download_worker = None
        
        self.setup_ui()
        self.setup_connections()
        
        # Apply theme
        theme = config_manager.get("theme", "dark")
        apply_theme_to_widget(self, theme)
        
    def setup_ui(self):
        """Setup the user interface"""
        self.setWindowTitle("Nojoin Update Available")
        self.setModal(True)
        self.setFixedSize(500, 400)
        
        # Main layout
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        
        # Header with icon and title
        header_layout = QHBoxLayout()
        
        # Try to load icon
        try:
            icon_path = Path(__file__).parent.parent.parent / "assets" / "NojoinLogo.png"
            if icon_path.exists():
                icon_label = QLabel()
                pixmap = QPixmap(str(icon_path)).scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                icon_label.setPixmap(pixmap)
                header_layout.addWidget(icon_label)
        except Exception:
            pass
            
        title_layout = QVBoxLayout()
        title_label = QLabel("Update Available")
        title_label.setFont(QFont("Segoe UI", 16, QFont.Bold))
        title_layout.addWidget(title_label)
        
        version_label = QLabel(f"Version {self.update_info['version']} is available")
        version_label.setFont(QFont("Segoe UI", 10))
        title_layout.addWidget(version_label)
        
        header_layout.addLayout(title_layout)
        header_layout.addStretch()
        layout.addLayout(header_layout)
        
        # Update info group
        info_group = QGroupBox("What's New")
        info_layout = QVBoxLayout(info_group)
        
        # Release notes
        self.release_notes = QTextEdit()
        self.release_notes.setPlainText(self.update_info.get('release_notes', 'No release notes available'))
        self.release_notes.setMaximumHeight(150)
        self.release_notes.setReadOnly(True)
        info_layout.addWidget(self.release_notes)
        
        # Update details
        details_layout = QHBoxLayout()
        
        size_mb = self.update_info.get('download_size', 0) / (1024 * 1024)
        size_label = QLabel(f"Download size: {size_mb:.1f} MB")
        details_layout.addWidget(size_label)
        
        details_layout.addStretch()
        
        published_date = self.update_info.get('published_at', '')
        if published_date:
            try:
                date_obj = datetime.fromisoformat(published_date.replace('Z', '+00:00'))
                date_str = date_obj.strftime('%B %d, %Y')
                date_label = QLabel(f"Released: {date_str}")
                details_layout.addWidget(date_label)
            except Exception:
                pass
                
        info_layout.addLayout(details_layout)
        layout.addWidget(info_group)
        
        # Progress bar (hidden initially)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        # Status label
        self.status_label = QLabel("")
        self.status_label.setVisible(False)
        layout.addWidget(self.status_label)
        
        # Options
        options_group = QGroupBox("Options")
        options_layout = QVBoxLayout(options_group)
        
        self.auto_check_checkbox = QCheckBox("Automatically check for updates")
        self.auto_check_checkbox.setChecked(config_manager.get("auto_update_check", True))
        options_layout.addWidget(self.auto_check_checkbox)
        
        layout.addWidget(options_group)
        
        # Spacer
        layout.addItem(QSpacerItem(20, 20, QSizePolicy.Minimum, QSizePolicy.Expanding))
        
        # Buttons
        button_layout = QHBoxLayout()
        
        self.later_button = QPushButton("Remind Me Later")
        self.later_button.setMinimumWidth(120)
        button_layout.addWidget(self.later_button)
        
        self.skip_button = QPushButton("Skip This Version")
        self.skip_button.setMinimumWidth(120)
        button_layout.addWidget(self.skip_button)
        
        button_layout.addStretch()
        
        self.download_button = QPushButton("Download Update")
        self.download_button.setMinimumWidth(120)
        self.download_button.setDefault(True)
        button_layout.addWidget(self.download_button)
        
        layout.addLayout(button_layout)
        
    def setup_connections(self):
        """Setup signal connections"""
        self.later_button.clicked.connect(self.remind_later)
        self.skip_button.clicked.connect(self.skip_version)
        self.download_button.clicked.connect(self.download_update)
        self.auto_check_checkbox.toggled.connect(self.toggle_auto_check)
        
    def toggle_auto_check(self, checked):
        """Toggle automatic update checking"""
        config_manager.set("auto_update_check", checked)
        
    def remind_later(self):
        """Remind user later"""
        logger.info("User chose to be reminded later about update")
        self.reject()
        
    def skip_version(self):
        """Skip this version"""
        logger.info(f"User chose to skip version {self.update_info['version']}")
        # Save skipped version to config
        config_manager.set("skipped_version", self.update_info['version'])
        self.reject()
        
    def download_update(self):
        """Start downloading the update"""
        if self.download_worker and self.download_worker.isRunning():
            return  # Already downloading
            
        logger.info(f"Starting download of version {self.update_info['version']}")
        
        # Update UI for download state
        self.download_button.setEnabled(False)
        self.download_button.setText("Downloading...")
        self.progress_bar.setVisible(True)
        self.status_label.setVisible(True)
        self.status_label.setText("Downloading update...")
        
        # Start download in background
        self.download_worker = UpdateDownloadWorker(self.version_manager, self.update_info)
        self.download_worker.progress.connect(self.update_progress)
        self.download_worker.finished.connect(self.download_finished)
        self.download_worker.error.connect(self.download_error)
        self.download_worker.start()
        
    def update_progress(self, percent):
        """Update download progress"""
        self.progress_bar.setValue(percent)
        self.status_label.setText(f"Downloading update... {percent}%")
        
    def download_finished(self, installer_path):
        """Handle successful download"""
        logger.info(f"Update downloaded successfully: {installer_path}")
        
        self.progress_bar.setVisible(False)
        self.status_label.setText("Download complete!")
        
        # Ask user if they want to install now
        reply = QMessageBox.question(
            self,
            "Install Update",
            "Download completed successfully!\n\n"
            "Do you want to install the update now?\n"
            "Nojoin will close and restart after installation.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        
        if reply == QMessageBox.Yes:
            self.install_update(installer_path)
        else:
            # Save installer path for later
            config_manager.set("pending_installer", installer_path)
            self.status_label.setText("Update ready to install. You can install it later from the Help menu.")
            
            # Re-enable download button as "Install Now"
            self.download_button.setEnabled(True)
            self.download_button.setText("Install Now")
            self.download_button.clicked.disconnect()
            self.download_button.clicked.connect(lambda: self.install_update(installer_path))
            
    def download_error(self, error_message):
        """Handle download error"""
        logger.error(f"Update download failed: {error_message}")
        
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"Download failed: {error_message}")
        
        # Re-enable download button
        self.download_button.setEnabled(True)
        self.download_button.setText("Retry Download")
        
        QMessageBox.warning(
            self,
            "Download Failed",
            f"Failed to download update:\n{error_message}\n\n"
            "Please check your internet connection and try again."
        )
        
    def install_update(self, installer_path):
        """Install the update"""
        logger.info(f"Installing update from: {installer_path}")
        
        # Confirm installation
        reply = QMessageBox.information(
            self,
            "Installing Update",
            "Nojoin will now close and the installer will launch.\n"
            "Your recordings and settings will be preserved.\n\n"
            "Click OK to continue.",
            QMessageBox.Ok | QMessageBox.Cancel,
            QMessageBox.Ok
        )
        
        if reply == QMessageBox.Ok:
            # Launch installer and close application
            if self.version_manager.install_update(Path(installer_path)):
                self.update_installed.emit()
                QTimer.singleShot(1000, lambda: sys.exit(0))  # Exit after 1 second
            else:
                QMessageBox.critical(
                    self,
                    "Installation Failed",
                    "Failed to launch the installer.\n"
                    "Please run the installer manually from:\n"
                    f"{installer_path}"
                )
                
    def closeEvent(self, event):
        """Handle dialog close"""
        # Stop download worker if running
        if self.download_worker and self.download_worker.isRunning():
            self.download_worker.terminate()
            self.download_worker.wait(3000)  # Wait up to 3 seconds
            
        super().closeEvent(event) 