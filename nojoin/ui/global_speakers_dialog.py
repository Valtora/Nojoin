from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, 
    QListWidget, QListWidgetItem, QMessageBox, QDialogButtonBox, QAbstractItemView
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon

from nojoin.db import database as db_ops
from nojoin.utils.theme_utils import apply_theme_to_widget, get_theme_qss, THEME_PALETTE # Assuming get_theme_qss might be used for specific parts
from nojoin.utils.config_manager import config_manager
from .search_bar_widget import SearchBarWidget # Import SearchBarWidget

import logging
logger = logging.getLogger(__name__)

class GlobalSpeakersManagementDialog(QDialog):
    global_speakers_updated = Signal() # Emitted when changes are made

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage Global Speaker Library")
        self.setMinimumWidth(450)
        self.setMinimumHeight(400)
        
        self.current_theme = config_manager.get("theme", "dark")
        apply_theme_to_widget(self, self.current_theme)

        self._init_ui()
        self._load_speakers()
        self._connect_signals()

    def _init_ui(self):
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setSpacing(10)

        # Instruction Label
        instruction_label = QLabel("Add, rename, or delete speakers in the global library. "
                                   "Deleting a global speaker will unlink them from specific meeting participants but will not delete those participants themselves.")
        instruction_label.setWordWrap(True)
        self.main_layout.addWidget(instruction_label)

        # --- Search Bar --- 
        self.search_bar = SearchBarWidget()
        self.search_bar.set_placeholder("Search global speakers...")
        # Apply theme-aware border styling to the search bar's line edit
        palette = THEME_PALETTE[self.current_theme]
        border_color = palette.get('panel_border', '#555555') # Default fallback
        search_bar_qss = f"""
            QLineEdit#SearchBarInput {{
                border: 1px solid {border_color};
                border-radius: 8px;
                padding: 4px 8px;
                background-color: {palette.get('input_bg', '#333333')};
                color: {palette.get('primary_text', '#FFFFFF')};
            }}
            QToolButton {{
                border: none;
                padding: 0px;
                margin-left: -2px; /* Adjust to align with LineEdit content area */
            }}
        """
        self.search_bar.setStyleSheet(search_bar_qss)
        self.main_layout.addWidget(self.search_bar)

        # List of global speakers
        self.speakers_list_widget = QListWidget()
        self.speakers_list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.main_layout.addWidget(self.speakers_list_widget, 1) # Give more stretch factor

        # Input and Add/Rename controls
        self.controls_layout = QHBoxLayout()
        self.name_input_edit = QLineEdit()
        self.name_input_edit.setPlaceholderText("Enter speaker name...")
        self.controls_layout.addWidget(self.name_input_edit, 1)

        self.add_button = QPushButton("Add")
        self.controls_layout.addWidget(self.add_button)
        
        self.main_layout.addLayout(self.controls_layout)

        # Rename and Delete in a separate row for clarity
        self.actions_layout = QHBoxLayout()
        self.actions_layout.addStretch(1) # Push buttons to the right

        self.rename_button = QPushButton("Rename Selected")
        self.rename_button.setEnabled(False)
        self.actions_layout.addWidget(self.rename_button)

        self.delete_button = QPushButton("Delete Selected")
        self.delete_button.setEnabled(False)
        self.actions_layout.addWidget(self.delete_button)
        
        self.main_layout.addLayout(self.actions_layout)


        # Dialog buttons (Done)
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close) # Changed to Close
        self.main_layout.addWidget(self.button_box)
        
        # Apply specific QSS if needed, or rely on global theme QSS
        # For example, to make QListWidget background match panel_bg
        palette = THEME_PALETTE[self.current_theme]
        list_widget_qss = f"""
            QListWidget {{
                background-color: {palette['secondary_bg']};
                border: 1px solid {palette['panel_border']};
                border-radius: 6px;
            }}
            QListWidget::item {{
                padding: 5px;
                color: {palette['primary_text']};
            }}
            QListWidget::item:selected {{
                background-color: {palette['accent']};
                color: {palette['secondary_bg'] if self.current_theme == 'dark' else '#FFFFFF'};
            }}
        """
        self.speakers_list_widget.setStyleSheet(list_widget_qss)

        self._all_speakers_cache = [] # For fuzzy searching

    def _reset_ui_after_operation(self):
        """Resets the UI to a neutral state after a successful operation."""
        self._load_speakers()
        self.name_input_edit.clear()
        # self.speakers_list_widget.clearSelection() is implicitly handled by _load_speakers -> clear()
        # _update_button_states() is called by _load_speakers(), ensuring a clean state.

    def _load_speakers(self):
        self.speakers_list_widget.clear()
        # self.name_input_edit.clear() # Clearing input handled by selection logic
        # self.rename_button.setEnabled(False) # Handled by _update_button_states
        # self.delete_button.setEnabled(False) # Handled by _update_button_states
        
        self._all_speakers_cache = db_ops.get_all_global_speakers()
        self._display_speakers(self._all_speakers_cache)
        self._update_button_states() # Ensure buttons are correct after load

    def _display_speakers(self, speakers_to_display):
        self.speakers_list_widget.clear() # Clear before repopulating
        if not speakers_to_display:
            self.speakers_list_widget.addItem(QListWidgetItem("No global speakers found."))
            # Disable selection-dependent buttons if list is empty or shows placeholder
            current_item_is_placeholder = self.speakers_list_widget.count() == 1 and \
                                          self.speakers_list_widget.item(0).data(Qt.ItemDataRole.UserRole) is None
            if current_item_is_placeholder or not speakers_to_display:
                 self.rename_button.setEnabled(False)
                 self.delete_button.setEnabled(False)
                 if self.speakers_list_widget.currentItem(): # If placeholder is selected, clear selection
                    self.speakers_list_widget.clearSelection()
            return

        for speaker in speakers_to_display:
            item = QListWidgetItem(speaker['name'])
            item.setData(Qt.ItemDataRole.UserRole, speaker['id']) # Store ID in item
            self.speakers_list_widget.addItem(item)
        
        # if not speakers_to_display: # This was already checked
        #     self.speakers_list_widget.addItem(QListWidgetItem("No global speakers yet."))

    def _connect_signals(self):
        self.search_bar.text_changed.connect(self._on_search_changed)
        self.search_bar.cleared.connect(self._load_speakers) # Reload all on clear
        self.speakers_list_widget.currentItemChanged.connect(self._on_speaker_selected)
        self.name_input_edit.textChanged.connect(self._on_name_input_changed)
        # self.name_input_edit.editingFinished.connect(self._check_rename_ eligibility) # May not be needed if textChanged covers it

        self.add_button.clicked.connect(self._on_add_clicked)
        self.rename_button.clicked.connect(self._on_rename_clicked)
        self.delete_button.clicked.connect(self._on_delete_clicked)
        
        self.button_box.rejected.connect(self.reject) # Close button triggers reject
        self.button_box.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)


    def _update_button_states(self):
        selected_item = self.speakers_list_widget.currentItem()
        is_item_really_selected = selected_item and selected_item.data(Qt.ItemDataRole.UserRole) is not None
        input_text = self.name_input_edit.text().strip()

        if is_item_really_selected:
            self.add_button.setText("Clear Selection")
            self.add_button.setEnabled(True) # Clear selection is always possible if an item is selected
            
            # Enable Rename if input text is not empty AND different from selected item's text
            can_rename = bool(input_text) and input_text != selected_item.text()
            self.rename_button.setEnabled(can_rename)
            self.delete_button.setEnabled(True)
        else:
            self.add_button.setText("Add")
            self.add_button.setEnabled(bool(input_text)) # Enable Add if text in input and nothing selected
            self.rename_button.setEnabled(False)
            self.delete_button.setEnabled(False)

    def _on_speaker_selected(self, current_item: QListWidgetItem, previous_item: QListWidgetItem):
        is_real_item = current_item and current_item.data(Qt.ItemDataRole.UserRole) is not None
        if is_real_item:
            self.name_input_edit.setText(current_item.text())
            # self.name_input_edit.setSelection(0, len(current_item.text())) # Optionally select all text
        else:
            # This case handles when selection is cleared or an invalid item (like "No speakers yet") is "selected"
            self.name_input_edit.clear() 
        self._update_button_states()


    def _on_name_input_changed(self, text: str):
        self._update_button_states()

    def _on_add_clicked(self):
        current_item = self.speakers_list_widget.currentItem()
        is_item_selected = current_item is not None and current_item.data(Qt.ItemDataRole.UserRole) is not None

        if is_item_selected: # Button acts as "Clear Selection / New"
            self.speakers_list_widget.clearSelection()
            # self.name_input_edit.clear() # Clearing is handled by _on_speaker_selected via currentItemChanged(None)
            # self.name_input_edit.setFocus() # Focus might be better handled by user clicking
            # self._update_button_states() # _on_speaker_selected will call this
            return

        name_to_add = self.name_input_edit.text().strip()
        if not name_to_add:
            QMessageBox.warning(self, "Input Error", "Speaker name cannot be empty.")
            return

        existing_speaker = db_ops.get_global_speaker_by_name(name_to_add)
        if existing_speaker:
            QMessageBox.information(self, "Duplicate", f"A global speaker named '{name_to_add}' already exists.")
            # Optionally select the existing one
            for i in range(self.speakers_list_widget.count()):
                item = self.speakers_list_widget.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == existing_speaker['id']:
                    self.speakers_list_widget.setCurrentItem(item)
                    break
            return

        new_id = db_ops.add_global_speaker(name_to_add)
        if new_id:
            self._reset_ui_after_operation()
            self.global_speakers_updated.emit()
            logger.info(f"Added global speaker: '{name_to_add}' (ID: {new_id})")
        else:
            QMessageBox.critical(self, "Database Error", f"Could not add global speaker '{name_to_add}'.")

    def _on_rename_clicked(self):
        selected_item = self.speakers_list_widget.currentItem()
        if not selected_item or selected_item.data(Qt.ItemDataRole.UserRole) is None:
            QMessageBox.warning(self, "Selection Error", "Please select a speaker to rename.")
            return

        speaker_id = selected_item.data(Qt.ItemDataRole.UserRole)
        new_name = self.name_input_edit.text().strip()
        original_name = selected_item.text()

        if not new_name:
            QMessageBox.warning(self, "Input Error", "New speaker name cannot be empty.")
            return
        
        if new_name == original_name:
            QMessageBox.information(self, "No Change", "The new name is the same as the original.")
            return

        # Check if new name conflicts with another existing global speaker
        existing_speaker_with_new_name = db_ops.get_global_speaker_by_name(new_name)
        if existing_speaker_with_new_name and existing_speaker_with_new_name['id'] != speaker_id:
            QMessageBox.warning(self, "Duplicate Name", f"Another global speaker named '{new_name}' already exists.")
            return

        if db_ops.update_global_speaker_name(speaker_id, new_name):
            self._reset_ui_after_operation()
            self.global_speakers_updated.emit()
            logger.info(f"Renamed global speaker ID {speaker_id} from '{original_name}' to '{new_name}'.")
        else:
            QMessageBox.critical(self, "Database Error", f"Could not rename global speaker '{original_name}'.")


    def _on_delete_clicked(self):
        selected_item = self.speakers_list_widget.currentItem()
        if not selected_item or selected_item.data(Qt.ItemDataRole.UserRole) is None:
            QMessageBox.warning(self, "Selection Error", "Please select a speaker to delete.")
            return

        speaker_id = selected_item.data(Qt.ItemDataRole.UserRole)
        speaker_name = selected_item.text()

        reply = QMessageBox.question(self, "Confirm Delete",
                                     f"Are you sure you want to delete the global speaker '{speaker_name}'?\n\n"
                                     f"This will unlink this global profile from any meeting participants currently associated with it. "
                                     f"The participants themselves (and their segments in meetings) will NOT be deleted.",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            if db_ops.delete_global_speaker(speaker_id):
                self._reset_ui_after_operation()
                self.global_speakers_updated.emit()
                logger.info(f"Deleted global speaker: '{speaker_name}' (ID: {speaker_id})")
            else:
                QMessageBox.critical(self, "Database Error", f"Could not delete global speaker '{speaker_name}'.")
                
    def reject(self):
        logger.debug("GlobalSpeakersManagementDialog rejected (closed via escape or system close).")
        super().reject()

    def accept(self):
        logger.debug("GlobalSpeakersManagementDialog accepted (closed via Done button).")
        super().accept()

    def _on_search_changed(self, query: str):
        if not query:
            self._display_speakers(self._all_speakers_cache)
            return

        from rapidfuzz import process, fuzz
        # Extract names for fuzzy matching
        speaker_names_map = {speaker['name']: speaker for speaker in self._all_speakers_cache}
        # Perform fuzzy search
        # We want results that are tuples of (string_matched, score, original_item_key)
        # Here, original_item_key will be the name itself.
        # Note: rapidfuzz.process.extract returns list of (value, score, key)
        # If choices is a dict, key is the dict key. If list, key is the index.
        # Here, speaker_names_map.keys() is a list of names.
        results = process.extract(query, list(speaker_names_map.keys()), scorer=fuzz.WRatio, limit=20, score_cutoff=60)
        
        filtered_speakers = []
        for name, score, _ in results: # name is the matched name from speaker_names_map.keys()
            if name in speaker_names_map: # Should always be true
                filtered_speakers.append(speaker_names_map[name])
        
        self._display_speakers(filtered_speakers)

# Example usage (for testing, not part of the class)
if __name__ == '__main__':
    import sys
    from PySide6.QtWidgets import QApplication
    # Ensure DB is initialized for standalone testing
    # You might need to adjust path if running this file directly
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    from nojoin.db.database import init_db, get_db_path
    
    # Create a dummy DB for testing if it doesn't exist
    if not os.path.exists(get_db_path()):
        print(f"Database not found at {get_db_path()}, initializing a new one for test.")
        try:
            init_db()
            # Add some test global speakers
            db_ops.add_global_speaker("Alice Wonderland")
            db_ops.add_global_speaker("Bob The Builder")
            db_ops.add_global_speaker("Charlie Brown")
        except Exception as e:
            print(f"Error initializing test DB: {e}")

    app = QApplication(sys.argv)
    dialog = GlobalSpeakersManagementDialog()
    dialog.show()
    sys.exit(app.exec()) 