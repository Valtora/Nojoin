from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from os import PathLike

from backend.core.exceptions import AudioFormatError
from backend.utils.audio import MEDIA_CONTAINER_SUFFIXES, convert_to_mono_16k
from backend.utils.recording_audio_sync import BROWSER_AUDIO_SEGMENT_SUFFIXES

# Swept by audio_preprocessing.cleanup_stale_pipeline_temp_files.
EMBEDDING_WAV_SUFFIX = "_embedding.wav"

# Upper bound on one decode. ffmpeg decodes far faster than real time, so this
# only ever ends a hung process.
EMBEDDING_DECODE_TIMEOUT_S = 15 * 60

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
    A worker killed mid-decode can strand the WAV; the daily pipeline
    temp-file sweep reclaims it by its ``_embedding.wav`` suffix.

    Raises:
        AudioFormatError: ffmpeg could not decode the file, or timed out. The
            cause may be transient (a full temp directory), so callers must not
            treat it as "nothing usable in this audio".
    """
    _, suffix = os.path.splitext(audio_path)
    if suffix.lower() not in MEDIA_CONTAINER_SUFFIXES:
        yield audio_path
        return

    temp_fd, temp_path = tempfile.mkstemp(suffix=EMBEDDING_WAV_SUFFIX)
    os.close(temp_fd)
    try:
        logger.info("Decoding %s to 16 kHz WAV for embedding extraction", audio_path)
        try:
            convert_to_mono_16k(
                audio_path, temp_path, timeout=EMBEDDING_DECODE_TIMEOUT_S
            )
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            raise AudioFormatError(
                f"Could not decode {audio_path} for embedding extraction: {exc}"
            ) from exc
        yield temp_path
    finally:
        try:
            os.remove(temp_path)
        except OSError as exc:
            logger.warning("Could not remove %s: %s", temp_path, exc)
