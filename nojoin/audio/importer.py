import os
import shutil
import logging
from datetime import datetime
from pydub import AudioSegment
from ..utils.config_manager import get_recordings_dir, to_project_relative_path
from ..utils.path_manager import path_manager

logger = logging.getLogger(__name__)

# Note: On Windows, you may see 'Failed to initialize COM library (Cannot change thread mode after it is set.)' in logs when using file dialogs.
# This is a benign warning from Qt/PySide6 and does not affect functionality.

SUPPORTED_FORMATS = ["mp3", "wav", "ogg", "flac", "m4a", "aac"]

class ImportResult:
    def __init__(self, success, message, rel_path=None, duration=None, size=None, format=None):
        self.success = success
        self.message = message
        self.rel_path = rel_path
        self.duration = duration
        self.size = size
        self.format = format

def is_supported_audio_file(filepath):
    ext = os.path.splitext(filepath)[1].lower().replace(".", "")
    return ext in SUPPORTED_FORMATS

def import_audio_file(src_path, recordings_dir=None):
    """
    Import an audio file into the recordings directory, converting to MP3 if needed.
    Returns ImportResult.
    """
    if not os.path.exists(src_path):
        return ImportResult(False, f"File does not exist: {src_path}")
    ext = os.path.splitext(src_path)[1].lower().replace(".", "")
    if ext not in SUPPORTED_FORMATS:
        return ImportResult(False, f"Unsupported audio format: {ext}")
    recordings_dir = recordings_dir or get_recordings_dir()
    os.makedirs(recordings_dir, exist_ok=True)
    # Generate a unique filename
    base_name = os.path.splitext(os.path.basename(src_path))[0]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_base = f"imported_{base_name}_{timestamp}"
    dest_mp3 = os.path.join(recordings_dir, f"{dest_base}.mp3")
    # If already mp3, just copy; else convert
    try:
        if ext == "mp3":
            shutil.copy2(src_path, dest_mp3)
            audio = AudioSegment.from_mp3(dest_mp3)
        else:
            audio = AudioSegment.from_file(src_path)
            audio.export(dest_mp3, format="mp3")
        duration = len(audio) / 1000.0
        size = os.path.getsize(dest_mp3)
        rel_path = to_project_relative_path(dest_mp3)
        logger.info(f"Imported audio file: {src_path} -> {dest_mp3} ({duration:.2f}s, {size} bytes)")
        return ImportResult(True, "Import successful", rel_path, duration, size, "MP3")
    except Exception as e:
        logger.error(f"Failed to import audio file {src_path}: {e}", exc_info=True)
        return ImportResult(False, f"Failed to import: {e}")

def import_multiple_audio_files(src_paths, recordings_dir=None):
    results = []
    for path in src_paths:
        results.append(import_audio_file(path, recordings_dir))
    return results 