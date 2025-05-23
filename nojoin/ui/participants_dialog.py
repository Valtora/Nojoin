from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QScrollArea, QWidget, QGridLayout, QMessageBox, QInputDialog, QDialogButtonBox, QSizePolicy, QStyle, QSlider
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QIcon
import os
from nojoin.db import database as db_ops
from nojoin.utils.config_manager import config_manager, from_project_relative_path
import logging
from .playback_controller import PlaybackController

logger = logging.getLogger(__name__)

class ParticipantsDialog(QDialog):
    participants_updated = Signal(int)  # recording_id

    def __init__(self, recording_id, recording_data, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Manage Participants - {recording_data.get('name', 'Meeting')}")
        self.setMinimumWidth(500)
        self.recording_id = recording_id
        self.recording_data = recording_data
        self.layout = QVBoxLayout(self)
        self.speakers = db_ops.get_speakers_for_recording(recording_id)
        self.speaker_widgets = {}
        self._merge_mode = False
        self._merge_selected = set()
        self.snippet_player = PlaybackController()
        self._pending_name_changes = {}  # speaker_id -> (diarization_label, new_name)
        self._init_ui()

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
        # Save/Cancel
        btn_box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        self.layout.addWidget(btn_box)
        # Add snippet playback bar at the bottom
        playback_bar = QHBoxLayout()
        self.snippet_play_btn = QPushButton()
        self.snippet_play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.snippet_play_btn.setToolTip("Play Snippet")
        self.snippet_play_btn.setEnabled(False)
        self.snippet_play_btn.clicked.connect(self._play_current_snippet)
        self.snippet_stop_btn = QPushButton()
        self.snippet_stop_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.snippet_stop_btn.setToolTip("Stop Snippet")
        self.snippet_stop_btn.setEnabled(False)
        self.snippet_stop_btn.clicked.connect(self._stop_current_snippet)
        self.snippet_volume_slider = QSlider(Qt.Horizontal)
        self.snippet_volume_slider.setMinimum(0)
        self.snippet_volume_slider.setMaximum(100)
        self.snippet_volume_slider.setValue(75)
        self.snippet_volume_slider.setFixedWidth(100)
        self.snippet_volume_slider.valueChanged.connect(self._set_snippet_volume)
        playback_bar.addWidget(self.snippet_play_btn)
        playback_bar.addWidget(self.snippet_stop_btn)
        playback_bar.addWidget(QLabel("Volume:"))
        playback_bar.addWidget(self.snippet_volume_slider)
        playback_bar.addStretch(1)
        self.layout.addLayout(playback_bar)
        self._current_snippet = None

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
            # Play
            play_btn = QPushButton()
            play_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
            play_btn.setToolTip("Play snippet")
            play_btn.setFixedSize(22, 22)
            play_btn.setEnabled(not is_unknown)
            play_btn.clicked.connect(lambda checked=False, s_id=speaker_id: self.play_speaker_snippet(s_id))
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
            name_edit.editingFinished.connect(lambda ne=name_edit: self.save_speaker_name(ne))
            # Merge checkbox
            merge_checkbox = QPushButton("Select")
            merge_checkbox.setCheckable(True)
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

    def play_speaker_snippet(self, speaker_id):
        try:
            speaker_data = db_ops.get_speaker_by_id(speaker_id)
            rec = db_ops.get_recording_by_id(self.recording_id)
            if not rec or not rec.get('audio_path') or not os.path.exists(rec['audio_path']):
                QMessageBox.warning(self, "Audio Missing", "Audio file for this recording is missing.")
                return
            audio_path = rec['audio_path']
            with db_ops.get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT snippet_start, snippet_end FROM recording_speakers WHERE recording_id = ? AND speaker_id = ?", (self.recording_id, speaker_id))
                row = cursor.fetchone()
                if not row or row['snippet_start'] is None or row['snippet_end'] is None:
                    QMessageBox.warning(self, "Snippet Missing", "No snippet segment available for this speaker. Please re-process the recording.")
                    return
                snippet_start = row['snippet_start']
                snippet_end = row['snippet_end']
            duration = snippet_end - snippet_start
            if duration <= 0:
                QMessageBox.warning(self, "Snippet Error", "Invalid snippet segment duration.")
                return
            max_duration = 10.0
            if duration > max_duration:
                duration = max_duration
            # Prepare snippet for playback bar
            self._current_snippet = (audio_path, snippet_start, duration)
            self.snippet_play_btn.setEnabled(True)
            self.snippet_stop_btn.setEnabled(True)
            self._play_current_snippet()
        except Exception as e:
            QMessageBox.warning(self, "Playback Error", f"Failed to play speaker snippet: {str(e)}")

    def _play_current_snippet(self):
        if self._current_snippet:
            audio_path, start, duration = self._current_snippet
            self.snippet_player.play(audio_path, start_time=start, duration=duration)

    def _stop_current_snippet(self):
        self.snippet_player.stop()

    def _set_snippet_volume(self, value):
        self.snippet_player.set_volume(value / 100.0)

    def save_speaker_name(self, name_edit):
        new_name = name_edit.text().strip()
        speaker_id = name_edit.property("speaker_id")
        diarization_label = name_edit.property("diarization_label")
        if not new_name or not speaker_id or not diarization_label:
            return
        speaker = db_ops.get_speaker_by_id(speaker_id)
        old_name = speaker.get('name') if speaker else None
        if new_name == old_name:
            if speaker_id in self._pending_name_changes:
                del self._pending_name_changes[speaker_id]
            return
        self._pending_name_changes[speaker_id] = (diarization_label, new_name)

    def delete_speaker(self, speaker_id):
        reply = QMessageBox.question(self, "Delete Speaker", "Are you sure you want to delete this speaker?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        success = db_ops.delete_speaker_from_recording(self.recording_id, speaker_id)
        if not success:
            QMessageBox.critical(self, "Delete Speaker", "Failed to delete speaker from recording.")
            return
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
        self.speakers = db_ops.get_speakers_for_recording(self.recording_id)
        self._populate_speakers()
        self.participants_updated.emit(self.recording_id)

    def _on_accept(self):
        # Apply all pending name changes
        for speaker_id, (diarization_label, new_name) in self._pending_name_changes.items():
            db_ops.update_speaker_name(speaker_id, new_name, self.recording_id)
        self._pending_name_changes.clear()
        self.participants_updated.emit(self.recording_id)
        self.accept()

    def reject(self):
        # Clear pending changes on cancel
        self._pending_name_changes.clear()
        super().reject()

    def closeEvent(self, event):
        self.snippet_player.stop()
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
        self.speakers = db_ops.get_speakers_for_recording(self.recording_id)
        self._populate_speakers()
        self.participants_updated.emit(self.recording_id) 