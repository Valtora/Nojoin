from __future__ import annotations

import os
from os import PathLike

from backend.utils.audio import MEDIA_CONTAINER_SUFFIXES
from backend.utils.recording_audio_sync import BROWSER_AUDIO_SEGMENT_SUFFIXES

# Containers pyannote cannot crop segments from reliably. Its seek-and-crop
# returns short or empty chunks from Matroska, MPEG-TS/PS and AVI, so imports
# in any of the media containers are read through the MP3 proxy as well.
_PROXY_PREFERRED_SUFFIXES = BROWSER_AUDIO_SEGMENT_SUFFIXES | MEDIA_CONTAINER_SUFFIXES


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
    playback artifact.
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
