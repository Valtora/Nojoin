"""Keep only the audio of an imported recording.

Import accepts video and media containers (OBS's MKV, a camera's MTS, a
phone's 3GP) as well as audio files, but stores audio only. Once the upload
is complete, the audio track is extracted to an audio-only file in a format
import already accepted, that file replaces the upload as the recording's
``audio_path``, and the upload is deleted. Nothing after import (the playback
proxy, processing, analytics, embeddings, backups) ever reads a video
container. See "Imported Media Input" in docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.utils.audio import ensure_ffmpeg_in_path

logger = logging.getLogger(__name__)

# Audio/video containers accepted for import. Their audio track is always
# extracted, so none of these suffixes is ever stored.
MEDIA_CONTAINER_SUFFIXES = frozenset(
    {".mkv", ".mka", ".mov", ".avi", ".m4v", ".ts", ".mts", ".mpg", ".mpeg", ".3gp"}
)

# A probe reads headers, not the whole file, so this only ever ends a hung one.
PROBE_TIMEOUT_S = 60

# Upper bound on one extraction. A stream copy runs at disk speed (6.5 s for a
# 3.5 GB, one-hour OBS recording) and an Opus re-encode at about 200x real
# time, so this only ever ends a hung ffmpeg.
EXTRACT_TIMEOUT_S = 15 * 60

_PROBE_ENTRIES = (
    "format=duration"
    ":stream=index,codec_type,codec_name,channels,duration"
    ":stream_disposition=default,attached_pic"
    ":stream_tags"
)

# Codecs stored as they are (a stream copy: no re-encode, no quality loss), and
# the audio-only container each goes into. Every suffix is one import accepted
# before media containers were, so the pipeline already reads it.
_STREAM_COPY_SUFFIXES = {
    "aac": ".m4a",
    "alac": ".m4a",
    "mp3": ".mp3",
    "opus": ".webm",
    "vorbis": ".ogg",
    "flac": ".flac",
}

# Any other codec is re-encoded. PCM becomes FLAC: lossless, about 40% of the
# WAV's size, and free of WAV's 4 GiB limit. Anything else (MP2, AC-3, AMR,
# DTS, ...) becomes what browser capture stores: Opus at 160 kb/s in WebM.
_PCM_CODEC_PREFIX = "pcm_"
_REENCODE_SUFFIX = ".webm"
_REENCODE_OPUS_BITRATE = "160k"
# libopus refuses some surround layouts, such as the 5.1(side) cameras write.
# Processing mixes to mono, so a surround track is re-encoded as stereo.
_MAX_REENCODE_CHANNELS = 2

# How far the extracted audio's length may differ from the source track's
# before the extraction counts as failed: whichever of these is larger.
_DURATION_TOLERANCE_S = 1.0
_DURATION_TOLERANCE_RATIO = 0.01


class ImportRefusedError(RuntimeError):
    """An uploaded file that import will not keep.

    ``detail`` is shown to the person importing the file and ``status_code`` is
    the HTTP status to answer with; the exception's own text names the server
    path and stays in the logs.
    """

    status_code = 400
    detail = "This file cannot be imported."


class NoAudioStreamError(ImportRefusedError):
    """The file holds no audio track, or only empty ones."""

    detail = (
        "This file has no audio track, so there is nothing to import. "
        "Check that the recording captured audio."
    )


class UnreadableAudioStreamError(NoAudioStreamError):
    """ffprobe lists an audio stream but could not read its sample format.

    In MPEG-PS/TS this happens when the first audio packet lies past ffmpeg's
    default probe window, and the extraction would fail with "Output file does
    not contain any stream".
    """

    detail = (
        "Nojoin cannot read this file's audio track; it may start too far into "
        "the file. Convert the file to an audio format such as MP3 or WAV and "
        "import that."
    )


class AudioExtractionError(ImportRefusedError):
    """ffmpeg could not extract the audio track, or the result did not verify."""

    status_code = 422
    detail = (
        "Nojoin could not extract this file's audio track. Convert the file to "
        "an audio format such as MP3 or WAV and import that."
    )


def keep_imported_audio(source_path: str) -> str:
    """Return the file to store as an import's ``audio_path``.

    A media container, or any file that carries video or more than one audio
    track, has one audio track extracted to a new audio-only file next to it.
    Once that file verifies (one audio stream, the source track's length),
    ``source_path`` is deleted and the new path returned. Any other file is
    returned unchanged, as is a non-container file ffprobe cannot read, which
    import has always accepted.

    The track is the one ffmpeg selects by default, so a file is stored with
    the audio the pipeline would have decoded from it.

    Blocking: it runs ffprobe and possibly a full pass of ffmpeg over the file,
    so call it off the event loop.

    Raises:
        NoAudioStreamError: no audio track, or only empty ones.
        UnreadableAudioStreamError: the selected track's format is unreadable.
        AudioExtractionError: a container ffprobe cannot read, or an
            extraction that failed, timed out or did not verify.

    On a refusal ``source_path`` is left in place for the caller to remove,
    and nothing else is left behind.
    """
    is_container = Path(source_path).suffix.lower() in MEDIA_CONTAINER_SUFFIXES
    try:
        probe = _probe(source_path)
    except RuntimeError as exc:
        if is_container:
            raise AudioExtractionError(str(exc)) from exc
        logger.warning("Keeping imported file %s as uploaded: %s", source_path, exc)
        return source_path

    streams = probe.get("streams") or []
    track = _selected_audio_track(streams, source_path)
    if not is_container and not _holds_video_or_several_audio_tracks(streams):
        return source_path

    extracted = _extract_audio_track(source_path, track)
    os.remove(source_path)
    logger.info("Kept the audio of imported file %s as %s", source_path, extracted)
    return extracted


def _probe(path: str) -> dict[str, Any]:
    """ffprobe ``path``'s format duration and streams as JSON.

    Raises:
        RuntimeError: ffprobe failed, timed out, could not be started or wrote
            something other than JSON.
    """
    ensure_ffmpeg_in_path()
    cmd = ["ffprobe", "-v", "error", "-show_entries", _PROBE_ENTRIES]
    cmd += ["-of", "json", path]
    try:
        result = subprocess.run(
            cmd, capture_output=True, check=True, timeout=PROBE_TIMEOUT_S
        )
        # Tag values are raw container metadata and need not be UTF-8.
        data = json.loads(result.stdout.decode("utf-8", errors="replace"))
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        raise RuntimeError(f"ffprobe could not read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"ffprobe could not read {path}: unexpected output")
    return data


def _holds_video_or_several_audio_tracks(streams: list[dict]) -> bool:
    """The file holds video (not cover art) or several audio tracks."""
    audio_tracks = 0
    for stream in streams:
        kind = stream.get("codec_type")
        if kind == "audio":
            audio_tracks += 1
        elif kind == "video" and not (stream.get("disposition") or {}).get(
            "attached_pic"
        ):
            return True
    return audio_tracks > 1


def _selected_audio_track(streams: list[dict], source: str) -> dict:
    """The audio track to keep, refusing a file with none usable."""
    track = decoded_audio_stream(streams)
    if track is None:
        raise NoAudioStreamError(f"No audio stream in {source}")
    if track.get("channels") == 0:
        raise UnreadableAudioStreamError(f"Unreadable audio stream in {source}")
    if _is_empty_track(track):
        raise NoAudioStreamError(f"Empty audio track in {source}")
    return track


def _duration_seconds(value: object) -> float | None:
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


def _is_empty_track(stream: dict) -> bool:
    """The stream's DURATION tag says the track holds no audio."""
    tagged = _duration_seconds(_duration_tag(stream))
    return tagged is not None and tagged <= 0


def _has_audio(stream: dict) -> bool:
    """ffprobe read the stream's format and found the track non-empty.

    This stands in for ffmpeg's own test, whether the stream yielded packets
    while the input was probed, which ffprobe does not report.
    """
    return int(stream.get("channels") or 0) > 0 and not _is_empty_track(stream)


def decoded_audio_stream(streams: list[dict]) -> dict | None:
    """The audio stream ffmpeg decodes when no stream is mapped explicitly.

    ffmpeg's automatic selection prefers a stream that has audio over one that
    is empty or unread, then the stream flagged default, then the one with the
    most channels, then the first.
    """
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio:
        return None
    ranked = max(
        enumerate(audio),
        key=lambda item: (
            _has_audio(item[1]),
            bool((item[1].get("disposition") or {}).get("default")),
            int(item[1].get("channels") or 0),
            -item[0],
        ),
    )
    return ranked[1]


def _track_duration(track: dict) -> float | None:
    """The track's own reported length, or None. Never the container's,
    which counts the video and can run longer than the audio."""
    for value in (_duration_tag(track), track.get("duration")):
        seconds = _duration_seconds(value)
        if seconds is not None and seconds > 0:
            return seconds
    return None


def _codec_arguments(track: dict) -> tuple[str, list[str]]:
    """The output suffix and ffmpeg codec arguments that keep ``track``."""
    codec = str(track.get("codec_name") or "")
    copy_suffix = _STREAM_COPY_SUFFIXES.get(codec)
    if copy_suffix is not None:
        return copy_suffix, ["-c:a", "copy"]
    if codec.startswith(_PCM_CODEC_PREFIX):
        return ".flac", ["-c:a", "flac"]
    arguments = ["-c:a", "libopus", "-b:a", _REENCODE_OPUS_BITRATE]
    if int(track.get("channels") or 0) > _MAX_REENCODE_CHANNELS:
        arguments += ["-ac", str(_MAX_REENCODE_CHANNELS)]
    return _REENCODE_SUFFIX, arguments


def _extract_audio_track(source_path: str, track: dict) -> str:
    """Write ``track`` of ``source_path`` to a new audio-only file; return it.

    Raises:
        AudioExtractionError: ffmpeg failed, timed out or could not be started,
            or the new file did not verify. The new file is removed.
    """
    suffix, codec_arguments = _codec_arguments(track)
    target = str(Path(source_path).with_name(f"{uuid4()}{suffix}"))
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", source_path]
    cmd += ["-map", f"0:{track['index']}", *codec_arguments, target]
    ensure_ffmpeg_in_path()
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=EXTRACT_TIMEOUT_S)
        _verify_extracted_audio(target, expected_seconds=_track_duration(track))
    except (subprocess.SubprocessError, OSError, RuntimeError) as exc:
        _remove_quietly(target)
        stderr = getattr(exc, "stderr", None)
        reason = stderr.decode(errors="replace") if stderr else str(exc)
        raise AudioExtractionError(
            f"Could not extract the audio of {source_path}: {reason}"
        ) from exc
    return target


def _verify_extracted_audio(path: str, *, expected_seconds: float | None) -> None:
    """Check ``path`` is one audio track as long as the source track.

    Raises:
        RuntimeError: it is not, or ffprobe cannot read it.
    """
    probe = _probe(path)
    streams = probe.get("streams") or []
    if (
        len(streams) != 1
        or streams[0].get("codec_type") != "audio"
        or not _has_audio(streams[0])
    ):
        raise RuntimeError(f"{path} is not a single audio track")
    seconds = _duration_seconds((probe.get("format") or {}).get("duration"))
    if seconds is None or seconds <= 0:
        raise RuntimeError(f"{path} reports no duration")
    if expected_seconds is None:
        return
    tolerance = max(_DURATION_TOLERANCE_S, expected_seconds * _DURATION_TOLERANCE_RATIO)
    if abs(seconds - expected_seconds) > tolerance:
        raise RuntimeError(
            f"{path} runs {seconds:.2f} s where the source track runs "
            f"{expected_seconds:.2f} s"
        )


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Could not remove %s: %s", path, exc)
