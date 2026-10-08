import json
import logging
import os
import shutil
import subprocess
from typing import List

logger = logging.getLogger(__name__)

LOSSY_AUDIO_BITRATE_FLOOR_BITS_PER_SECOND = 128_000
PLAYBACK_PROXY_SAMPLE_RATE_HZ = 48_000
PLAYBACK_PROXY_BITRATE_BITS_PER_SECOND = 192_000

# Audio/video containers accepted for import (OBS records MKV, cameras MTS, phones
# 3GP). Only the audio is ever read: every conversion below writes an audio-only
# format, so ffmpeg maps the audio stream alone and a video track is demuxed past,
# never decoded.
MEDIA_CONTAINER_SUFFIXES = frozenset(
    {".mkv", ".mka", ".mov", ".avi", ".m4v", ".ts", ".mts", ".mpg", ".mpeg", ".3gp"}
)


def load_audio(path: str, *, channels_first: bool = True):
    """Load an audio file into a float32 torch tensor and its sample rate.

    Explicit soundfile loader used instead of torchaudio.load: torchaudio 2.11
    ignores the legacy backend argument and routes I/O through torchcodec, and
    its load/info helpers are being retired. Audio reaching the processing
    pipeline is always ffmpeg-transcoded WAV (see processing/segment_transcode),
    which soundfile decodes natively and deterministically. soundfile and torch
    are imported lazily so the torch-free API container can import this module.

    Returns a ``(channels, frames)`` tensor when ``channels_first`` is True.
    """
    import soundfile as sf
    import torch

    data, sample_rate = sf.read(path, dtype="float32", always_2d=False)
    tensor = torch.from_numpy(data)
    if tensor.ndim == 1:
        tensor = tensor.unsqueeze(0 if channels_first else 1)
    elif channels_first:
        tensor = tensor.t()
    return tensor.contiguous(), sample_rate


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


class NoAudioStreamError(RuntimeError):
    """The file holds no audio track, or only an empty one.

    ``detail`` is the message shown to the person importing the file; the
    exception's own text names the server path and stays in the logs.
    """

    detail = (
        "This file has no audio track, so there is nothing to import. "
        "Check that the recording captured audio."
    )


class UnreadableAudioStreamError(NoAudioStreamError):
    """ffprobe lists an audio stream but could not read its sample format.

    In MPEG-PS/TS this happens when the first audio packet lies past ffmpeg's
    default probe window. Every conversion uses that same window, so each one
    would fail with "Output file does not contain any stream".
    """

    detail = (
        "Nojoin cannot read this file's audio track; it may start too far into "
        "the file. Convert the file to an audio format such as MP3 or WAV and "
        "import that."
    )


# A probe reads headers, not the whole file, so this only ever ends a hung one.
FFPROBE_TIMEOUT_S = 60

_DURATION_PROBE_ENTRIES = (
    "format=duration"
    ":stream=codec_type,channels,duration"
    ":stream_disposition=default"
    ":stream_tags"
)


def _duration_seconds(value) -> float | None:
    """Parse an ffprobe duration: seconds, or a Matroska ``HH:MM:SS.f`` tag."""
    if value in (None, "", "N/A"):
        return None
    text = str(value)
    try:
        if ":" in text:
            hours, minutes, seconds = text.split(":")
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        return float(text)
    except ValueError:
        return None


def _duration_tag(stream: dict) -> str | None:
    """A Matroska track's DURATION statistics tag.

    mkvmerge can write it with a language suffix, which ffprobe reports as a
    separate key such as ``DURATION-eng``.
    """
    for key, value in (stream.get("tags") or {}).items():
        name = key.upper()
        if name == "DURATION" or name.startswith("DURATION-"):
            return value
    return None


def decoded_audio_stream(streams: list[dict]) -> dict | None:
    """The audio stream ffmpeg decodes when no stream is mapped explicitly.

    ffmpeg's automatic selection takes the audio stream flagged default, then
    the one with the most channels, then the first. Every conversion in this
    module relies on that selection, so a file's length and format are read
    from the same stream.
    """
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio:
        return None
    return max(
        audio,
        key=lambda s: (
            bool((s.get("disposition") or {}).get("default")),
            int(s.get("channels") or 0),
        ),
    )


def audio_duration_from_probe(data: dict, source: str) -> float:
    """The length in seconds of the audio ffmpeg decodes, from ffprobe JSON.

    ``data`` needs the entries in ``_DURATION_PROBE_ENTRIES`` (``-show_format
    -show_streams`` includes them). The decoded stream's Matroska DURATION tag
    wins, then the stream's own duration, then the container's: a video file's
    container can run longer than its audio track. A zero or negative value
    counts as missing at each step, and is returned only when no step reports
    a positive length.

    Raises:
        NoAudioStreamError: the streams hold no audio, or the decoded audio
            track is empty (its DURATION tag is zero).
        UnreadableAudioStreamError: the decoded audio stream has no channels.
        RuntimeError: no duration is reported at all.
    """
    container = _duration_seconds((data.get("format") or {}).get("duration"))
    candidates = [container]
    streams = data.get("streams") or []
    if streams:
        stream = decoded_audio_stream(streams)
        if stream is None:
            raise NoAudioStreamError(f"No audio stream in {source}")
        if stream.get("channels") == 0:
            raise UnreadableAudioStreamError(f"Unreadable audio stream in {source}")
        tagged = _duration_seconds(_duration_tag(stream))
        if tagged is not None and tagged <= 0:
            raise NoAudioStreamError(f"Empty audio track in {source}")
        candidates = [tagged, _duration_seconds(stream.get("duration")), container]

    reported = [seconds for seconds in candidates if seconds is not None]
    for seconds in reported:
        if seconds > 0:
            return seconds
    if reported:
        return 0.0
    raise RuntimeError(f"Failed to get audio duration for {source}: none reported")


def get_audio_duration(file_path: str) -> float:
    """
    Get the duration of a file's audio in seconds using ffprobe.

    See ``audio_duration_from_probe`` for which track and which reported
    length are used.

    Raises:
        NoAudioStreamError: no audio track, or an empty one.
        UnreadableAudioStreamError: an audio track ffmpeg cannot read.
        RuntimeError: ffprobe failed, timed out or reported no duration.
    """
    ensure_ffmpeg_in_path()

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        _DURATION_PROBE_ENTRIES,
        "-of",
        "json",
        file_path,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=FFPROBE_TIMEOUT_S,
        )
        data = json.loads(result.stdout)
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        ValueError,
        FileNotFoundError,
    ) as e:
        # FileNotFoundError can happen if ffprobe is still not found
        raise RuntimeError(f"Failed to get audio duration for {file_path}: {e}")

    return audio_duration_from_probe(data, file_path)


def _concatenate_with_ffmpeg_concat_demuxer(segment_paths: List[str], output_path: str):
    """Concatenate multiple same-codec/container files into a single output."""
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
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        list_file_path,
        "-c",
        "copy",
        output_path,
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


def concatenate_media_files(segment_paths: List[str], output_path: str):
    """
    Concatenate multiple same-codec/container audio files into a single file.
    """
    _concatenate_with_ffmpeg_concat_demuxer(segment_paths, output_path)


def concatenate_wavs(segment_paths: List[str], output_path: str):
    """
    Concatenate multiple WAV files into a single file using ffmpeg.
    """
    _concatenate_with_ffmpeg_concat_demuxer(segment_paths, output_path)


def concatenate_binary_files(segment_paths: List[str], output_path: str):
    """
    Concatenate multiple binary files into a single file.
    Used for reassembling chunked uploads of arbitrary file types.
    """
    try:
        with open(output_path, "wb") as outfile:
            for segment_path in segment_paths:
                with open(segment_path, "rb") as infile:
                    shutil.copyfileobj(infile, outfile)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Failed to concatenate binary files: {str(e)}")


def convert_to_mono_16k(
    input_path: str, output_path: str, *, timeout: float | None = None
):
    """
    Convert audio to mono 16kHz WAV using ffmpeg.

    ``timeout`` (seconds) kills a hung ffmpeg and raises
    ``subprocess.TimeoutExpired``; None waits indefinitely.
    """
    ensure_ffmpeg_in_path()

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-ac",
        "1",  # Mono
        "-ar",
        "16000",  # 16kHz
        "-f",
        "wav",
        output_path,
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
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
        "-i",
        input_path,
        "-codec:a",
        "libmp3lame",
        "-b:a",
        "128k",
        output_path,
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(
            f"Failed to convert audio to MP3: {e.stderr.decode() if e.stderr else str(e)}"
        )
        return False
    except Exception as e:  # noqa: BLE001
        logger.error(f"Unexpected error converting to MP3: {str(e)}")
        return False


def convert_to_wav(input_path: str, output_path: str) -> bool:
    """
    Convert audio to WAV (PCM 16-bit) using ffmpeg.
    Useful for restoring proxy mp3 back to wav for processing.
    """
    ensure_ffmpeg_in_path()

    cmd = ["ffmpeg", "-y", "-i", input_path, "-acodec", "pcm_s16le", output_path]

    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(
            f"Failed to convert audio to WAV: {e.stderr.decode() if e.stderr else str(e)}"
        )
        return False
    except Exception as e:  # noqa: BLE001
        logger.error(f"Unexpected error converting to WAV: {str(e)}")
        return False


def convert_to_proxy_mp3(
    input_path: str,
    output_path: str,
    *,
    mix_to_mono: bool = False,
) -> bool:
    """
    Convert audio to a high-quality MP3 proxy for frontend playback.
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
        "-i",
        input_path,
        "-ar",
        str(PLAYBACK_PROXY_SAMPLE_RATE_HZ),
    ]

    if mix_to_mono:
        cmd.extend(["-ac", "1"])

    cmd.extend(
        [
            "-codec:a",
            "libmp3lame",
            "-b:a",
            f"{PLAYBACK_PROXY_BITRATE_BITS_PER_SECOND // 1000}k",
            "-f",
            "mp3",  # Force MP3 format
            final_output_path,
        ]
    )

    try:
        subprocess.run(cmd, check=True, capture_output=True)

        if is_same_file:
            # Atomic replacement if possible, or move
            if os.path.exists(final_output_path):
                shutil.move(final_output_path, output_path)

        return True
    except subprocess.CalledProcessError as e:
        logger.error(
            f"Failed to convert audio to proxy MP3: {e.stderr.decode() if e.stderr else str(e)}"
        )
        # Cleanup temp file
        if is_same_file and os.path.exists(final_output_path):
            try:
                os.remove(final_output_path)
            except OSError:
                pass
        return False


def extract_audio_clip(
    input_path: str,
    output_path: str,
    *,
    start_seconds: float,
    end_seconds: float,
) -> None:
    """Extract a PCM WAV subclip from an audio file using ffmpeg."""
    ensure_ffmpeg_in_path()

    duration_seconds = max(float(end_seconds) - float(start_seconds), 0.0)
    if duration_seconds <= 0.0:
        raise RuntimeError("Clip duration must be positive")

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{float(start_seconds):.3f}",
        "-t",
        f"{duration_seconds:.3f}",
        "-i",
        input_path,
        "-acodec",
        "pcm_s16le",
        output_path,
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode() if e.stderr else "Unknown ffmpeg error"
        raise RuntimeError(f"Failed to extract audio clip: {error_msg}") from e
    except Exception as e:  # noqa: BLE001 -- boundary: clean up then translate to RuntimeError
        # Remove any partially-written clip before propagating the failure.
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass
        raise RuntimeError(f"Failed to extract audio clip: {e}") from e
