"""Keep only the audio of an imported recording.

Import accepts video and media containers (OBS's MKV, a camera's MTS, a
phone's 3GP) as well as audio files, but stores no video. Once the upload is
complete, the audio track of a container, or of any other upload that carries
video, is extracted to an audio-only file in a format import already
accepted; that file replaces the upload as the recording's ``audio_path``, and
the upload is deleted. Nothing after import (the playback proxy, processing,
analytics, embeddings, backups) ever reads a video container. See "Imported
Media Input" in docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple
from uuid import uuid4

from backend.utils.audio import ensure_ffmpeg_in_path
from backend.utils.import_audio_probe import (
    ToolFailure,
    UnreadableMediaError,
    decoded_audio_stream,
    has_audio,
    is_empty_track,
    killed_by_server_signal,
    probe_streams,
    seconds,
    track_end,
    track_span,
)

logger = logging.getLogger(__name__)

# Audio/video containers accepted for import. Their audio track is always
# extracted, so none of these suffixes is ever stored.
MEDIA_CONTAINER_SUFFIXES = frozenset(
    {".mkv", ".mka", ".mov", ".avi", ".m4v", ".ts", ".mts", ".mpg", ".mpeg", ".3gp"}
)

# Upper bound on one extraction, and on a full read of a track's packets. A
# stream copy runs at disk speed (6.5 s for a 3.5 GB, one-hour OBS recording,
# cold) and an Opus re-encode at about 200x real time, so this only ever ends
# a hung ffmpeg; it is not a latency budget (see ARCHITECTURE.md on proxy
# timeouts).
EXTRACT_TIMEOUT_S = 15 * 60

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

# Any other codec is re-encoded. PCM and the other lossless codecs become FLAC:
# still lossless, about 40% of a WAV's size, and free of WAV's 4 GiB limit.
# Anything else (MP2, AC-3, AMR, DTS, ...) becomes what browser capture stores:
# Opus at 160 kb/s in WebM.
_PCM_CODEC_PREFIX = "pcm_"
_LOSSLESS_CODECS = frozenset(
    {"wavpack", "tta", "truehd", "mlp", "ape", "tak", "wmalossless"}
)
_REENCODE_SUFFIX = ".webm"
_REENCODE_OPUS_BITRATE = "160k"
# libopus refuses some surround layouts, such as the 5.1(side) cameras write,
# and FLAC holds at most 8 channels. Processing mixes to mono, so such a track
# is re-encoded as stereo.
_OPUS_MAX_CHANNELS = 2
_FLAC_MAX_CHANNELS = 8
_DOWNMIX_CHANNELS = "2"

# How much shorter than the source track the extracted audio may be before the
# extraction counts as failed: whichever of these is larger. Both sides are
# measured from packets, which agree to within 0.1 s on every file probed; the
# 1 s floor covers a last packet that reports no duration.
_DURATION_TOLERANCE_S = 1.0
_DURATION_TOLERANCE_RATIO = 0.001

# ffmpeg error text that points at the server rather than at the file.
_SERVER_FAULT_MARKERS = (
    "No space left on device",
    "Disk quota exceeded",
    "File too large",
    "Input/output error",
    "Read-only file system",
    "Permission denied",
    "Cannot allocate memory",
)


class ImportRefusedError(RuntimeError):
    """An uploaded file that import will not keep, because of the file itself.

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
    """ffmpeg could not decode or copy the audio track, or the result did not
    hold the whole track."""

    status_code = 422
    detail = (
        "Nojoin could not extract this file's audio track. Convert the file to "
        "an audio format such as MP3 or WAV and import that."
    )


class ImportServerError(Exception):
    """The server failed while keeping the audio; the file may be fine.

    ffmpeg or ffprobe could not be started, timed out or was killed, the disk
    filled up, or the upload could not be removed. Answered as a server error,
    never as advice to convert the file.
    """


@dataclass(frozen=True)
class KeptAudio:
    """What import stores for an upload.

    ``reencoded_from_lossy`` is set when a lossy track was re-encoded, and
    ``source_bit_rate`` is then the source track's bit rate as ffprobe reports
    it (None when it reports none). A bitrate floor has to judge that, since
    measuring the stored file would measure the encoder.
    """

    path: str
    reencoded_from_lossy: bool = False
    source_bit_rate: int | None = None


class _OutputPlan(NamedTuple):
    suffix: str
    codec_arguments: list[str]
    reencodes_lossy: bool
    # The codec the output must hold; None to accept what ffmpeg wrote.
    copies_codec: str | None = None


class _CopyChangedCodec(RuntimeError):
    """A stream copy holds another codec than the track was reported as.

    MP4 and MOV label MPEG-1 Layer II audio "mp3", so a copy into ``.mp3``
    would store MP2 frames, which browsers do not play as MP3.
    """


def keep_imported_audio(source_path: str) -> KeptAudio:
    """Return what to store as an import's ``audio_path``.

    A media container, or any other file that carries video, has one audio
    track extracted to a new audio-only file next to it. Once that file
    verifies (one audio stream, as long as the source track), ``source_path``
    is deleted and the new path returned. Any other file is kept unchanged,
    including an audio-only file with several tracks and a non-container file
    ffprobe cannot read, both of which import has always stored as uploaded.

    The track is the one ffmpeg selects by default, so a file is stored with
    the audio the pipeline would have decoded from it.

    Blocking: it runs ffprobe and possibly a full pass of ffmpeg over the file,
    so call it off the event loop.

    Raises:
        NoAudioStreamError: no audio track, or only empty ones.
        UnreadableAudioStreamError: the selected track's format is unreadable.
        AudioExtractionError: a container ffprobe cannot read, or an
            extraction ffmpeg failed or whose result did not verify.
        ImportServerError: a failure on the server's side (see the class).

    On any of these ``source_path`` is left in place for the caller to remove
    (unless removing it is what failed), and nothing else is left behind.
    """
    is_container = Path(source_path).suffix.lower() in MEDIA_CONTAINER_SUFFIXES
    try:
        probe = probe_streams(source_path)
    except ToolFailure as exc:
        raise ImportServerError(str(exc)) from exc
    except UnreadableMediaError as exc:
        if is_container:
            logger.warning("Refusing imported container %s: %s", source_path, exc)
            raise AudioExtractionError(str(exc)) from exc
        logger.warning("Keeping imported file %s as uploaded: %s", source_path, exc)
        return KeptAudio(source_path)

    streams = probe.get("streams") or []
    track = _selected_audio_track(streams, source_path)
    if not is_container and not _carries_video(streams):
        return KeptAudio(source_path)

    extracted, plan = _extract_audio_track(source_path, track, probe)
    try:
        os.remove(source_path)
    except OSError as exc:
        # The caller's cleanup only knows source_path; leave nothing it cannot see.
        _remove_quietly(extracted)
        raise ImportServerError(
            f"Could not remove the upload {source_path}: {exc}"
        ) from exc
    logger.info("Kept the audio of imported file %s as %s", source_path, extracted)
    if not plan.reencodes_lossy:
        return KeptAudio(extracted)
    bit_rate = seconds(track.get("bit_rate"))
    return KeptAudio(
        extracted,
        reencoded_from_lossy=True,
        source_bit_rate=int(bit_rate) if bit_rate else None,
    )


def _carries_video(streams: list[dict]) -> bool:
    """The file holds a video stream that is not cover art."""
    return any(
        stream.get("codec_type") == "video"
        and not (stream.get("disposition") or {}).get("attached_pic")
        for stream in streams
    )


def _selected_audio_track(streams: list[dict], source: str) -> dict:
    """The audio track to keep, refusing a file with none usable."""
    track = decoded_audio_stream(streams)
    if track is None:
        raise NoAudioStreamError(f"No audio stream in {source}")
    if track.get("channels") == 0:
        raise UnreadableAudioStreamError(f"Unreadable audio stream in {source}")
    if is_empty_track(track):
        raise NoAudioStreamError(f"Empty audio track in {source}")
    return track


def _output_plan(track: dict) -> _OutputPlan:
    """The output suffix and ffmpeg codec arguments that keep ``track``."""
    codec = str(track.get("codec_name") or "")
    channels = int(track.get("channels") or 0)
    copy_suffix = _STREAM_COPY_SUFFIXES.get(codec)
    if copy_suffix is not None:
        return _OutputPlan(
            copy_suffix, ["-c:a", "copy"], reencodes_lossy=False, copies_codec=codec
        )
    if codec.startswith(_PCM_CODEC_PREFIX) or codec in _LOSSLESS_CODECS:
        arguments = ["-c:a", "flac"]
        if channels > _FLAC_MAX_CHANNELS:
            arguments += ["-ac", _DOWNMIX_CHANNELS]
        return _OutputPlan(".flac", arguments, reencodes_lossy=False)
    return _opus_plan(channels)


def _opus_plan(channels: int) -> _OutputPlan:
    arguments = ["-c:a", "libopus", "-b:a", _REENCODE_OPUS_BITRATE]
    if channels > _OPUS_MAX_CHANNELS:
        arguments += ["-ac", _DOWNMIX_CHANNELS]
    return _OutputPlan(_REENCODE_SUFFIX, arguments, reencodes_lossy=True)


def _extract_audio_track(
    source_path: str, track: dict, probe: dict
) -> tuple[str, _OutputPlan]:
    """Write ``track`` to a new audio-only file; return it and how it was made.

    A copy that turns out to hold another codec than reported is redone as an
    Opus re-encode (see ``_CopyChangedCodec``).

    Raises:
        AudioExtractionError, ImportServerError: as ``_write_audio_track``.
    """
    plan = _output_plan(track)
    try:
        return _write_audio_track(source_path, track, probe, plan), plan
    except _CopyChangedCodec as exc:
        logger.info("Re-encoding instead of copying: %s", exc)
    plan = _opus_plan(int(track.get("channels") or 0))
    return _write_audio_track(source_path, track, probe, plan), plan


def _write_audio_track(
    source_path: str, track: dict, probe: dict, plan: _OutputPlan
) -> str:
    """Write ``track`` of ``source_path`` to a new file by ``plan``; return it.

    The output starts at zero (``-avoid_negative_ts make_zero``), so a track
    that started late is stored without the leading gap and its duration is
    its length.

    Raises:
        AudioExtractionError: ffmpeg rejected or crashed on the input, or the
            new file did not verify.
        ImportServerError: a failure on the server's side.
        _CopyChangedCodec: see the class.
        The new file is removed in every case.
    """
    # Named after the upload, so whoever cleans up after it finds this file,
    # finished or partial (``remove_finalize_leftovers``).
    source = Path(source_path)
    target = str(source.with_name(f"{source.stem}.{uuid4().hex}{plan.suffix}"))
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", source_path]
    cmd += ["-map", f"0:{track['index']}", *plan.codec_arguments]
    cmd += ["-avoid_negative_ts", "make_zero", target]
    ensure_ffmpeg_in_path()
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=EXTRACT_TIMEOUT_S)
        _verify_extracted_audio(target, source_path, track, probe, plan)
    except _CopyChangedCodec:
        _remove_quietly(target)
        raise
    except (ToolFailure, subprocess.SubprocessError, OSError, RuntimeError) as exc:
        _remove_quietly(target)
        message = f"Could not extract the audio of {source_path}: {_reason(exc)}"
        logger.warning("%s", message)
        if _is_server_fault(exc):
            raise ImportServerError(message) from exc
        raise AudioExtractionError(message) from exc
    return target


def _reason(exc: BaseException) -> str:
    stderr = getattr(exc, "stderr", None)
    if isinstance(stderr, bytes) and stderr:
        return stderr.decode(errors="replace").strip()
    return str(exc)


def _is_server_fault(exc: BaseException) -> bool:
    """The failure lies with the server (tools, disk, time), not the file."""
    if isinstance(exc, (ToolFailure, subprocess.TimeoutExpired, OSError)):
        return True
    if isinstance(exc, subprocess.CalledProcessError):
        # A crash on the file (SIGSEGV, SIGABRT) is the file's fault.
        return killed_by_server_signal(exc.returncode) or (
            exc.returncode > 0
            and any(marker in _reason(exc) for marker in _SERVER_FAULT_MARKERS)
        )
    return False


def _verify_extracted_audio(
    target: str, source_path: str, track: dict, probe: dict, plan: _OutputPlan
) -> None:
    """Check ``target`` is one audio track that holds all of the source track.

    Both lengths are measured the same way, from the packets (``track_span``),
    so a late start or a header that counts the video does not count. Only a
    shorter output is refused: a longer one has lost nothing, and the source's
    span can undercount (a last WavPack block without a duration, concatenated
    MPEG-TS whose timestamps restart). When the source track has no timed
    packets there is nothing to compare, and only the output is checked.

    Raises:
        _CopyChangedCodec: see the class.
        RuntimeError: it is not one audio track, holds no audio or is shorter
            than the source track.
        ToolFailure, UnreadableMediaError: ffprobe could not measure a file.
    """
    out = probe_streams(target)
    streams = out.get("streams") or []
    if (
        len(streams) != 1
        or streams[0].get("codec_type") != "audio"
        or not has_audio(streams[0])
    ):
        raise RuntimeError(f"{target} is not a single audio track")
    written = streams[0].get("codec_name")
    if plan.copies_codec is not None and written != plan.copies_codec:
        raise _CopyChangedCodec(
            f"{source_path}: {plan.copies_codec} copied as {written}"
        )
    out_span = track_span(
        target,
        int(streams[0]["index"]),
        track_end(streams[0], out),
        timeout=EXTRACT_TIMEOUT_S,
    )
    if out_span is None or out_span <= 0:
        raise RuntimeError(f"{target} holds no audio")
    source_span = track_span(
        source_path,
        int(track["index"]),
        track_end(track, probe),
        timeout=EXTRACT_TIMEOUT_S,
    )
    if source_span is None:
        logger.info(
            "No timed packets in %s to compare the extraction with", source_path
        )
        return
    tolerance = max(_DURATION_TOLERANCE_S, source_span * _DURATION_TOLERANCE_RATIO)
    if out_span < source_span - tolerance:
        raise RuntimeError(
            f"{target} runs {out_span:.2f} s where the source track runs "
            f"{source_span:.2f} s"
        )


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Could not remove %s: %s", path, exc)
