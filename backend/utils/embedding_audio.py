from __future__ import annotations

import os
from os import PathLike

from backend.utils.recording_audio_sync import BROWSER_AUDIO_SEGMENT_SUFFIXES


def _path_exists(path: str | PathLike[str] | None) -> bool:
    return bool(path) and os.path.exists(path)


def _is_browser_capture_raw_audio(path: str | PathLike[str] | None) -> bool:
    if not path:
        return False
    _, suffix = os.path.splitext(str(path))
    return suffix.lower() in BROWSER_AUDIO_SEGMENT_SUFFIXES


def select_recording_audio_for_embedding(recording) -> str | None:
    """
    Choose the safest audio artifact for speaker-embedding extraction.

    Browser-capture master files may remain in raw container formats such as
    WebM/Ogg/M4A. When a proxy exists for those recordings, prefer the proxy
    because pyannote segment cropping is more reliable against the transcoded
    playback artifact.
    """

    audio_path = getattr(recording, "audio_path", None)
    proxy_path = getattr(recording, "proxy_path", None)

    audio_exists = _path_exists(audio_path)
    proxy_exists = _path_exists(proxy_path)

    if audio_exists and _is_browser_capture_raw_audio(audio_path) and proxy_exists:
        return str(proxy_path)
    if audio_exists:
        return str(audio_path)
    if proxy_exists:
        return str(proxy_path)
    return None
