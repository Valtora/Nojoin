from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QScrollArea, QWidget, QGridLayout, QMessageBox, QInputDialog, QDialogButtonBox, QSizePolicy, QStyle, QSlider, QCheckBox, QCompleter
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QIcon
import os
from nojoin.db import database as db_ops
from nojoin.utils.config_manager import config_manager, from_project_relative_path
import logging
from .playback_controller import PlaybackController
from nojoin.utils.theme_utils import apply_theme_to_widget

logger = logging.getLogger(__name__)

class ParticipantsDialog(QDialog):
    participants_updated = Signal(str)  # recording_id (str)
    regenerate_notes_requested = Signal(str)  # recording_id (str) when notes should be regenerated

    def __init__(self, recording_id, recording_data, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Manage Participants - {recording_data.get('name', 'Meeting')}")
        self.setMinimumWidth(500)
        from nojoin.utils.config_manager import config_manager
        self.current_theme = config_manager.get("theme", "dark") # Store theme
        apply_theme_to_widget(self, self.current_theme)
        self.recording_id = recording_id
        self.recording_data = recording_data
        self.layout = QVBoxLayout(self)
        self.speakers = db_ops.get_speakers_for_recording(recording_id)
        self.speaker_widgets = {}
        self._merge_mode = False
        self._merge_selected = set()
        self.snippet_player = PlaybackController()
        self._pending_name_changes = {}  # speaker_id -> (diarization_label, new_name)
        self._playing_speaker_id = None  # Track which speaker is currently playing
        self._speakers_modified = False  # Track if any changes were made
        self._global_speakers_cache = [] # Cache for global speaker names for completer
        self._load_global_speakers_cache() # Load once on init
        self._init_ui()

    def _load_global_speakers_cache(self):
        self._global_speakers_cache = [gs['name'] for gs in db_ops.get_all_global_speakers()]

    def _init_ui(self):
        # Title
        title = QLabel("<b>Participants</b>")
        self.layout.addWidget(title)
        # Scroll area for speakers
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        panel = QWidget()
        self.grid = QGridLayout(panel)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(4)
        self._populate_speakers()
        scroll.setWidget(panel)
        self.layout.addWidget(scroll, 1)
        # Merge controls
        merge_row = QHBoxLayout()
        self.merge_mode_btn = QPushButton("Enable Merge Mode")
        self.merge_mode_btn.setCheckable(True)
        self.merge_mode_btn.toggled.connect(self.toggle_merge_mode)
        self.merge_btn = QPushButton("Merge Selected")
        self.merge_btn.setEnabled(False)
        self.merge_btn.clicked.connect(self.handle_merge_speakers)
        merge_row.addWidget(self.merge_mode_btn)
        merge_row.addWidget(self.merge_btn)
        merge_row.addStretch(1)
        self.layout.addLayout(merge_row)
        
        # Regenerate notes checkbox
        self.regenerate_notes_checkbox = QCheckBox("Regenerate meeting notes after saving")
        self.regenerate_notes_checkbox.setChecked(True)  # Default to regenerating notes
        self.layout.addWidget(self.regenerate_notes_checkbox)
        
        # Save/Cancel
        btn_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        self.layout.addWidget(btn_box)

    def _populate_speakers(self):
        # Remove old widgets
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.speaker_widgets = {}
        for idx, speaker in enumerate(self.speakers):
            diarization_label = speaker['diarization_label']
            speaker_id = speaker['id']
            name = speaker.get('name') or f"Speaker {idx+1}"
            is_unknown = (name == "Unknown")
            # Only show 'Unknown' if it actually exists in the DB for this recording
            if is_unknown:
                # Check if there are any transcript lines for 'Unknown' (i.e., if the speaker is present in DB)
                # If not, skip rendering this row
                # (We rely on the DB query to only return 'Unknown' if it exists, so this is just for clarity)
                pass
            # Play/Stop
            play_btn = QPushButton()
            play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            play_btn.setToolTip("Play snippet")
            play_btn.setFixedSize(22, 22)
            play_btn.setEnabled(not is_unknown)
            play_btn.setCheckable(True)
            play_btn.setProperty("speaker_id", speaker_id)
            play_btn.clicked.connect(lambda checked, s_id=speaker_id, btn=play_btn: self.toggle_speaker_snippet(s_id, btn))
            # Delete
            del_btn = QPushButton()
            del_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
            del_btn.setToolTip("Delete speaker" if not is_unknown else "Delete all 'Unknown' transcript lines")
            del_btn.setFixedSize(22, 22)
            if is_unknown:
                del_btn.setEnabled(True)
                del_btn.clicked.connect(lambda checked=False: self.delete_unknown_speaker())
            else:
                del_btn.setEnabled(True)
                del_btn.clicked.connect(lambda checked=False, s_id=speaker_id: self.delete_speaker(s_id))
            # Name edit
            name_edit = QLineEdit(name)
            name_edit.setMinimumWidth(120)
            name_edit.setProperty("speaker_id", speaker_id)
            name_edit.setProperty("diarization_label", diarization_label)
            name_edit.setEnabled(not is_unknown)
            name_edit.editingFinished.connect(lambda ne=name_edit: self.handle_speaker_name_editing_finished(ne))
            
            # Setup QCompleter for speaker name suggestions
            completer = QCompleter(self._global_speakers_cache, name_edit)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setFilterMode(Qt.MatchContains)
            name_edit.setCompleter(completer)
            
            # Merge checkbox
            merge_checkbox = QCheckBox("")
            # Apply themed stylesheet for visibility
            if self.current_theme == "dark":
                checkbox_border_color = "#ff9800" # Orange for dark theme
            else:
                checkbox_border_color = "#007bff" # Blue for light theme
            
            merge_checkbox.setStyleSheet(f"""
                QCheckBox::indicator {{
                    border: 1px solid {checkbox_border_color};
                    width: 13px;
                    height: 13px;
                    border-radius: 3px;
                }}
                QCheckBox::indicator:checked {{
                    background-color: {checkbox_border_color};
                    image: url(none); 
                }}
                QCheckBox::indicator:unchecked {{
                    background-color: transparent;
                }}
            """)
            merge_checkbox.setVisible(self._merge_mode and not is_unknown)
            merge_checkbox.setEnabled(not is_unknown)
            merge_checkbox.toggled.connect(lambda checked, s_id=speaker_id: self.handle_merge_checkbox(s_id, checked))
            # Add to grid
            self.grid.addWidget(play_btn, idx, 0)
            self.grid.addWidget(del_btn, idx, 1)
            self.grid.addWidget(name_edit, idx, 2)
            self.grid.addWidget(merge_checkbox, idx, 3)
            self.speaker_widgets[speaker_id] = {
                'play_btn': play_btn,
                'del_btn': del_btn,
                'name_edit': name_edit,
                'merge_checkbox': merge_checkbox,
            }

    def toggle_merge_mode(self, checked):
        self._merge_mode = checked
        for widgets in self.speaker_widgets.values():
            widgets['merge_checkbox'].setVisible(checked)
            widgets['merge_checkbox'].setChecked(False)
        self._merge_selected = set()
        self.merge_btn.setEnabled(False)

    def handle_merge_checkbox(self, speaker_id, checked):
        if checked:
            self._merge_selected.add(speaker_id)
        else:
            self._merge_selected.discard(speaker_id)
        self.merge_btn.setEnabled(len(self._merge_selected) >= 2)

    def toggle_speaker_snippet(self, speaker_id, button):
        try:
            # If this speaker is already playing, stop it
            if self._playing_speaker_id == speaker_id:
                self.snippet_player.stop()
                self._playing_speaker_id = None
                button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
                button.setToolTip("Play snippet")
                button.setChecked(False)
                return
            
            # Stop any other playing snippet
            if self._playing_speaker_id is not None:
                # Find and reset the previous playing button
                if self._playing_speaker_id in self.speaker_widgets:
                    prev_btn = self.speaker_widgets[self._playing_speaker_id]['play_btn']
                    prev_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
                    prev_btn.setToolTip("Play snippet")
                    prev_btn.setChecked(False)
                self.snippet_player.stop()
            
            # Play the new snippet
            speaker_data = db_ops.get_speaker_by_id(speaker_id)
            rec = db_ops.get_recording_by_id(self.recording_id)
            if not rec or not rec.get('audio_path') or not os.path.exists(rec['audio_path']):
                QMessageBox.warning(self, "Audio Missing", "Audio file for this recording is missing.")
                button.setChecked(False)
                return
            audio_path = rec['audio_path']
            with db_ops.get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT snippet_start, snippet_end FROM recording_speakers WHERE recording_id = ? AND speaker_id = ?", (self.recording_id, speaker_id))
                row = cursor.fetchone()
                if not row or row['snippet_start'] is None or row['snippet_end'] is None:
                    QMessageBox.warning(self, "Snippet Missing", "No snippet segment available for this speaker. Please re-process the recording.")
                    button.setChecked(False)
                    return
                snippet_start = row['snippet_start']
                snippet_end = row['snippet_end']
            duration = snippet_end - snippet_start
            if duration <= 0:
                QMessageBox.warning(self, "Snippet Error", "Invalid snippet segment duration.")
                button.setChecked(False)
                return
            max_duration = 10.0
            if duration > max_duration:
                duration = max_duration
            
            # Play the snippet and update button state
            self.snippet_player.play(audio_path, start_time=snippet_start, duration=duration)
            self._playing_speaker_id = speaker_id
            button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
            button.setToolTip("Stop snippet")
            
            # Connect to playback finished signal to reset button when snippet ends
            self.snippet_player.playback_finished.connect(lambda: self._on_snippet_finished(speaker_id))
        except Exception as e:
            QMessageBox.warning(self, "Playback Error", f"Failed to play speaker snippet: {str(e)}")
            button.setChecked(False)
    
    def _on_snippet_finished(self, speaker_id):
        """Reset the button state when snippet finishes playing"""
        if self._playing_speaker_id == speaker_id and speaker_id in self.speaker_widgets:
            play_btn = self.speaker_widgets[speaker_id]['play_btn']
            play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            play_btn.setToolTip("Play snippet")
            play_btn.setChecked(False)
            self._playing_speaker_id = None

    def handle_speaker_name_editing_finished(self, name_edit_widget: QLineEdit):
        """Handles logic when a speaker name QLineEdit finishes editing."""
        new_name = name_edit_widget.text().strip()
        speaker_id = name_edit_widget.property("speaker_id")
        diarization_label = name_edit_widget.property("diarization_label")

        if not new_name or not speaker_id:
            # If name cleared, or invalid speaker_id, revert to original or do nothing
            # For now, let's fetch original name and revert if new_name is empty
            # This needs robust handling of what "original" means (from DB at dialog load)
            return

        # Check against existing local name for this speaker_id to see if it actually changed
        original_speaker_data = db_ops.get_speaker_by_id(speaker_id)
        original_name = original_speaker_data['name'] if original_speaker_data else diarization_label

        if new_name == original_name:
            # If name hasn't changed from what's in DB, clear from pending and do nothing further
            if speaker_id in self._pending_name_changes:
                del self._pending_name_changes[speaker_id]
            return

        # Name has changed from original, stage it for saving
        self._pending_name_changes[speaker_id] = (diarization_label, new_name)
        self._speakers_modified = True

        # Now, handle global speaker linking/creation logic
        # Check if new_name matches an existing global speaker
        global_speaker_match = db_ops.get_global_speaker_by_name(new_name)

        if global_speaker_match:
            # Exact match found in global library
            reply = QMessageBox.question(self, "Link to Global Speaker?", 
                                         f"The name '{new_name}' exists in the Global Speaker Library. Would you like to link this participant to '{global_speaker_match['name']}'?",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply == QMessageBox.Yes:
                db_ops.link_speaker_to_global(speaker_id, global_speaker_match['id'])
                logger.info(f"Linked local speaker ID {speaker_id} to global speaker ID {global_speaker_match['id']} ('{global_speaker_match['name']}')")
                # Refresh completer cache in case this was a new global name added via another dialog instance (unlikely here but good practice)
                self._load_global_speakers_cache()
                for _, widgets in self.speaker_widgets.items():
                    if widgets['name_edit'].completer():
                        widgets['name_edit'].completer().model().setStringList(self._global_speakers_cache)
        else:
            # No exact match in global library
            reply = QMessageBox.question(self, "Add to Global Speakers?",
                                         f"The name '{new_name}' is not in the Global Speaker Library. Would you like to add it and link this participant?",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply == QMessageBox.Yes:
                global_id = db_ops.add_global_speaker(new_name)
                if global_id:
                    db_ops.link_speaker_to_global(speaker_id, global_id)
                    logger.info(f"Added '{new_name}' to Global Speaker Library (ID: {global_id}) and linked local speaker ID {speaker_id}.")
                    self._load_global_speakers_cache() # Refresh cache
                    for _, widgets in self.speaker_widgets.items():
                         if widgets['name_edit'].completer():
                            widgets['name_edit'].completer().model().setStringList(self._global_speakers_cache)
                else:
                    QMessageBox.warning(self, "Error", f"Could not add '{new_name}' to the Global Speaker Library.")

    def delete_speaker(self, speaker_id):
        reply = QMessageBox.question(self, "Delete Speaker", "Are you sure you want to delete this speaker?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        success = db_ops.delete_speaker_from_recording(self.recording_id, speaker_id)
        if not success:
            QMessageBox.critical(self, "Delete Speaker", "Failed to delete speaker from recording.")
            return
        self._speakers_modified = True
        self.speakers = db_ops.get_speakers_for_recording(self.recording_id)
        self._populate_speakers()
        self.participants_updated.emit(self.recording_id)

    def handle_merge_speakers(self):
        selected_ids = list(self._merge_selected)
        if len(selected_ids) < 2:
            QMessageBox.warning(self, "Merge Speakers", "Select at least two speakers to merge.")
            return
        speaker_map = {s['id']: s for s in self.speakers if s['id'] in selected_ids}
        items = [f"{s['name'] or s['diarization_label']} (ID {sid})" for sid, s in speaker_map.items()]
        target_idx, ok = QInputDialog.getItem(self, "Select Target Speaker", "Merge into:", items, 0, False)
        if not ok:
            return
        for sid, s in speaker_map.items():
            label = f"{s['name'] or s['diarization_label']} (ID {sid})"
            if label == target_idx:
                target_speaker_id = sid
                break
        else:
            QMessageBox.warning(self, "Merge Speakers", "Could not determine target speaker.")
            return
        success = db_ops.merge_speakers_in_recording(self.recording_id, selected_ids, target_speaker_id)
        if not success:
            QMessageBox.critical(self, "Merge Speakers", "Failed to merge speakers.")
            return
        self._speakers_modified = True
        self.speakers = db_ops.get_speakers_for_recording(self.recording_id)
        self._populate_speakers()
        self.participants_updated.emit(self.recording_id)

    def _on_accept(self):
        # Stop any playing snippet
        self.snippet_player.stop()
        self._playing_speaker_id = None
        
        # Apply all pending name changes and add to global library if appropriate
        names_added_to_global = set()
        for speaker_id, (diarization_label, new_name) in self._pending_name_changes.items():
            # Update the local speaker name and transcript
            db_ops.update_speaker_name(speaker_id, new_name, self.recording_id)
            
            # Add to global library if it's not a generic label (like SPEAKER_XX)
            # and not already processed (to avoid multiple popups for the same new name if it's used for multiple local speakers)
            import re
            if not re.match(r"^(SPEAKER|Speaker)[_ ]?\\d+$", new_name) and new_name not in names_added_to_global:
                existing_global = db_ops.get_global_speaker_by_name(new_name)
                if not existing_global:
                    global_id = db_ops.add_global_speaker(new_name)
                    if global_id:
                        db_ops.link_speaker_to_global(speaker_id, global_id) # Link this specific instance
                        logger.info(f"Auto-added '{new_name}' to Global Library (ID: {global_id}) and linked local speaker {speaker_id}.")
                        names_added_to_global.add(new_name)
                        self._speakers_modified = True # Ensure flag is set if global add happened
                    else:
                        logger.warning(f"Failed to auto-add '{new_name}' to Global Library from ParticipantsDialog.")
                elif existing_global: # Global speaker with this name already exists, link if not already
                    # Check if this specific local speaker_id is already linked to this global_id or any global_id
                    current_link = db_ops.get_speaker_with_global_info(speaker_id)
                    if not current_link or current_link.get('global_speaker_id') != existing_global['id']:
                        db_ops.link_speaker_to_global(speaker_id, existing_global['id'])
                        logger.info(f"Auto-linked local speaker {speaker_id} to existing Global Speaker '{new_name}' (ID: {existing_global['id']}).")
                        self._speakers_modified = True # Ensure flag is set if linking happened

        self._pending_name_changes.clear()
        
        if self._speakers_modified: # Check if any modifications were made (local or global)
            self.participants_updated.emit(self.recording_id)
            if self.regenerate_notes_checkbox.isChecked():
                self.regenerate_notes_requested.emit(self.recording_id)
        
        self.accept()

    def reject(self):
        # Stop any playing snippet
        self.snippet_player.stop()
        self._playing_speaker_id = None
        # Clear pending changes on cancel
        self._pending_name_changes.clear()
        super().reject()

    def closeEvent(self, event):
        self.snippet_player.stop()
        self._playing_speaker_id = None
        # Clear pending changes on close
        self._pending_name_changes.clear()
        super().closeEvent(event)

    def delete_unknown_speaker(self):
        from nojoin.db import database as db_ops
        reply = QMessageBox.question(self, "Delete 'Unknown' Speaker", "Are you sure you want to delete all transcript lines attributed to 'Unknown'?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        success = db_ops.delete_unknown_speaker_from_recording(self.recording_id)
        if not success:
            QMessageBox.critical(self, "Delete 'Unknown' Speaker", "Failed to delete 'Unknown' speaker from recording.")
            return
        self._speakers_modified = True
        self.speakers = db_ops.get_speakers_for_recording(self.recording_id)
        self._populate_speakers()
        self.participants_updated.emit(self.recording_id) 