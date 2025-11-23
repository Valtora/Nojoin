import shutil
import os
import subprocess
import sys

print(f"Python executable: {sys.executable}")
print(f"Current working directory: {os.getcwd()}")
print(f"PATH: {os.environ['PATH']}")

ffmpeg_path = shutil.which("ffmpeg")
print(f"shutil.which('ffmpeg'): {ffmpeg_path}")

ffprobe_path = shutil.which("ffprobe")
print(f"shutil.which('ffprobe'): {ffprobe_path}")

if ffmpeg_path:
    try:
        print("Running ffmpeg -version...")
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
        print(f"Return code: {result.returncode}")
        print(f"Output head: {result.stdout[:100]}")
    except Exception as e:
        print(f"Error running ffmpeg: {e}")
else:
    print("ffmpeg not found in PATH.")
    # Try common locations
    possible_paths = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\ffmpeg.exe"
    ]
    for p in possible_paths:
        if os.path.exists(p):
            print(f"Found at {p}")
