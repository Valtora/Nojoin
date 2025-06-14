"""
Update Dialog UI for Nojoin

This module provides dialogs for checking, displaying, and managing updates.
"""

import os
import sys
import logging
from datetime import datetime
from typing import Optional, Dict

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit,
    QComboBox, QCheckBox, QProgressDialog, QMessageBox, QButtonGroup,
    QRadioButton, QGroupBox, QScrollArea, QFrame
)
from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QFont, QPixmap

from ..utils.version_manager import version_manager, UpdatePreference
from ..utils.theme_utils import apply_theme_to_widget
from ..utils.config_manager import config_manager

logger = logging.getLogger(__name__)

class UpdateCheckThread(QThread):
    """Thread for checking updates without blocking the UI."""
    
    update_checked = Signal(bool, object)  # has_update, release_info
    
    def run(self):
        """Check for updates in background thread."""
        try:
            has_update, release_info = version_manager.check_for_updates()
            version_manager.mark_update_check_performed()
            self.update_checked.emit(has_update, release_info)
        except Exception as e:
            logger.error(f"Error in update check thread: {e}")
            self.update_checked.emit(False, None)

class UpdateDownloadThread(QThread):
    """Thread for downloading updates without blocking the UI."""
    
    download_progress = Signal(int, str)  # progress, message
    download_completed = Signal(bool, str)  # success, file_path_or_error
    
    def __init__(self, release_info: Dict):
        super().__init__()
        self.release_info = release_info
    
    def run(self):
        """Download update in background thread."""
        try:
            def progress_callback(progress, message):
                self.download_progress.emit(progress, message)
            
            file_path = version_manager.download_update(self.release_info, progress_callback)
            if file_path:
                self.download_completed.emit(True, file_path)
            else:
                self.download_completed.emit(False, "Download failed")
        except Exception as e:
            logger.error(f"Error in download thread: {e}")
            self.download_completed.emit(False, str(e))

class UpdateAvailableDialog(QDialog):
    """Dialog shown when an update is available."""
    
    def __init__(self, release_info: Dict, parent=None):
        super().__init__(parent)
        self.release_info = release_info
        self.setWindowTitle("Update Available")
        self.setModal(True)
        self.setMinimumSize(500, 400)
        self._init_ui()
        apply_theme_to_widget(self, config_manager.get("theme", "dark"))
    
    def _init_ui(self):
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        # Header with version info
        header_frame = QFrame()
        header_layout = QVBoxLayout(header_frame)
        
        # Show version with pre-release indicator if applicable
        version_text = f"Nojoin {self.release_info['version']} is available!"
        if self.release_info.get('prerelease', False):
            version_text += " (Pre-release)"
        
        title_label = QLabel(version_text)
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title_label.setFont(title_font)
        header_layout.addWidget(title_label)
        
        current_version_label = QLabel(f"Current version: {version_manager.get_current_version()}")
        header_layout.addWidget(current_version_label)
        
        # Add pre-release note if applicable
        if self.release_info.get('prerelease', False):
            prerelease_note = QLabel("Note: This is a pre-release version that may contain experimental features.")
            prerelease_note.setStyleSheet("color: #ff8c00; font-style: italic;")
            header_layout.addWidget(prerelease_note)
        
        if self.release_info.get('published_at'):
            published_date = datetime.fromisoformat(
                self.release_info['published_at'].replace('Z', '+00:00')
            ).strftime('%B %d, %Y')
            date_label = QLabel(f"Released: {published_date}")
            header_layout.addWidget(date_label)
        
        layout.addWidget(header_frame)
        
        # Release notes
        if self.release_info.get('body'):
            notes_label = QLabel("Release Notes:")
            notes_label.setFont(QFont("", weight=QFont.Bold))
            layout.addWidget(notes_label)
            
            notes_text = QTextEdit()
            notes_text.setPlainText(self.release_info['body'])
            notes_text.setReadOnly(True)
            notes_text.setMaximumHeight(150)
            layout.addWidget(notes_text)
        
        # Reminder preferences
        reminder_group = QGroupBox("Remind me about updates:")
        reminder_layout = QVBoxLayout(reminder_group)
        
        self.reminder_group = QButtonGroup()
        
        self.never_radio = QRadioButton("Never")
        self.next_run_radio = QRadioButton("On next run")
        self.week_radio = QRadioButton("In one week")
        self.month_radio = QRadioButton("In one month")
        
        self.reminder_group.addButton(self.never_radio, 0)
        self.reminder_group.addButton(self.next_run_radio, 1)
        self.reminder_group.addButton(self.week_radio, 2)
        self.reminder_group.addButton(self.month_radio, 3)
        
        reminder_layout.addWidget(self.next_run_radio)
        reminder_layout.addWidget(self.week_radio)
        reminder_layout.addWidget(self.month_radio)
        reminder_layout.addWidget(self.never_radio)
        
        # Set default based on current preference
        current_pref = version_manager.get_update_preferences().get("reminder_preference", UpdatePreference.ONE_WEEK)
        if current_pref == UpdatePreference.NEVER:
            self.never_radio.setChecked(True)
        elif current_pref == UpdatePreference.NEXT_RUN:
            self.next_run_radio.setChecked(True)
        elif current_pref == UpdatePreference.ONE_MONTH:
            self.month_radio.setChecked(True)
        else:  # Default to one week
            self.week_radio.setChecked(True)
        
        layout.addWidget(reminder_group)
        
        # Buttons
        button_layout = QHBoxLayout()
        
        self.skip_button = QPushButton("Skip This Update")
        self.skip_button.clicked.connect(self._skip_version)
        
        self.remind_button = QPushButton("Remind Me Later")
        self.remind_button.clicked.connect(self._remind_later)
        
        self.install_button = QPushButton("Download & Install")
        self.install_button.setDefault(True)
        self.install_button.clicked.connect(self._start_update)
        
        button_layout.addWidget(self.skip_button)
        button_layout.addStretch()
        button_layout.addWidget(self.remind_button)
        button_layout.addWidget(self.install_button)
        
        layout.addLayout(button_layout)
    
    def _get_selected_preference(self) -> str:
        """Get the selected reminder preference."""
        if self.never_radio.isChecked():
            return UpdatePreference.NEVER
        elif self.next_run_radio.isChecked():
            return UpdatePreference.NEXT_RUN
        elif self.month_radio.isChecked():
            return UpdatePreference.ONE_MONTH
        else:
            return UpdatePreference.ONE_WEEK
    
    def _skip_version(self):
        """Skip this version."""
        # Skip this specific version
        version_manager.skip_version(self.release_info['version'])
        version_manager.set_reminder_preference(self._get_selected_preference())
        self.reject()
    
    def _remind_later(self):
        """Set reminder preference and close."""
        version_manager.set_reminder_preference(self._get_selected_preference())
        version_manager.mark_update_reminder_shown()
        self.reject()
    
    def _start_update(self):
        """Start the update process."""
        version_manager.set_reminder_preference(self._get_selected_preference())
        self.accept()

class UpdateProgressDialog(QDialog):
    """Dialog showing update progress."""
    
    def __init__(self, release_info: Dict, parent=None):
        super().__init__(parent)
        self.release_info = release_info
        self.download_thread = None
        self.update_file_path = None
        
        self.setWindowTitle("Updating Nojoin")
        self.setModal(True)
        self.setMinimumSize(400, 200)
        self._init_ui()
        apply_theme_to_widget(self, config_manager.get("theme", "dark"))
        
        # Start the update process
        self._start_download()
    
    def _init_ui(self):
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        # Title
        title_label = QLabel(f"Updating to Nojoin {self.release_info['version']}")
        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        title_label.setFont(title_font)
        layout.addWidget(title_label)
        
        # Progress info
        self.status_label = QLabel("Preparing update...")
        layout.addWidget(self.status_label)
        
        # Progress dialog (will be created when needed)
        self.progress_dialog = None
        
        # Buttons
        button_layout = QHBoxLayout()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self._cancel_update)
        button_layout.addStretch()
        button_layout.addWidget(self.cancel_button)
        layout.addLayout(button_layout)
    
    def _start_download(self):
        """Start downloading the update."""
        self.status_label.setText("Downloading update...")
        
        # Create progress dialog
        self.progress_dialog = QProgressDialog("Downloading update...", "Cancel", 0, 100, self)
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.setAutoClose(False)
        self.progress_dialog.setAutoReset(False)
        self.progress_dialog.canceled.connect(self._cancel_update)
        self.progress_dialog.show()
        
        # Start download thread
        self.download_thread = UpdateDownloadThread(self.release_info)
        self.download_thread.download_progress.connect(self._update_progress)
        self.download_thread.download_completed.connect(self._download_completed)
        self.download_thread.start()
    
    def _update_progress(self, progress: int, message: str):
        """Update the progress display."""
        if self.progress_dialog:
            self.progress_dialog.setValue(progress)
            self.progress_dialog.setLabelText(message)
        self.status_label.setText(message)
    
    def _download_completed(self, success: bool, result: str):
        """Handle download completion."""
        if self.progress_dialog:
            self.progress_dialog.close()
            self.progress_dialog = None
        
        if success:
            self.update_file_path = result
            self._start_backup_and_update()
        else:
            QMessageBox.critical(self, "Download Failed", f"Failed to download update:\n{result}")
            self.reject()
    
    def _start_backup_and_update(self):
        """Start the update process (Inno Setup handles backup automatically)."""
        self.status_label.setText("Preparing for update...")
        
        # Inno Setup automatically preserves config.json and nojoin_data.db
        # No manual backup needed - proceed directly to update
        self._execute_update(None)
    
    def _execute_update(self, backup_path: Optional[str]):
        """Execute the update using Inno Setup installer."""
        try:
            self.status_label.setText("Preparing installer...")
            
            # Show final confirmation
            reply = QMessageBox.question(
                self, "Ready to Update",
                f"Ready to update to Nojoin {self.release_info['version']}.\n\n"
                "The installer will:\n"
                "1. Preserve your settings and database\n"
                "2. Update application files\n"
                "3. Restart Nojoin automatically\n\n"
                "Continue with the update?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            
            if reply == QMessageBox.Yes:
                # Execute the installer
                if version_manager.execute_update(self.update_file_path):
                    QMessageBox.information(
                        self, "Update Started",
                        "The installer is running. Nojoin will close and restart automatically."
                    )
                    # Signal the main application to close
                    self.accept()
                    # Close the main application to allow installer to work
                    sys.exit(0)
                else:
                    QMessageBox.critical(self, "Update Failed", "Failed to start the installer.")
                    self.reject()
            else:
                self.reject()
                
        except Exception as e:
            logger.error(f"Error executing update: {e}")
            QMessageBox.critical(self, "Update Error", f"An error occurred: {str(e)}")
            self.reject()
    
    def _cancel_update(self):
        """Cancel the update process."""
        if self.download_thread and self.download_thread.isRunning():
            self.download_thread.terminate()
            self.download_thread.wait()
        
        if self.progress_dialog:
            self.progress_dialog.close()
        
        # Clean up downloaded file if it exists
        if self.update_file_path and os.path.exists(self.update_file_path):
            try:
                os.remove(self.update_file_path)
            except Exception as e:
                logger.warning(f"Could not clean up downloaded file: {e}")
        
        self.reject()

class CheckForUpdatesDialog(QDialog):
    """Dialog for manually checking for updates."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.check_thread = None
        self.setWindowTitle("Check for Updates")
        self.setModal(True)
        self.setMinimumSize(300, 150)
        self._init_ui()
        apply_theme_to_widget(self, config_manager.get("theme", "dark"))
        
        # Start checking immediately
        self._start_check()
    
    def _init_ui(self):
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        self.status_label = QLabel("Checking for updates...")
        layout.addWidget(self.status_label)
        
        button_layout = QHBoxLayout()
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        self.close_button.setEnabled(False)
        button_layout.addStretch()
        button_layout.addWidget(self.close_button)
        layout.addLayout(button_layout)
    
    def _start_check(self):
        """Start checking for updates."""
        self.check_thread = UpdateCheckThread()
        self.check_thread.update_checked.connect(self._check_completed)
        self.check_thread.start()
    
    def _check_completed(self, has_update: bool, release_info: Optional[Dict]):
        """Handle update check completion."""
        self.close_button.setEnabled(True)
        
        if has_update and release_info:
            self.status_label.setText(f"Update available: Nojoin {release_info['version']}")
            
            # Show update dialog
            update_dialog = UpdateAvailableDialog(release_info, self)
            if update_dialog.exec() == QDialog.Accepted:
                # User chose to update
                progress_dialog = UpdateProgressDialog(release_info, self)
                progress_dialog.exec()
        else:
            self.status_label.setText("You have the latest version of Nojoin!")
    
    def closeEvent(self, event):
        """Handle dialog close event."""
        if self.check_thread and self.check_thread.isRunning():
            self.check_thread.terminate()
            self.check_thread.wait()
        event.accept()

def check_for_updates_on_startup(parent_window):
    """Check for updates on application startup if enabled."""
    if not version_manager.should_check_for_updates():
        return
    
    def check_completed(has_update: bool, release_info: Optional[Dict]):
        if has_update and release_info and version_manager.should_remind_about_update(release_info):
            # Show update available dialog
            dialog = UpdateAvailableDialog(release_info, parent_window)
            if dialog.exec() == QDialog.Accepted:
                # User chose to update
                progress_dialog = UpdateProgressDialog(release_info, parent_window)
                progress_dialog.exec()
    
    # Start background check
    check_thread = UpdateCheckThread()
    check_thread.update_checked.connect(check_completed)
    check_thread.start()
    
    # Keep reference to thread to prevent garbage collection
    parent_window._update_check_thread = check_thread 