"""What files an archive will carry, resolved from the database rather than the disk.

Selecting by ``Recording.audio_path`` rather than walking the recordings directory is
what makes proxy shadowing impossible: a recording and its playback proxy share a
filename stem, so a stem-keyed directory walk had to guess between them.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set, Tuple

from backend.core.backup.format import (
    ARCHIVABLE_AUDIO_EXTENSIONS,
    ARCHIVE_QUALITY_COMPRESSED,
)
from backend.core.backup.paths import (
    _build_backup_document_path,
    _build_backup_recording_audio_path,
    _get_document_subpath,
    _get_recording_subpath,
)

logger = logging.getLogger(__name__)


@dataclass
class _AudioPlanEntry:
    """One recording's audio, resolved on disk and assigned an archive member path."""

    source_path: str
    arcname: str
    compress: bool


@dataclass
class _AudioPlan:
    """What audio the archive will carry, derived from the recording rows themselves.

    Selecting by ``Recording.audio_path`` rather than walking the recordings directory is
    what makes proxy shadowing impossible. A recording and its playback proxy share a
    filename stem (``<uuid>.wav`` and ``<uuid>.mp3``), so a stem-keyed directory walk had
    to guess between them and could archive the mono, lossy proxy as the master audio.
    """

    entries: List[_AudioPlanEntry] = field(default_factory=list)
    # Recording.audio_path as stored on the row -> archive member path.
    arcname_by_audio_path: Dict[str, str] = field(default_factory=dict)
    # Recordings whose row survives but whose audio file could not be found on disk.
    missing_audio: int = 0


@dataclass
class _DocumentPlan:
    """Attached document files the archive will carry.

    Documents are always included: they are capped at UPLOAD_LIMIT_DOCUMENT each and are
    a rounding error next to audio, so a separate toggle would add format surface for no
    real benefit. They are stored verbatim; there is nothing useful to re-encode.
    """

    # (source path on disk, archive member path)
    entries: List[Tuple[str, str]] = field(default_factory=list)
    # Document.file_path as stored on the row -> archive member path.
    arcname_by_file_path: Dict[str, str] = field(default_factory=dict)
    missing_files: int = 0


def _resolve_source_audio_path(
    audio_path: str | None,
    recordings_dir: str | os.PathLike[str],
) -> str | None:
    """Find a recording's audio on disk, or ``None`` if it is not there.

    ``Recording.audio_path`` is stored relative to the process working directory,
    which is ``/app`` in every container. The recordings directory is tried as a
    fallback so a library moved via ``NOJOIN_DATA_DIR`` still resolves.
    """
    if not audio_path:
        return None

    candidates = [os.path.abspath(audio_path)]

    subpath = _get_recording_subpath(audio_path)
    if subpath:
        candidates.append(
            os.path.abspath(os.path.join(os.fspath(recordings_dir), subpath))
        )

    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate

    return None


def _build_audio_plan(
    recording_rows: List[Any],
    recordings_dir: str | os.PathLike[str],
    archive_quality: str,
) -> _AudioPlan:
    """Resolve every recording row's audio and decide how it enters the archive."""
    plan = _AudioPlan()
    claimed_arcnames: Set[str] = set()

    for row in recording_rows:
        audio_path = getattr(row, "audio_path", None)
        if not audio_path:
            continue

        source_path = _resolve_source_audio_path(audio_path, recordings_dir)
        if source_path is None:
            logger.warning(
                "Recording audio not found on disk; archiving metadata only: %s",
                audio_path,
            )
            plan.missing_audio += 1
            continue

        extension = os.path.splitext(source_path)[1].lower()
        if extension not in ARCHIVABLE_AUDIO_EXTENSIONS:
            logger.warning(
                "Recording audio has an unsupported extension %r; "
                "archiving metadata only: %s",
                extension,
                audio_path,
            )
            plan.missing_audio += 1
            continue

        # Already-Opus audio is copied verbatim under either quality: re-encoding it
        # would be a pointless generation loss.
        compress = (
            archive_quality == ARCHIVE_QUALITY_COMPRESSED and extension != ".opus"
        )
        arc_extension = ".opus" if compress else extension

        arcname = _build_backup_recording_audio_path(audio_path, arc_extension)
        if not arcname or arcname in claimed_arcnames:
            continue

        claimed_arcnames.add(arcname)
        plan.entries.append(
            _AudioPlanEntry(source_path=source_path, arcname=arcname, compress=compress)
        )
        plan.arcname_by_audio_path[audio_path] = arcname

    return plan


def _build_document_plan(
    document_rows: List[Any],
    documents_dir: str | os.PathLike[str],
) -> _DocumentPlan:
    plan = _DocumentPlan()
    claimed_arcnames: Set[str] = set()

    for row in document_rows:
        file_path = getattr(row, "file_path", None)
        if not file_path:
            continue

        source_path = None
        candidates = [os.path.abspath(file_path)]
        subpath = _get_document_subpath(file_path)
        if subpath:
            candidates.append(
                os.path.abspath(os.path.join(os.fspath(documents_dir), subpath))
            )
        for candidate in candidates:
            if os.path.isfile(candidate):
                source_path = candidate
                break

        if source_path is None:
            logger.warning(
                "Document file not found on disk; archiving metadata only: %s",
                file_path,
            )
            plan.missing_files += 1
            continue

        arcname = _build_backup_document_path(file_path)
        if not arcname or arcname in claimed_arcnames:
            continue

        claimed_arcnames.add(arcname)
        plan.entries.append((source_path, arcname))
        plan.arcname_by_file_path[file_path] = arcname

    return plan
