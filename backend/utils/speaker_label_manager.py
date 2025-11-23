import threading

class SpeakerLabelManager:
    """
    Manages mapping between internal speaker labels/IDs and display names for speakers.
    Thread-safe for use across application modules.
    """
    def __init__(self):
        self._lock = threading.Lock()
        self._label_to_name = {}  # e.g., {'SPEAKER_00': 'Alice', ...}

    def set_mapping(self, mapping: dict):
        with self._lock:
            self._label_to_name = dict(mapping)

    def update_label(self, label: str, name: str):
        with self._lock:
            self._label_to_name[label] = name

    def get_name(self, label: str) -> str:
        with self._lock:
            return self._label_to_name.get(label, label)

    def get_mapping(self) -> dict:
        with self._lock:
            return dict(self._label_to_name)

    def clear(self):
        with self._lock:
            self._label_to_name.clear() 