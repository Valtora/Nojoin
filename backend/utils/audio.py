import subprocess
import json
import os
import shutil
import logging
from typing import List

logger = logging.getLogger(__name__)

def ensure_ffmpeg_in_path():
    """
    Ensures ffmpeg and ffprobe are in the system PATH.
    Checks common locations if not found.
    """
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return

    possible_paths = [
        # Windows
        os.path.join(os.getcwd(), "ffmpeg.exe"),
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        # Linux / Unix
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/snap/bin/ffmpeg",
        # macOS
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/opt/ffmpeg/bin/ffmpeg",
    ]
    
    found = False
    for p in possible_paths:
        if os.path.exists(p):
            ffmpeg_dir = os.path.dirname(p)
            if ffmpeg_dir not in os.environ["PATH"]:
                logger.info(f"Adding ffmpeg directory to PATH: {ffmpeg_dir}")
                os.environ["PATH"] += os.pathsep + ffmpeg_dir
            found = True
            break
    
    if not found and not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        logger.warning(
            "FFmpeg/FFprobe not found in PATH or common locations. "
            "Please install FFmpeg to enable audio processing features."
        )

def get_audio_duration(file_path: str) -> float:
    """
    Get the duration of an audio file in seconds using ffprobe.
    """
    ensure_ffmpeg_in_path()
    
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
    except (subprocess.CalledProcessError, KeyError, ValueError, FileNotFoundError) as e:
        # FileNotFoundError can happen if ffprobe is still not found
        raise RuntimeError(f"Failed to get audio duration for {file_path}: {e}")

def concatenate_wavs(segment_paths: List[str], output_path: str):
    """
    Concatenate multiple WAV files into a single file using ffmpeg.
    """
    ensure_ffmpeg_in_path()
    
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

def concatenate_binary_files(segment_paths: List[str], output_path: str):
    """
    Concatenate multiple binary files into a single file.
    Used for reassembling chunked uploads of arbitrary file types.
    """
    try:
        with open(output_path, 'wb') as outfile:
            for segment_path in segment_paths:
                with open(segment_path, 'rb') as infile:
                    shutil.copyfileobj(infile, outfile)
    except Exception as e:
        raise RuntimeError(f"Failed to concatenate binary files: {str(e)}")

def convert_to_mono_16k(input_path: str, output_path: str):
    """
    Convert audio to mono 16kHz WAV using ffmpeg.
    """
    ensure_ffmpeg_in_path()
    
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

def convert_to_mp3(input_path: str, output_path: str) -> bool:
    """
    Convert audio to MP3 (128kbps) using ffmpeg.
    Returns True if successful, False otherwise.
    """
    ensure_ffmpeg_in_path()
    
    cmd = [
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-codec:a", "libmp3lame",
        "-b:a", "128k",
        output_path
    ]
    
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to convert audio to MP3: {e.stderr.decode() if e.stderr else str(e)}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error converting to MP3: {str(e)}")
        return False

def convert_to_proxy_mp3(input_path: str, output_path: str) -> bool:
    """
    Convert audio to 48kHz mono MP3 (128kbps) for proxy playback.
    Returns True if successful, False otherwise.
    """
    ensure_ffmpeg_in_path()
    
    # Check for in-place modification
    input_abs = os.path.abspath(input_path)
    output_abs = os.path.abspath(output_path)
    is_same_file = input_abs == output_abs
    
    final_output_path = output_path
    if is_same_file:
         import uuid
         # Use a unique temp file in the same directory to ensure atomic move/rename works usually
         final_output_path = f"{output_path}.{uuid.uuid4().hex[:8]}.tmp"

    cmd = [
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-ar", "48000",   # 48kHz sample rate
        "-ac", "1",       # Mono (speech-optimised)
        "-codec:a", "libmp3lame",
        "-b:a", "128k",   # Transparent quality for speech
        "-f", "mp3",      # Force MP3 format
        final_output_path
    ]
    
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        
        if is_same_file:
            # Atomic replacement if possible, or move
            if os.path.exists(final_output_path):
                shutil.move(final_output_path, output_path)
                
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to convert audio to proxy MP3: {e.stderr.decode() if e.stderr else str(e)}")
        # Cleanup temp file
        if is_same_file and os.path.exists(final_output_path):
            try:
                os.remove(final_output_path)
            except OSError:
                pass
        return False
    except Exception as e:
        logger.error(f"Unexpected error converting to proxy MP3: {str(e)}")
        # Cleanup temp file
        if is_same_file and os.path.exists(final_output_path):
            try:
                os.remove(final_output_path)
            except OSError:
                pass
        return False
