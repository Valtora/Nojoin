from PySide6.QtCore import QThread, Signal

class MeetingNotesWorker(QThread):
    success = Signal(str)  # Emitted with generated notes (markdown or text)
    error = Signal(str)    # Emitted with error message

    def __init__(self, backend, transcript, label_to_name=None, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.transcript = transcript
        self.label_to_name = label_to_name

    def run(self):
        try:
            if self.label_to_name is not None:
                # Use speaker mapping if provided (after relabel)
                notes = self.backend.generate_meeting_notes(self.transcript, self.label_to_name)
            else:
                # Use infer_speakers_and_generate_notes if mapping not provided
                _, notes = self.backend.infer_speakers_and_generate_notes(self.transcript)
            self.success.emit(notes)
        except Exception as e:
            self.error.emit(str(e)) 