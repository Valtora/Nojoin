from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QScrollArea, QWidget, QGridLayout, QMessageBox, QInputDialog, QDialogButtonBox, QSizePolicy, QStyle, QSlider, QCheckBox, QCompleter
from PySide6.QtCore import Qt, Signal, QSize, QTimer
from PySide6.QtGui import QIcon
import os
from nojoin.db import database as db_ops
from nojoin.utils.config_manager import config_manager, from_project_relative_path
import logging
from .playback_controller import PlaybackController
from nojoin.utils.theme_utils import apply_theme_to_widget
from nojoin.utils.ui_scale_manager import get_ui_scale_manager

logger = logging.getLogger(__name__)

class ParticipantsDialog(QDialog):
    participants_updated = Signal(str)  # recording_id (str)
    regenerate_notes_requested = Signal(str)  # recording_id (str) when notes should be regenerated

    def __init__(self, recording_id, recording_data, parent=None):
        super().__init__(parent)
        self.original_window_title = f"Manage Participants - {recording_data.get('name', 'Meeting')}"
        self.setWindowTitle(self.original_window_title)
        
        # Initialize ui_scale_manager here to ensure it's in proper scope
        self.ui_scale_manager = get_ui_scale_manager()
        min_width, _ = self.ui_scale_manager.get_scaled_minimum_sizes()['participants_dialog']
        self.setMinimumWidth(min_width)
        self.current_theme = getattr(parent, 'current_theme', 'dark') if parent else 'dark'
        apply_theme_to_widget(self, self.current_theme)
        self.recording_id = recording_id
        self.recording_data = recording_data
        self.layout = QVBoxLayout(self)
        self.speakers = db_ops.get_speakers_for_recording(recording_id)
        self.speaker_widgets = {}
        self._merge_mode = False
        self._merge_selected = set()
        self.snippet_player = PlaybackController()
        self._playing_speaker_id = None  # Track which speaker is currently playing
        self._speakers_modified = False  # Track if any changes were made
        self._global_speakers_cache = [] # Cache for global speaker names for completer
        self._just_linked_via_completer = None # Speaker_id that was just auto-linked
        self._load_global_speakers_cache() # Load once on init
        self._init_ui()

    def _clear_layout(self, layout):
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0) # Take item from layout
            widget = item.widget()
            if widget:
                widget.deleteLater() # Delete widget
            else:
                sub_layout = item.layout()
                if sub_layout:
                    self._clear_layout(sub_layout) # Recursively clear sub-layout

    def _update_window_title(self):
        title = self.original_window_title
        if self._speakers_modified:
            title += " *"
        self.setWindowTitle(title)

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
        self.grid.setVerticalSpacing(6)
        self._populate_speakers()
        scroll.setWidget(panel)
        self.layout.addWidget(scroll, 1)
        # Merge controls
        merge_row = QHBoxLayout()
        # Add Participant button
        self.add_participant_btn = QPushButton("Add Participant")
        self.add_participant_btn.clicked.connect(self.show_add_participant_dialog)
        merge_row.addWidget(self.add_participant_btn)
        # Merge controls
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
        # Clear existing items from the grid
        if hasattr(self, 'grid') and self.grid is not None: # Ensure grid exists
            self._clear_layout(self.grid) # Clear all items from the grid layout

        self.speaker_widgets.clear() # Clear the dictionary tracking widgets

        # Re-fetch speakers as they might have changed due to merge/delete operations
        self.speakers = db_ops.get_speakers_for_recording(self.recording_id)

        for idx, speaker in enumerate(self.speakers):
            diarization_label = speaker['diarization_label']
            speaker_id = speaker['id']
            
            # Fetch full speaker info including global link status
            speaker_full_info = db_ops.get_speaker_with_global_info(speaker_id)
            name = speaker_full_info.get('name') if speaker_full_info else f"Speaker {idx+1}"
            is_unknown = (name == "Unknown")
            global_speaker_name = speaker_full_info.get('global_speaker_name') if speaker_full_info else None

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
            play_btn.setFixedSize(18, 18)
            play_btn.setEnabled(not is_unknown)
            play_btn.setCheckable(True)
            play_btn.setProperty("speaker_id", speaker_id)
            play_btn.clicked.connect(lambda checked, s_id=speaker_id, btn=play_btn: self.toggle_speaker_snippet(s_id, btn))
            # Delete
            del_btn = QPushButton()
            del_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
            del_btn.setToolTip("Delete speaker" if not is_unknown else "Delete all 'Unknown' transcript lines")
            del_btn.setFixedSize(18, 18)
            if is_unknown:
                del_btn.setEnabled(True)
                del_btn.clicked.connect(lambda checked=False: self.delete_unknown_speaker())
            else:
                del_btn.setEnabled(True)
                del_btn.clicked.connect(lambda checked=False, s_id=speaker_id: self.delete_speaker(s_id))
            # Name edit
            name_edit = QLineEdit(name)
            name_edit.setMinimumWidth(self.ui_scale_manager.scale_value(120))
            name_edit.setProperty("speaker_id", speaker_id)
            name_edit.setProperty("diarization_label", diarization_label)
            name_edit.setEnabled(not is_unknown)
            name_edit.editingFinished.connect(lambda ne=name_edit: self.handle_speaker_name_editing_finished(ne))
            name_edit.setMinimumHeight(18)
            
            # Setup QCompleter for speaker name suggestions
            completer = QCompleter(self._global_speakers_cache, name_edit)
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setFilterMode(Qt.MatchContains)
            # Connect completer activated signal for auto-linking
            completer.activated[str].connect(lambda text, sid=speaker_id, ne=name_edit: self._handle_completer_activated(text, sid, ne))
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
            self.grid.addWidget(play_btn, idx, 0, alignment=Qt.AlignmentFlag.AlignCenter)
            self.grid.addWidget(del_btn, idx, 1, alignment=Qt.AlignmentFlag.AlignCenter)

            name_label_text = name
            if global_speaker_name:
                name_label_text += f" (Linked: {global_speaker_name})"
            
            # Instead of directly adding name_edit, create a layout for name and potential global indicator
            name_layout = QHBoxLayout()
            name_edit_widget = name_edit # Keep ref to the QLineEdit
            name_layout.addWidget(name_edit_widget)

            if global_speaker_name:
                global_indicator = QLabel("<small>🔗</small>") # Simple link emoji as icon
                global_indicator.setToolTip(f"Linked to Global Speaker: {global_speaker_name}")
                name_layout.addWidget(global_indicator)
            else:
                # Add a stretch or empty label to keep alignment if some have icon and others don't
                name_layout.addStretch(1)

            self.grid.addLayout(name_layout, idx, 2, alignment=Qt.AlignmentFlag.AlignCenter)
            self.grid.addWidget(merge_checkbox, idx, 3, alignment=Qt.AlignmentFlag.AlignCenter)
            self.speaker_widgets[speaker_id] = {
                'play_btn': play_btn,
                'del_btn': del_btn,
                'name_edit': name_edit_widget, # Use the actual QLineEdit widget here
                'merge_checkbox': merge_checkbox,
                'global_indicator': global_indicator if global_speaker_name else None, # Store for potential updates
                'name_layout': name_layout # Store the layout itself
            }

    def _refresh_speaker_row_ui(self, speaker_id):
        logger.debug(f"Refreshing UI for speaker_id: {speaker_id}")
        if speaker_id not in self.speaker_widgets:
            logger.warning(f"_refresh_speaker_row_ui: speaker_id {speaker_id} not in self.speaker_widgets.")
            return

        widgets = self.speaker_widgets[speaker_id]
        name_edit_widget = widgets['name_edit']
        name_layout = widgets['name_layout'] # Get the QHBoxLayout for the name and indicator

        speaker_full_info = db_ops.get_speaker_with_global_info(speaker_id)
        if not speaker_full_info:
            logger.warning(f"_refresh_speaker_row_ui: Could not get full info for speaker_id {speaker_id}.")
            # Clear global indicator if speaker info is gone
            if widgets.get('global_indicator'):
                widgets['global_indicator'].setVisible(False)
                widgets['global_indicator'].setToolTip("")
            return

        # Update name in QLineEdit (though it should be current from edit/completer)
        current_name_in_db = speaker_full_info.get('name', '')
        if name_edit_widget.text() != current_name_in_db:
            name_edit_widget.setText(current_name_in_db)

        global_speaker_name = speaker_full_info.get('global_speaker_name')

        # Remove old indicator if it exists
        if widgets.get('global_indicator'):
            widgets['global_indicator'].deleteLater()
            widgets['global_indicator'] = None
            # Remove it from layout if it was there - by finding it
            for i in range(name_layout.count()):
                item = name_layout.itemAt(i)
                if isinstance(item.widget(), QLabel) and "Linked to Global Speaker" in item.widget().toolTip():
                    name_layout.takeAt(i) # Remove item from layout
                    break # Assuming only one such indicator

        if global_speaker_name:
            if not widgets.get('global_indicator'): # Create new one if needed
                global_indicator = QLabel("<small>🔗</small>")
                global_indicator.setToolTip(f"Linked to Global Speaker: {global_speaker_name}")
                name_layout.addWidget(global_indicator) # Add to the existing name_layout
                widgets['global_indicator'] = global_indicator
            else: # Update existing one
                widgets['global_indicator'].setToolTip(f"Linked to Global Speaker: {global_speaker_name}")
                widgets['global_indicator'].setVisible(True)
        else: # No global link
            # Ensure any stretch is at the end or manage spacing
            # If indicator was removed, a stretch might be needed if not already last
            if name_layout.itemAt(name_layout.count() -1).spacerItem() is None : # if last item is not a spacer
                name_layout.addStretch(1)


        logger.debug(f"UI refreshed for speaker_id: {speaker_id}. Global name: {global_speaker_name}")


    def _handle_completer_activated(self, selected_text, speaker_id, name_edit_widget):
        logger.info(f"Completer activated for speaker_id {speaker_id} with text: '{selected_text}'")

        # Ensure the line edit displays the selected text
        name_edit_widget.setText(selected_text)

        global_speaker_match = db_ops.get_global_speaker_by_name(selected_text)

        if global_speaker_match:
            # Link the local speaker to the global one
            db_ops.link_speaker_to_global(speaker_id, global_speaker_match['id'])
            # Update the local speaker's name to match the global one, and save to DB
            db_ops.update_speaker_name(speaker_id, selected_text, self.recording_id)
            
            logger.info(f"Auto-linked speaker ID {speaker_id} to global '{selected_text}' (ID: {global_speaker_match['id']}) and updated local name via completer.")
            
            self._just_linked_via_completer = speaker_id  # Flag that completer handled linking
            self._speakers_modified = True
            self._update_window_title()
            self._refresh_speaker_row_ui(speaker_id)
            self.participants_updated.emit(self.recording_id) # Notify main window of change

            # Refresh global speaker cache and all completer models
            # This is important if linking might implicitly create/change global speaker list,
            # or just to ensure all other completers are up-to-date.
            self._load_global_speakers_cache()
            for sp_id_key, sp_widgets_val in self.speaker_widgets.items():
                if 'name_edit' in sp_widgets_val and sp_widgets_val['name_edit'].completer():
                    sp_widgets_val['name_edit'].completer().model().setStringList(self._global_speakers_cache)
        else:
            logger.warning(f"Completer selected text '{selected_text}' but no matching global speaker found. This should not happen if cache is correct.")
            # If this case occurs, it might mean the cache is stale or there's an issue.
            # For now, we'll let handle_speaker_name_editing_finished deal with it as a new name.
            self._just_linked_via_completer = None # Ensure it's not flagged as completer-linked

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
            if not rec or not rec.get('audio_path'):
                QMessageBox.warning(self, "Audio Missing", "Audio file path for this recording is missing.")
                button.setChecked(False)
                return
            
            # Convert relative path to absolute path for file access
            audio_path = from_project_relative_path(rec['audio_path'])
            if not os.path.exists(audio_path):
                QMessageBox.warning(self, "Audio Missing", f"Audio file for this recording is missing: {audio_path}")
                button.setChecked(False)
                return
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
        """Handles logic when a speaker name QLineEdit finishes editing. Auto-saves changes."""
        new_name = name_edit_widget.text().strip()
        speaker_id = name_edit_widget.property("speaker_id")


        logger.debug(f"handle_speaker_name_editing_finished for SID: {speaker_id}, new_name: '{new_name}'")

        if not new_name or not speaker_id:
            logger.debug("Name is empty or speaker_id missing, reverting/doing nothing.")
            # Optionally, revert to original name if new_name is empty
            # For now, if user clears the name, it might become an empty name.
            # Consider if this should revert to original diarization label or previous name.
            return

        # Get original name from DB to see if it actually changed
        original_speaker_data = db_ops.get_speaker_by_id(speaker_id)
        original_name_from_db = original_speaker_data['name'] if original_speaker_data else ""

        # If name hasn't changed from what's in DB, do nothing further unless completer just acted
        if new_name == original_name_from_db and self._just_linked_via_completer != speaker_id:
            logger.debug(f"Name '{new_name}' for SID {speaker_id} is same as in DB. No change.")
            return

        # --- Auto-save name to DB --- (Moved from _on_accept)
        db_ops.update_speaker_name(speaker_id, new_name, self.recording_id)
        logger.info(f"Auto-saved name for speaker ID {speaker_id} to '{new_name}'. Recording ID: {self.recording_id}")
        self._speakers_modified = True
        self._update_window_title()
        self.participants_updated.emit(self.recording_id) # Notify main window of name change

        # --- Global Speaker Linking/Creation Logic --- 
        # Skip if completer just handled linking for this speaker_id and the name matches the linked global name
        if self._just_linked_via_completer == speaker_id:
            global_info_after_completer = db_ops.get_speaker_with_global_info(speaker_id)
            if global_info_after_completer and global_info_after_completer.get('global_speaker_name') == new_name:
                logger.debug(f"Global linking for SID {speaker_id} was handled by completer. Skipping prompts.")
                self._just_linked_via_completer = None # Reset flag
                self._refresh_speaker_row_ui(speaker_id) # Ensure UI is up-to-date
                return 
        self._just_linked_via_completer = None # Reset flag if not returned above

        global_speaker_match = db_ops.get_global_speaker_by_name(new_name)
        current_link_info = db_ops.get_speaker_with_global_info(speaker_id)
        is_already_linked_to_this_global = False
        if global_speaker_match and current_link_info:
            is_already_linked_to_this_global = current_link_info.get('global_speaker_id') == global_speaker_match['id']

        if global_speaker_match and not is_already_linked_to_this_global:
            reply = QMessageBox.question(self, "Link to Global Speaker?", 
                                         f"The name '{new_name}' exists in the Global Speaker Library. Would you like to link this participant to '{global_speaker_match['name']}'?",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply == QMessageBox.Yes:
                db_ops.link_speaker_to_global(speaker_id, global_speaker_match['id'])
                logger.info(f"Linked local speaker ID {speaker_id} to global '{new_name}' (ID: {global_speaker_match['id']}).")
                self._load_global_speakers_cache()
                for _, widgets_dict in self.speaker_widgets.items(): # Use a different var name
                    if widgets_dict['name_edit'].completer():
                        widgets_dict['name_edit'].completer().model().setStringList(self._global_speakers_cache)
        elif not global_speaker_match:
            # Do not prompt to add to global library if name is like "Speaker X" or "SPEAKER_XX"
            import re
            if not re.match(r"^(SPEAKER|Speaker)[_ ]?\d+$", new_name):
                reply = QMessageBox.question(self, "Add to Global Speakers?",
                                            f"The name '{new_name}' is not in the Global Speaker Library. Would you like to add it and link this participant?",
                                            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                if reply == QMessageBox.Yes:
                    global_id = db_ops.add_global_speaker(new_name)
                    if global_id:
                        db_ops.link_speaker_to_global(speaker_id, global_id)
                        logger.info(f"Added '{new_name}' to Global Speaker Library (ID: {global_id}) and linked local speaker ID {speaker_id}.")
                        self._load_global_speakers_cache()
                        for _, widgets_dict in self.speaker_widgets.items(): # Use a different var name
                            if widgets_dict['name_edit'].completer():
                                widgets_dict['name_edit'].completer().model().setStringList(self._global_speakers_cache)
                    else:
                        QMessageBox.warning(self, "Error", f"Could not add '{new_name}' to the Global Speaker Library.")
            else:
                logger.debug(f"New name '{new_name}' matches generic pattern. Skipping 'Add to Global' prompt.")
        
        self._refresh_speaker_row_ui(speaker_id) # Refresh UI after potential linking changes

    def delete_speaker(self, speaker_id):
        reply = QMessageBox.question(self, "Delete Speaker", "Are you sure you want to delete this speaker?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        # Check for global link before deleting for enhanced confirmation (Step 3)
        speaker_info = db_ops.get_speaker_with_global_info(speaker_id)
        if speaker_info and speaker_info.get('global_speaker_id'):
            global_name = speaker_info.get('global_speaker_name', 'this global speaker')
            reply = QMessageBox.question(self, "Confirm Delete Linked Speaker",
                                         f"This speaker ('{speaker_info.get('name', 'Unknown')}') is linked to the global speaker '{global_name}'.\\n\\n"
                                         f"Deleting it here will only remove it from this recording and unlink it. "
                                         f"The global speaker entry '{global_name}' will remain in your library.\\n\\n"
                                         f"Do you want to proceed with deleting from this recording?",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return

        success = db_ops.delete_speaker_from_recording(self.recording_id, speaker_id)
        if not success:
            QMessageBox.critical(self, "Delete Speaker", "Failed to delete speaker from recording.")
            return
        self._speakers_modified = True
        self._update_window_title()
        self.speakers = db_ops.get_speakers_for_recording(self.recording_id)
        self._populate_speakers()
        self.participants_updated.emit(self.recording_id)

    def handle_merge_speakers(self):
        selected_ids = list(self._merge_selected)
        if len(selected_ids) < 2:
            QMessageBox.warning(self, "Merge Speakers", "Select at least two speakers to merge.")
            return

        # Create a list of display items and a mapping from the display item back to speaker_id
        prospective_targets = [] 

        for speaker_id_to_display in selected_ids:

            
            # if not base_speaker_data:
            #     logger.warning(f"Could not find base data for selected speaker ID {speaker_id_to_display} during merge prep.")
            #     continue

            # Get the current, potentially un-saved, name directly from the QLineEdit widget
            if speaker_id_to_display in self.speaker_widgets:
                name_edit_widget = self.speaker_widgets[speaker_id_to_display]['name_edit']
                new_display_name = name_edit_widget.text().strip()
            else: # Fallback logic in case widget not found
                base_speaker_data = next((s for s in self.speakers if s['id'] == speaker_id_to_display), None)
                new_display_name = base_speaker_data.get('name') or base_speaker_data.get('diarization_label') if base_speaker_data else "Unknown"

            display_item_string = f"{new_display_name} (ID {speaker_id_to_display})"
            prospective_targets.append({'text': display_item_string, 'id': speaker_id_to_display})

        if not prospective_targets:
            QMessageBox.critical(self, "Merge Speakers", "Could not prepare list of speakers for merging.")
            return

        item_texts_for_dialog = [pt['text'] for pt in prospective_targets]

        selected_item_text, ok = QInputDialog.getItem(self, "Select Target Speaker", 
                                                      "Merge into:", item_texts_for_dialog, 0, False)
        if not ok or not selected_item_text:
            return 

        target_speaker_id = None
        for pt in prospective_targets:
            if pt['text'] == selected_item_text:
                target_speaker_id = pt['id']
                break
        
        if target_speaker_id is None:
            QMessageBox.critical(self, "Merge Speakers", "Could not determine target speaker from selection.")
            return
        
        success = db_ops.merge_speakers_in_recording(self.recording_id, selected_ids, target_speaker_id)
        if not success:
            QMessageBox.critical(self, "Merge Speakers", "Failed to merge speakers.")
            return

        self._speakers_modified = True
        self._update_window_title()
        
        ids_merged_away = [sid for sid in selected_ids if sid != target_speaker_id]
        for merged_id in ids_merged_away:
            # if merged_id in self._pending_name_changes: # _pending_name_changes is removed for names
            #     del self._pending_name_changes[merged_id]
            #     logger.info(f"Removed pending name change for speaker ID {merged_id} as it was merged away.")
            pass # No action needed for pending name changes as they are auto-saved
        
        self.speakers = db_ops.get_speakers_for_recording(self.recording_id)
        self._populate_speakers() 
        
        self._merge_selected.clear()
        self.merge_btn.setEnabled(False) 
        
        self.participants_updated.emit(self.recording_id)

    def _on_accept(self):
        # Stop any playing snippet
        self.snippet_player.stop()
        self._playing_speaker_id = None
        
        # Capture whether we need to trigger meeting-note regeneration after closing
        should_regenerate = self._speakers_modified and self.regenerate_notes_checkbox.isChecked()

        # Emit update signal before closing so the main window can refresh immediately
        if self._speakers_modified:
            self.participants_updated.emit(self.recording_id)

        # Reset modified flag and update title prior to closing
        self._speakers_modified = False
        self._update_window_title()

        # Close the dialog first so it no longer blocks subsequent modal dialogs
        self.accept()

        # Defer regeneration request until the dialog has fully closed and the event
        # loop has unwound. This prevents the need for the user to press Save twice.
        if should_regenerate:
            QTimer.singleShot(0, lambda rid=self.recording_id: self.regenerate_notes_requested.emit(rid))

    def reject(self):
        # Stop any playing snippet
        self.snippet_player.stop()
        self._playing_speaker_id = None
        # Clear pending changes on cancel
        # self._pending_name_changes.clear() # No longer used for names, and attribute removed
        self._speakers_modified = False # Reset on cancel
        self._update_window_title() # Update title to remove asterisk
        super().reject()

    def closeEvent(self, event):
        self.snippet_player.stop()
        self._playing_speaker_id = None
        if self._speakers_modified:
            reply = QMessageBox.question(self, "Unsaved Changes", 
                                         "You have unsaved changes. Do you want to save them before closing?",
                                         QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, 
                                         QMessageBox.Save)
            if reply == QMessageBox.Save:
                self._on_accept() # Calls accept which also handles title update
                event.accept()
            elif reply == QMessageBox.Discard:
                self._speakers_modified = False # Ensure it's reset
                self._update_window_title() # Remove asterisk
                event.accept()
            else: # Cancel
                event.ignore()
                return
        else:
            event.accept()
        # Clear pending changes on close
        # self._pending_name_changes.clear() # No longer used for names, and attribute removed

        # self._update_window_title() # Already handled
        super().closeEvent(event) # Call super only if event is accepted

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
        self._update_window_title()
        self.speakers = db_ops.get_speakers_for_recording(self.recording_id)
        self._populate_speakers()
        self.participants_updated.emit(self.recording_id)

    # ------------------------- NEW METHODS -------------------------
    def show_add_participant_dialog(self):
        """Prompt user for a new participant name and create a silent participant entry."""
        name, ok = QInputDialog.getText(self, "Add Participant", "Participant name:")
        if not ok or not name.strip():
            return  # Cancelled or empty

        name = name.strip()

        # Avoid duplicate names within this recording
        current_names = [widgets['name_edit'].text().strip().lower() for widgets in self.speaker_widgets.values()]
        if name.lower() in current_names:
            QMessageBox.information(self, "Duplicate Participant", f"A participant named '{name}' already exists in this recording.")
            return

        self._add_participant(name)

    def _add_participant(self, name: str):
        """Create a new silent participant (no diarization lines) and associate with the recording."""
        # Generate a unique synthetic diarization label (SILENT_XX)
        existing_labels = {s['diarization_label'] for s in self.speakers}
        idx = 0
        while True:
            label_candidate = f"SILENT_{idx:02d}"
            if label_candidate not in existing_labels:
                break
            idx += 1

        # Create or fetch the speaker row
        speaker_info = db_ops.get_or_create_speaker(name)
        if not speaker_info or 'id' not in speaker_info:
            QMessageBox.critical(self, "Error", "Failed to create participant in database.")
            return

        if not db_ops.associate_speaker_with_recording(self.recording_id, speaker_info['id'], label_candidate):
            QMessageBox.critical(self, "Error", "Failed to associate participant with recording.")
            return

        # Mark modified, refresh UI
        self._speakers_modified = True
        self._update_window_title()

        # Refresh speaker list and UI
        self.speakers = db_ops.get_speakers_for_recording(self.recording_id)
        self._populate_speakers()
        self.participants_updated.emit(self.recording_id) 