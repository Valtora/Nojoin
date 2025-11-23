import subprocess
import json
import os
from typing import List

def get_audio_duration(file_path: str) -> float:
    """
    Get the duration of an audio file in seconds using ffprobe.
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        file_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except (subprocess.CalledProcessError, KeyError, ValueError) as e:
        raise RuntimeError(f"Failed to get audio duration for {file_path}: {e}")

def concatenate_wavs(segment_paths: List[str], output_path: str):
    """
    Concatenate multiple WAV files into a single file using ffmpeg.
    """
    # Create a temporary file list for ffmpeg
    list_file_path = output_path + ".list.txt"
    with open(list_file_path, "w") as f:
        for path in segment_paths:
            # Use forward slashes for ffmpeg compatibility on Windows
            safe_path = os.path.abspath(path).replace("\\", "/")
            # Escape single quotes
            safe_path = safe_path.replace("'", "'\\''")
            f.write(f"file '{safe_path}'\n")
    
    cmd = [
        "ffmpeg",
        "-y",  # Overwrite output file
        "-f", "concat",
        "-safe", "0",
        "-i", list_file_path,
        "-c", "copy",
        output_path
    ]
    
    try:
        # Capture stderr to include in error message
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode() if e.stderr else "Unknown ffmpeg error"
        raise RuntimeError(f"Failed to concatenate audio files: {error_msg}")
    finally:
        if os.path.exists(list_file_path):
            os.remove(list_file_path)

def convert_to_mono_16k(input_path: str, output_path: str):
    """
    Convert audio to mono 16kHz WAV using ffmpeg.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-ac", "1",       # Mono
        "-ar", "16000",   # 16kHz
        "-f", "wav",
        output_path
    ]
    
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to convert audio: {e.stderr.decode()}")
