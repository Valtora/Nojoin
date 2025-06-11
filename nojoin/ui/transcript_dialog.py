import sys
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QTextEdit, QPushButton, QApplication, QDialogButtonBox
)
from PySide6.QtCore import Qt

# Add import for DB and config utilities
from nojoin.db import database as db_ops
from nojoin.utils.config_manager import from_project_relative_path
from nojoin.utils.config_manager import config_manager
from nojoin.utils.theme_utils import wrap_html_body
import os
import re
from nojoin.utils.transcript_utils import render_transcript

class TranscriptViewDialog(QDialog):
    def __init__(self, transcript_html=None, window_title="View Transcript", parent=None, recording_id=None):
        super().__init__(parent)
        self.setWindowTitle(window_title)
        
        # Use scaled minimum size
        from nojoin.utils.ui_scale_manager import get_ui_scale_manager
        ui_scale_manager = get_ui_scale_manager()
        min_width, min_height = ui_scale_manager.get_scaled_minimum_sizes()['transcript_dialog']
        self.setMinimumSize(min_width, min_height)

        self.layout = QVBoxLayout(self)

        self.transcript_display = QTextEdit()
        self.transcript_display.setReadOnly(True)
        # If a recording_id is provided, fetch and render the latest transcript
        if recording_id is not None:
            transcript_html = self._generate_transcript_html(recording_id)
        self.transcript_display.setHtml(transcript_html or "<p>No transcript available.</p>")
        self.layout.addWidget(self.transcript_display)

        # Standard OK button to close
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok)
        self.button_box.accepted.connect(self.accept)
        self.layout.addWidget(self.button_box)

        self.setLayout(self.layout)

    def _generate_transcript_html(self, recording_id):
        # Fetch transcript content and speaker mapping
        from nojoin.utils.transcript_store import TranscriptStore
        
        rec = db_ops.get_recording_by_id(recording_id)
        if not rec:
            return wrap_html_body("<p>Recording not found.</p>", config_manager.get("theme", "dark"))
        
        # Try to get transcript from database first
        diarized_transcript_text = TranscriptStore.get(recording_id, "diarized")
        
        if not diarized_transcript_text:
            # Fallback to file-based approach for legacy recordings
            diarized_transcript_path = rec.get("diarized_transcript_path")
            abs_diarized_transcript_path = from_project_relative_path(diarized_transcript_path) if diarized_transcript_path else None
            if abs_diarized_transcript_path and os.path.exists(abs_diarized_transcript_path):
                try:
                    with open(abs_diarized_transcript_path, 'r', encoding='utf-8') as f:
                        diarized_transcript_text = f.read()
                except Exception as e:
                    return wrap_html_body(f"<p>Error reading transcript file: {e}</p>", config_manager.get("theme", "dark"))
        
        if not diarized_transcript_text:
            return wrap_html_body("<p>Diarized transcript not found.</p>", config_manager.get("theme", "dark"))
        
        # Build label->name mapping with fallback
        speakers = db_ops.get_speakers_for_recording(recording_id)
        label_to_name = {s['diarization_label']: s['name'] for s in speakers if s.get('diarization_label')}
        label_to_name['Unknown'] = 'Unknown'
        
        # Process transcript text directly instead of using render_transcript with file path
        display_text_lines = []
        for line in diarized_transcript_text.split('\n'):
            if not line.strip():
                continue
            # Match lines like: [timestamp] - <speaker> - text
            m = re.match(r"(\[.*?\]\s*-\s*)(.+?)(\s*-\s*)(.*)", line)
            if m:
                prefix = m.group(1)
                diarization_label = m.group(2).strip()
                sep = m.group(3)
                text_content = m.group(4)
                # Map label to current name
                speaker_name = label_to_name.get(diarization_label, diarization_label)
                import html as html_converter 
                escaped_text_content = html_converter.escape(text_content)
                html_line = (f'<span class="meta">{prefix}</span> '
                             f'<b>{speaker_name}</b>'
                             f'<span class="meta">{sep}</span>'
                             f'<span>{escaped_text_content}</span>')
                display_text_lines.append(html_line)
            else:
                import html as html_converter
                escaped_line = html_converter.escape(line.rstrip('\n'))
                display_text_lines.append(f'<span>{escaped_line}</span>')
        
        full_html = "<br>".join(display_text_lines)
        return wrap_html_body(full_html, config_manager.get("theme", "dark"))

if __name__ == '__main__':
    # Example usage for testing the dialog directly
    app = QApplication(sys.argv)
    example_html = """
    <html><head><style>body{font-family:'Segoe UI',Arial,sans-serif;font-size:14px;background:#181818; color:#eaeaea;}</style></head>
    <body>
        <p><span style="color:#888;font-size:12px;">[00:00:00 - 00:00:05] - </span><b style="color:#ff9800;">Speaker 1</b><span style="color:#888;font-size:12px;"> - </span><span style="color:#eaeaea;">Hello, this is a test transcript.</span></p>
        <p><span style="color:#888;font-size:12px;">[00:00:06 - 00:00:10] - </span><b style="color:#2196f3;">Speaker 2</b><span style="color:#888;font-size:12px;"> - </span><span style="color:#eaeaea;">This is another line from a different speaker.</span></p>
        <p>This line has no speaker information.</p>
        <p>And <b>this</b> has some <i>rich</i> text.</p>
    </body></html>
    """
    dialog = TranscriptViewDialog(example_html, "Test Transcript Dialog")
    dialog.exec()
    sys.exit(app.exec()) 