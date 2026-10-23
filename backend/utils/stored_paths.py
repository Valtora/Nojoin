"""The directories stored files may live in, and the check every read makes.

A recording's audio, proxy and segments, and a document's file, are read only
from inside their own directories, with links followed before the check. A
stored path anywhere else is treated as no file at all.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from backend.utils.path_manager import PathManager
from backend.utils.recording_storage import (
    recordings_root_dir,
    resolve_path_within_root,
)


def recording_roots() -> tuple[Path, ...]:
    """The directories a recording's files may live in.

    Uploads, imports and captures write under :func:`recordings_root_dir`;
    restores and the demo seed write under the data directory's recordings
    directory. The two differ whenever either has been moved.
    """
    return recordings_root_dir(create=False), PathManager().recordings_directory


def document_roots() -> tuple[Path, ...]:
    """The directories a document's file may live in."""
    return (PathManager().documents_directory,)


def _within(stored_path: str | None, roots: Iterable[Path]) -> str | None:
    if stored_path and any(
        resolve_path_within_root(stored_path, root) is not None for root in roots
    ):
        return stored_path
    return None


def confined_recording_path(stored_path: str | None) -> str | None:
    """``stored_path`` when it lies inside a recordings directory, else None."""
    return _within(stored_path, recording_roots())


def require_recording_path(stored_path: str | None) -> str:
    """``stored_path``, which must lie inside a recordings directory.

    Raises:
        ValueError: it does not, so nothing is read or written there.
    """
    confined = confined_recording_path(stored_path)
    if confined is None:
        raise ValueError("A recording file is outside the recordings directories.")
    return confined


def confined_document_path(stored_path: str | None) -> str | None:
    """``stored_path`` when it lies inside the documents directory, else None."""
    return _within(stored_path, document_roots())
