"""Path and identity helpers for archive members and on-disk files.

Pure functions over strings. The identity helpers decide when two recordings are the
same meeting, which is what makes a backup mergeable into a target that already holds
some of its rows.
"""

import os
from typing import Any


def _get_subpath_after(path: str | None, marker: str) -> str | None:
    """Return the portion of ``path`` below the last ``marker`` directory component.

    Falls back to the bare filename so a path stored in an unexpected layout still
    yields a stable archive member name.
    """
    if not path:
        return None

    normalized = str(path).strip().replace("\\", "/")
    parts = [part for part in normalized.split("/") if part and part != "."]
    if not parts:
        return None

    for index in range(len(parts) - 1, -1, -1):
        if parts[index].lower() == marker:
            tail = parts[index + 1 :]
            if tail:
                return "/".join(tail)
            break

    return parts[-1]


def _get_recording_subpath(audio_path: str | None) -> str | None:
    return _get_subpath_after(audio_path, "recordings")


def _get_document_subpath(file_path: str | None) -> str | None:
    return _get_subpath_after(file_path, "documents")


def _build_backup_document_path(file_path: str | None) -> str | None:
    subpath = _get_document_subpath(file_path)
    if not subpath:
        return None
    return os.path.join("documents", subpath)


def _build_runtime_document_path(
    file_path: str | None,
    documents_dir: str | os.PathLike[str],
) -> str | None:
    subpath = _get_document_subpath(file_path)
    if not subpath:
        return None

    target_abs = os.path.abspath(os.path.join(os.fspath(documents_dir), subpath))
    cwd = os.path.abspath(os.getcwd())

    try:
        if os.path.commonpath([cwd, target_abs]) == cwd:
            return os.path.relpath(target_abs, cwd)
    except ValueError:
        pass

    return target_abs


def _get_recording_identity(audio_path: str | None) -> str | None:
    subpath = _get_recording_subpath(audio_path)
    if not subpath:
        return None

    stem, _ = os.path.splitext(subpath)
    return os.path.normcase(stem)


def _normalise_meeting_uid(meeting_uid: Any) -> str | None:
    if meeting_uid is None:
        return None

    normalized = str(meeting_uid).strip().lower()
    return normalized or None


def _normalise_public_id(public_id: Any) -> str | None:
    if public_id is None:
        return None

    normalized = str(public_id).strip().lower()
    return normalized or None


def _get_recording_match_keys(
    audio_path: str | None,
    meeting_uid: Any = None,
    public_id: Any = None,
) -> set[str]:
    """
    Returns every identifier key under which two recordings should be considered the same.
    Prefers durable identifiers (meeting_uid, public_id) and falls back to the legacy audio-path
    stem for backups created before those columns existed.
    """
    keys: set[str] = set()
    normalized_uid = _normalise_meeting_uid(meeting_uid)
    if normalized_uid:
        keys.add(f"meeting_uid:{normalized_uid}")
    normalized_pid = _normalise_public_id(public_id)
    if normalized_pid:
        keys.add(f"public_id:{normalized_pid}")
    if not keys:
        legacy_identity = _get_recording_identity(audio_path)
        if legacy_identity:
            keys.add(f"audio_path:{legacy_identity}")
    return keys


def _get_recording_match_key(
    audio_path: str | None,
    meeting_uid: Any = None,
    public_id: Any = None,
) -> str | None:
    """
    Returns the single highest-priority match key. Retained for callers that only need a
    primary identifier; prefer ``_get_recording_match_keys`` when comparing two records.
    """
    normalized_uid = _normalise_meeting_uid(meeting_uid)
    if normalized_uid:
        return f"meeting_uid:{normalized_uid}"

    normalized_pid = _normalise_public_id(public_id)
    if normalized_pid:
        return f"public_id:{normalized_pid}"

    legacy_identity = _get_recording_identity(audio_path)
    if legacy_identity:
        return f"audio_path:{legacy_identity}"

    return None


def _build_backup_recording_audio_path(
    audio_path: str | None, extension: str = ".opus"
) -> str | None:
    subpath = _get_recording_subpath(audio_path)
    if not subpath:
        return None

    stem, _ = os.path.splitext(subpath)
    return os.path.join("recordings", stem + extension)


def _build_runtime_recording_audio_path(
    audio_path: str | None,
    recordings_dir: str | os.PathLike[str],
) -> str | None:
    """Map an archived member path back onto a runtime path under the recordings dir.

    The archived extension is preserved rather than forced to ``.opus``, so an
    Original-quality archive restores as the format it was taken in. Legacy archives
    already carry ``.opus`` and are therefore unaffected.
    """
    subpath = _get_recording_subpath(audio_path)
    if not subpath:
        return None

    target_abs = os.path.abspath(os.path.join(os.fspath(recordings_dir), subpath))
    cwd = os.path.abspath(os.getcwd())

    try:
        if os.path.commonpath([cwd, target_abs]) == cwd:
            return os.path.relpath(target_abs, cwd)
    except ValueError:
        pass

    return target_abs
