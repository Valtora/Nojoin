import os
import re
from typing import List, Tuple, Optional
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit, 
    QPushButton, QCheckBox, QComboBox, QLabel, QFrame, QProgressBar,
    QTextEdit, QMessageBox, QGroupBox
)
from PySide6.QtCore import Qt, Signal, QThread, QTimer
from PySide6.QtGui import QFont, QTextCursor, QTextDocument

from nojoin.utils.theme_utils import get_theme_qss, THEME_PALETTE
from nojoin.db import database as db_ops
from nojoin.utils.config_manager import from_project_relative_path, config_manager
from nojoin.utils.transcript_store import TranscriptStore
import logging

logger = logging.getLogger(__name__)


class FindReplaceWorker(QThread):
    """Worker thread for bulk find/replace operations across all transcripts."""
    progress_update = Signal(int, str)  # progress percentage, current file
    finished = Signal(int)  # total replacements made
    error = Signal(str)  # error message
    
    def __init__(self, search_text: str, replace_text: str, case_sensitive: bool, whole_word: bool):
        super().__init__()
        self.search_text = search_text
        self.replace_text = replace_text
        self.case_sensitive = case_sensitive
        self.whole_word = whole_word
        self.total_replacements = 0
        
    def run(self):
        try:
            recordings = db_ops.get_all_recordings()
            total_files = len(recordings)
            processed = 0
            
            for recording in recordings:
                recording_dict = dict(recording) if not isinstance(recording, dict) else recording
                recording_id = recording_dict['id']
                recording_name = recording_dict.get('name', f'Recording {recording_id}')
                
                self.progress_update.emit(int((processed / total_files) * 100), recording_name)
                
                # Process diarized transcript
                if TranscriptStore.exists(recording_id, "diarized"):
                    def replacement_fn(text):
                        return FindReplaceWorker._replace_in_text(
                            text, self.search_text, self.replace_text, self.case_sensitive, self.whole_word
                        )
                    replacements = TranscriptStore.replace(recording_id, replacement_fn, "diarized")
                    if replacements > 0:
                        self.total_replacements += replacements
                        logger.info(f"Made {replacements} replacements in diarized transcript for {recording_id}")
                
                # Process meeting notes
                notes_entry = db_ops.get_meeting_notes_for_recording(recording_id)
                if notes_entry and notes_entry.get('notes'):
                    notes_text = notes_entry['notes']
                    new_notes, replacements = FindReplaceWorker._replace_in_text(
                        notes_text, self.search_text, self.replace_text, self.case_sensitive, self.whole_word
                    )
                    if replacements > 0:
                        # Update notes in database
                        db_ops.add_meeting_notes(
                            recording_id, 
                            notes_entry.get('provider', 'unknown'),
                            notes_entry.get('model', 'unknown'), 
                            new_notes
                        )
                        self.total_replacements += replacements
                        logger.info(f"Made {replacements} replacements in notes for {recording_id}")
                
                processed += 1
                
            self.progress_update.emit(100, "Complete")
            self.finished.emit(self.total_replacements)
            
        except Exception as e:
            logger.error(f"Find/Replace worker error: {e}", exc_info=True)
            self.error.emit(str(e))
    
    def _replace_in_file(self, file_path: str) -> int:
        """Replace text in a transcript file and return number of replacements."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            new_content, replacements = FindReplaceWorker._replace_in_text(
                content, self.search_text, self.replace_text, self.case_sensitive, self.whole_word
            )
            
            if replacements > 0:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
            
            return replacements
            
        except Exception as e:
            logger.error(f"Error processing file {file_path}: {e}")
            return 0
    
    @staticmethod
    def _replace_in_text(text: str, search_text: str, replace_text: str, case_sensitive: bool, whole_word: bool) -> Tuple[str, int]:
        """Replace text and return (new_text, replacement_count)."""
        if not search_text:
            return text, 0
            
        flags = 0 if case_sensitive else re.IGNORECASE
        
        if whole_word:
            pattern = r'\b' + re.escape(search_text) + r'\b'
        else:
            pattern = re.escape(search_text)
        
        new_text, replacements = re.subn(pattern, replace_text, text, flags=flags)
        return new_text, replacements


class FindReplaceDialog(QDialog):
    """Find and Replace dialog with Notepad++-like interface."""
    
    # Signal emitted when bulk operations complete that might affect current view
    bulk_operation_completed = Signal()
    
    def __init__(self, parent=None, text_edit: QTextEdit = None, theme_name: str = "dark", recording_id: Optional[int] = None):
        super().__init__(parent)
        self.text_edit = text_edit
        self.theme_name = theme_name
        self.recording_id = recording_id
        self.worker = None
        
        self.setWindowTitle("Find and Replace")
        
        # Use scaled minimum size
        from nojoin.utils.ui_scale_manager import get_ui_scale_manager
        self.ui_scale_manager = get_ui_scale_manager()
        min_width, min_height = self.ui_scale_manager.get_scaled_minimum_sizes()['find_replace_dialog']
        self.setMinimumSize(min_width, min_height)
        self.setModal(True)
        
        self._setup_ui()
        self._apply_theme()
        self._connect_signals()
        
        # Focus on find field
        self.find_line_edit.setFocus()
        
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)
        
        # --- Search and Replace Fields ---
        fields_group = QGroupBox("Find and Replace")
        fields_layout = QGridLayout(fields_group)
        fields_layout.setSpacing(8)
        
        # Find field
        fields_layout.addWidget(QLabel("Find what:"), 0, 0)
        self.find_line_edit = QLineEdit()
        self.find_line_edit.setMinimumHeight(30)
        fields_layout.addWidget(self.find_line_edit, 0, 1)
        
        # Replace field
        fields_layout.addWidget(QLabel("Replace with:"), 1, 0)
        self.replace_line_edit = QLineEdit()
        self.replace_line_edit.setMinimumHeight(30)
        fields_layout.addWidget(self.replace_line_edit, 1, 1)
        
        layout.addWidget(fields_group)
        
        # --- Options ---
        options_group = QGroupBox("Search Options")
        options_layout = QVBoxLayout(options_group)
        options_layout.setSpacing(5)
        
        self.case_sensitive_checkbox = QCheckBox("Match case")
        self.whole_word_checkbox = QCheckBox("Whole word only")
        
        options_layout.addWidget(self.case_sensitive_checkbox)
        options_layout.addWidget(self.whole_word_checkbox)
        
        layout.addWidget(options_group)
        
        # --- Search Scope ---
        scope_group = QGroupBox("Search Scope")
        scope_layout = QVBoxLayout(scope_group)
        
        self.scope_combo = QComboBox()
        self.scope_combo.addItem("Current Notes/Transcript", "current")
        self.scope_combo.addItem("All Notes/Transcripts", "all")
        scope_layout.addWidget(self.scope_combo)
        
        layout.addWidget(scope_group)
        
        # --- Progress Bar (hidden by default) ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_label = QLabel("")
        self.progress_label.setVisible(False)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.progress_bar)
        
        # --- Buttons ---
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(10)
        
        self.find_next_button = QPushButton("Find Next")
        self.find_all_button = QPushButton("Find All")
        self.replace_button = QPushButton("Replace")
        self.replace_all_button = QPushButton("Replace All")
        self.close_button = QPushButton("Close")
        
        # Set button sizes
        button_height = 35
        for btn in [self.find_next_button, self.find_all_button, self.replace_button, 
                   self.replace_all_button, self.close_button]:
            btn.setMinimumHeight(self.ui_scale_manager.scale_value(button_height))
            btn.setMinimumWidth(self.ui_scale_manager.scale_value(100))
        
        buttons_layout.addWidget(self.find_next_button)
        buttons_layout.addWidget(self.find_all_button)
        buttons_layout.addWidget(self.replace_button)
        buttons_layout.addWidget(self.replace_all_button)
        buttons_layout.addStretch()
        buttons_layout.addWidget(self.close_button)
        
        layout.addLayout(buttons_layout)
        
    def _apply_theme(self):
        """Apply the current theme to the dialog."""
        try:
            theme_qss = get_theme_qss(self.theme_name)
            
            # Additional styling for the dialog
            dialog_qss = f"""
            QDialog {{
                background-color: {THEME_PALETTE[self.theme_name]['primary_bg']};
                color: {THEME_PALETTE[self.theme_name]['primary_text']};
            }}
            
            QGroupBox {{
                font-weight: bold;
                border: 2px solid {THEME_PALETTE[self.theme_name]['panel_border']};
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }}
            
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
                color: {THEME_PALETTE[self.theme_name]['primary_text']};
            }}
            
            QLineEdit {{
                padding: 8px;
                border: 1px solid {THEME_PALETTE[self.theme_name]['panel_border']};
                border-radius: 4px;
                background-color: {THEME_PALETTE[self.theme_name]['secondary_bg']};
                color: {THEME_PALETTE[self.theme_name]['primary_text']};
                font-size: 14px;
            }}
            
            QLineEdit:focus {{
                border: 2px solid {THEME_PALETTE[self.theme_name]['accent']};
            }}
            
            QPushButton {{
                padding: 8px 16px;
                border: 1px solid {THEME_PALETTE[self.theme_name]['panel_border']};
                border-radius: 4px;
                background-color: {THEME_PALETTE[self.theme_name]['secondary_bg']};
                color: {THEME_PALETTE[self.theme_name]['primary_text']};
                font-weight: 500;
            }}
            
            QPushButton:hover {{
                background-color: {THEME_PALETTE[self.theme_name]['accent']};
                border-color: {THEME_PALETTE[self.theme_name]['accent']};
                color: {THEME_PALETTE[self.theme_name]['secondary_bg']};
            }}
            
            QPushButton:pressed {{
                background-color: {THEME_PALETTE[self.theme_name]['accent2']};
                color: {THEME_PALETTE[self.theme_name]['secondary_bg']};
            }}
            
            QPushButton:disabled {{
                background-color: {THEME_PALETTE[self.theme_name]['disabled_bg']};
                color: {THEME_PALETTE[self.theme_name]['disabled_text']};
                border-color: {THEME_PALETTE[self.theme_name]['disabled_bg']};
            }}
            
            QComboBox {{
                padding: 8px;
                border: 1px solid {THEME_PALETTE[self.theme_name]['panel_border']};
                border-radius: 4px;
                background-color: {THEME_PALETTE[self.theme_name]['secondary_bg']};
                color: {THEME_PALETTE[self.theme_name]['primary_text']};
            }}
            
            QCheckBox {{
                color: {THEME_PALETTE[self.theme_name]['primary_text']};
                spacing: 8px;
            }}
            
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border: 1px solid {THEME_PALETTE[self.theme_name]['panel_border']};
                border-radius: 3px;
                background-color: {THEME_PALETTE[self.theme_name]['secondary_bg']};
            }}
            
            QCheckBox::indicator:checked {{
                background-color: {THEME_PALETTE[self.theme_name]['accent']};
                border-color: {THEME_PALETTE[self.theme_name]['accent']};
            }}
            
            QProgressBar {{
                border: 1px solid {THEME_PALETTE[self.theme_name]['panel_border']};
                border-radius: 4px;
                text-align: center;
                background-color: {THEME_PALETTE[self.theme_name]['secondary_bg']};
                color: {THEME_PALETTE[self.theme_name]['primary_text']};
            }}
            
            QProgressBar::chunk {{
                background-color: {THEME_PALETTE[self.theme_name]['accent']};
                border-radius: 3px;
            }}
            """
            
            self.setStyleSheet(theme_qss + dialog_qss)
            
        except Exception as e:
            logger.error(f"Error applying theme to FindReplaceDialog: {e}")
    
    def _connect_signals(self):
        """Connect button signals to their handlers."""
        self.find_next_button.clicked.connect(self._find_next)
        self.find_all_button.clicked.connect(self._find_all)
        self.replace_button.clicked.connect(self._replace)
        self.replace_all_button.clicked.connect(self._replace_all)
        self.close_button.clicked.connect(self.reject)
        
        # Enable/disable buttons based on text input
        self.find_line_edit.textChanged.connect(self._update_button_states)
        self.scope_combo.currentTextChanged.connect(self._update_button_states)
        
        # Enter key in find field triggers find next
        self.find_line_edit.returnPressed.connect(self._find_next)
        self.replace_line_edit.returnPressed.connect(self._replace)
        
    def _update_button_states(self):
        """Enable/disable buttons based on current state."""
        has_search_text = bool(self.find_line_edit.text().strip())
        scope_is_current = self.scope_combo.currentData() == "current"
        has_text_edit = self.text_edit is not None
        
        # Find buttons
        self.find_next_button.setEnabled(has_search_text and scope_is_current and has_text_edit)
        self.find_all_button.setEnabled(has_search_text and scope_is_current and has_text_edit)
        
        # Replace buttons
        self.replace_button.setEnabled(has_search_text and scope_is_current and has_text_edit)
        self.replace_all_button.setEnabled(has_search_text)
        
    def _find_next(self):
        """Find the next occurrence of the search text."""
        if not self.text_edit or not self.find_line_edit.text().strip():
            return
            
        search_text = self.find_line_edit.text()
        flags = QTextDocument.FindFlag(0)
        
        if self.case_sensitive_checkbox.isChecked():
            flags |= QTextDocument.FindCaseSensitively
        if self.whole_word_checkbox.isChecked():
            flags |= QTextDocument.FindWholeWords
        
        cursor = self.text_edit.textCursor()
        found_cursor = self.text_edit.document().find(search_text, cursor, flags)
        
        if found_cursor.isNull():
            # Search from beginning
            found_cursor = self.text_edit.document().find(search_text, 0, flags)
            if found_cursor.isNull():
                QMessageBox.information(self, "Find", f"'{search_text}' not found.")
                return
        
        self.text_edit.setTextCursor(found_cursor)
        
    def _find_all(self):
        """Find and highlight all occurrences of the search text."""
        if not self.text_edit or not self.find_line_edit.text().strip():
            return
            
        search_text = self.find_line_edit.text()
        flags = QTextDocument.FindFlag(0)
        
        if self.case_sensitive_checkbox.isChecked():
            flags |= QTextDocument.FindCaseSensitively
        if self.whole_word_checkbox.isChecked():
            flags |= QTextDocument.FindWholeWords
        
        # Clear previous selections
        cursor = self.text_edit.textCursor()
        cursor.clearSelection()
        self.text_edit.setTextCursor(cursor)
        
        # Find all occurrences
        doc = self.text_edit.document()
        found_count = 0
        search_cursor = QTextCursor(doc)
        
        while True:
            found_cursor = doc.find(search_text, search_cursor, flags)
            if found_cursor.isNull():
                break
            found_count += 1
            search_cursor = found_cursor
        
        if found_count == 0:
            QMessageBox.information(self, "Find All", f"'{search_text}' not found.")
        else:
            # Highlight first occurrence
            first_cursor = doc.find(search_text, 0, flags)
            if not first_cursor.isNull():
                self.text_edit.setTextCursor(first_cursor)
            QMessageBox.information(self, "Find All", f"Found {found_count} occurrence(s) of '{search_text}'.")
        
    def _replace(self):
        """Replace the current selection if it matches the search text."""
        if not self.text_edit or not self.find_line_edit.text().strip():
            return
            
        cursor = self.text_edit.textCursor()
        if not cursor.hasSelection():
            self._find_next()
            return
            
        selected_text = cursor.selectedText()
        search_text = self.find_line_edit.text()
        replace_text = self.replace_line_edit.text()
        
        # Check if selection matches search text
        if self.case_sensitive_checkbox.isChecked():
            matches = selected_text == search_text
        else:
            matches = selected_text.lower() == search_text.lower()
            
        if matches:
            cursor.insertText(replace_text)
            # Find next occurrence
            self._find_next()
        else:
            self._find_next()
            
    def _replace_all(self):
        """Replace all occurrences based on the selected scope."""
        scope = self.scope_combo.currentData()
        
        if scope == "current":
            self._replace_all_current()
        elif scope == "all":
            self._replace_all_transcripts()
            
    def _replace_all_current(self):
        """Replace all occurrences in the current recording's notes and transcript."""
        if self.recording_id is None:
            QMessageBox.warning(self, "No Recording Selected", "No recording is currently selected to perform this operation.")
            return

        search_text = self.find_line_edit.text()
        if not search_text:
            return

        replace_text = self.replace_line_edit.text()
        case_sensitive = self.case_sensitive_checkbox.isChecked()
        whole_word = self.whole_word_checkbox.isChecked()
        
        total_replacements = 0
        
        try:
            # Get recording data
            recording = db_ops.get_recording_by_id(self.recording_id)
            if not recording:
                QMessageBox.critical(self, "Error", f"Could not find recording with ID: {self.recording_id}")
                return
            
            recording_dict = dict(recording)
            
            # 1. Process transcript in database
            if TranscriptStore.exists(self.recording_id, "diarized"):
                def replacement_fn(text):
                    return FindReplaceWorker._replace_in_text(
                        text, search_text, replace_text, case_sensitive, whole_word
                    )
                replacements = TranscriptStore.replace(self.recording_id, replacement_fn, "diarized")
                if replacements > 0:
                    total_replacements += replacements
                    logger.info(f"Made {replacements} replacements in diarized transcript for {self.recording_id}")

            # 2. Process meeting notes
            notes_entry = db_ops.get_meeting_notes_for_recording(self.recording_id)
            if notes_entry and notes_entry.get('notes'):
                notes_text = notes_entry['notes']
                new_notes, replacements = FindReplaceWorker._replace_in_text(
                    notes_text, search_text, replace_text, case_sensitive, whole_word
                )
                if replacements > 0:
                    db_ops.add_meeting_notes(
                        self.recording_id, 
                        notes_entry.get('provider', 'unknown'),
                        notes_entry.get('model', 'unknown'), 
                        new_notes
                    )
                    total_replacements += replacements
                    logger.info(f"Made {replacements} replacements in notes for {self.recording_id}")

            QMessageBox.information(self, "Replace All Complete", f"Made {total_replacements} replacements in the current notes and transcript.")
            self.bulk_operation_completed.emit()

        except Exception as e:
            logger.error(f"Error during 'replace all current': {e}", exc_info=True)
            QMessageBox.critical(self, "Error", f"An unexpected error occurred: {e}")

    def _replace_all_transcripts(self):
        """Replace all occurrences in all transcripts using a worker thread."""
        search_text = self.find_line_edit.text()
        replace_text = self.replace_line_edit.text()
        
        reply = QMessageBox.question(
            self, 
            "Replace All in Transcripts",
            f"This will search and replace '{search_text}' with '{replace_text}' across ALL meeting transcripts and notes.\n\n"
            f"This action cannot be undone. Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
            
        # Show progress
        self.progress_bar.setVisible(True)
        self.progress_label.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Starting bulk replace operation...")
        
        # Disable buttons during operation
        self._set_buttons_enabled(False)
        
        # Start worker thread
        self.worker = FindReplaceWorker(
            search_text, 
            replace_text,
            self.case_sensitive_checkbox.isChecked(),
            self.whole_word_checkbox.isChecked()
        )
        self.worker.progress_update.connect(self._update_progress)
        self.worker.finished.connect(self._replace_finished)
        self.worker.error.connect(self._replace_error)
        self.worker.start()
        
    def _update_progress(self, percentage: int, current_file: str):
        """Update progress bar and label."""
        self.progress_bar.setValue(percentage)
        self.progress_label.setText(f"Processing: {current_file}")
        
    def _replace_finished(self, total_replacements: int):
        """Handle completion of bulk replace operation."""
        self.progress_bar.setVisible(False)
        self.progress_label.setVisible(False)
        self._set_buttons_enabled(True)
        
        QMessageBox.information(
            self, 
            "Replace Complete", 
            f"Bulk replace operation completed.\n"
            f"Total replacements made: {total_replacements}"
        )
        
        self.worker = None
        
        # Emit signal to notify parent
        self.bulk_operation_completed.emit()
        
    def _replace_error(self, error_message: str):
        """Handle error in bulk replace operation."""
        self.progress_bar.setVisible(False)
        self.progress_label.setVisible(False)
        self._set_buttons_enabled(True)
        
        QMessageBox.warning(
            self, 
            "Replace Error", 
            f"An error occurred during the bulk replace operation:\n{error_message}"
        )
        
        self.worker = None
        
    def _set_buttons_enabled(self, enabled: bool):
        """Enable or disable all buttons."""
        for btn in [self.find_next_button, self.find_all_button, self.replace_button, 
                   self.replace_all_button]:
            btn.setEnabled(enabled)
            
    def closeEvent(self, event):
        """Handle dialog close event."""
        if self.worker and self.worker.isRunning():
            reply = QMessageBox.question(
                self,
                "Operation in Progress",
                "A bulk replace operation is in progress. Close anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.No:
                event.ignore()
                return
            # Terminate worker if user insists on closing
            self.worker.terminate()
            self.worker.wait(3000)  # Wait up to 3 seconds
            
        event.accept()
        
    def set_search_text(self, text: str):
        """Set the search text field."""
        self.find_line_edit.setText(text)
        self.find_line_edit.selectAll() 