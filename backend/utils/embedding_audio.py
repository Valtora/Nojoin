from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from os import PathLike

from backend.core.exceptions import AudioFormatError
from backend.processing.audio_preprocessing import cleanup_stale_pipeline_temp_files
from backend.utils.audio import convert_to_mono_16k
from backend.utils.import_audio import MEDIA_CONTAINER_SUFFIXES
from backend.utils.recording_audio_sync import BROWSER_AUDIO_SEGMENT_SUFFIXES

EMBEDDING_WAV_SUFFIX = "_embedding.wav"

# Upper bound on one decode. ffmpeg decodes far faster than real time, so this
# only ever ends a hung process.
EMBEDDING_DECODE_TIMEOUT_S = 15 * 60

# An embedding WAV older than this has no extraction left reading it: the
# decode is bounded above, and cropping a recording's speakers takes minutes.
_STRANDED_EMBEDDING_WAV_AGE_HOURS = 6

# Containers pyannote cannot crop segments from reliably. Its seek-and-crop
# returns short or empty chunks from Matroska, MPEG-TS/PS and AVI, so imports
# in any of the media containers are read through the MP3 proxy as well.
_PROXY_PREFERRED_SUFFIXES = BROWSER_AUDIO_SEGMENT_SUFFIXES | MEDIA_CONTAINER_SUFFIXES

logger = logging.getLogger(__name__)


def _path_exists(path: str | PathLike[str] | None) -> bool:
    return bool(path) and os.path.exists(path)


def _prefers_proxy(path: str | PathLike[str] | None) -> bool:
    if not path:
        return False
    _, suffix = os.path.splitext(str(path))
    return suffix.lower() in _PROXY_PREFERRED_SUFFIXES


def select_recording_audio_for_embedding(recording) -> str | None:
    """
    Choose the safest audio artifact for speaker-embedding extraction.

    Browser-capture master files may remain in raw container formats such as
    WebM/Ogg/M4A, and imports may arrive in a media container such as MKV or
    MPEG-TS. When a proxy exists for those recordings, prefer the proxy
    because pyannote segment cropping is more reliable against the transcoded
    playback artifact. Without one, the original is returned and
    ``pyannote_readable_audio`` decodes a media container before cropping.
    """

    audio_path = getattr(recording, "audio_path", None)
    proxy_path = getattr(recording, "proxy_path", None)

    audio_exists = _path_exists(audio_path)
    proxy_exists = _path_exists(proxy_path)

    if audio_exists and _prefers_proxy(audio_path) and proxy_exists:
        return str(proxy_path)
    if audio_exists:
        return str(audio_path)
    if proxy_exists:
        return str(proxy_path)
    return None


@contextmanager
def pyannote_readable_audio(audio_path: str) -> Iterator[str]:
    """Yield a path pyannote can crop segments from reliably.

    A media container (MKV, MPEG-TS/PS, AVI and the rest of
    ``MEDIA_CONTAINER_SUFFIXES``) is reached here only when its recording has no
    playback proxy yet. pyannote's crop returns short or empty chunks from
    those, so the audio is decoded to a temporary 16 kHz mono WAV (the
    embedding model's rate), removed on exit. Any other path is yielded as is.

    The decode covers the whole recording, so a caller cropping several
    speakers from one recording should hold one context around all of them.
    The WAV is removed in a finally block, which a worker killed outright never
    reaches, so each decode first sweeps embedding WAVs older than a few hours
    from the same temp dir. The sweep runs here, on the lane that writes them,
    because each worker container has a private /tmp the io lane's daily
    cleanup cannot see.

    Raises:
        AudioFormatError: the decode failed for any reason: ffmpeg could not
            decode the file, could not be started or timed out, or no
            temporary file could be created. The cause may be transient (a
            full temp directory), so callers must not treat it as "nothing
            usable in this audio". Exceptions raised by the caller's own block
            pass through unchanged.
    """
    _, suffix = os.path.splitext(audio_path)
    if suffix.lower() not in MEDIA_CONTAINER_SUFFIXES:
        yield audio_path
        return

    cleanup_stale_pipeline_temp_files(
        max_age_hours=_STRANDED_EMBEDDING_WAV_AGE_HOURS,
        suffixes=(EMBEDDING_WAV_SUFFIX,),
    )
    try:
        temp_path = _decode_to_embedding_wav(audio_path)
    except Exception as exc:
        # Every decode failure is one contract, whatever raised it, so a
        # caller iterating recordings can hold one back and carry on.
        raise AudioFormatError(
            f"Could not decode {audio_path} for embedding extraction: {exc}"
        ) from exc
    try:
        yield temp_path
    finally:
        _remove_embedding_wav(temp_path)


def _decode_to_embedding_wav(audio_path: str) -> str:
    """Decode ``audio_path`` to a new temporary 16 kHz mono WAV; return its path.

    The WAV is removed again if the decode fails.
    """
    temp_fd, temp_path = tempfile.mkstemp(suffix=EMBEDDING_WAV_SUFFIX)
    os.close(temp_fd)
    logger.info("Decoding %s to 16 kHz WAV for embedding extraction", audio_path)
    try:
        convert_to_mono_16k(audio_path, temp_path, timeout=EMBEDDING_DECODE_TIMEOUT_S)
    except BaseException:
        _remove_embedding_wav(temp_path)
        raise
    return temp_path


def _remove_embedding_wav(temp_path: str) -> None:
    try:
        os.remove(temp_path)
    except OSError as exc:
        logger.warning("Could not remove %s: %s", temp_path, exc)
