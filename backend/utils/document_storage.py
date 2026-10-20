"""Attached document files on disk: removal with their recording, and the orphan sweep.

Documents are stored flat in the documents directory as ``<uuid>.<ext>``, apart
from the recording's own audio, so removing a recording's audio leaves them behind.
Each delete path collects its documents' paths before the rows go and removes the
files once the delete has committed; the sweep reclaims whatever that misses.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterable, Sequence
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from backend.models.document import Document
from backend.utils.db_batching import bind_batches
from backend.utils.path_manager import PathManager
from backend.utils.recording_storage import resolve_path_within_root

# How old an unreferenced file must be before the sweep removes it. An upload
# writes its file before it commits its row, so for a moment a live document's
# file has no row. The body has been received in full before the file is
# written, so that moment is one commit long. A day is far beyond it and matches
# the retention of the other temporary upload storage.
ORPHAN_DOCUMENT_GRACE_HOURS = 24

# Rows fetched per round trip while the sweep reads every document's path.
_ROW_BATCH = 5_000


def documents_root_dir() -> Path:
    return PathManager().documents_directory


async def recording_document_paths(
    db: AsyncSession, recording_ids: Sequence[int]
) -> list[str]:
    """The stored file paths of every document attached to these recordings.

    Read before the recordings are deleted: the rows go with them by cascade, and
    with the rows the only record of where the files are.
    """
    paths: list[str] = []
    for id_batch in bind_batches(list(recording_ids)):
        result = await db.execute(
            select(Document.file_path).where(col(Document.recording_id).in_(id_batch))
        )
        paths.extend(path for path in result.scalars().all() if path)
    return paths


def delete_document_files(
    file_paths: Iterable[str | None], *, logger: logging.Logger
) -> None:
    """Remove document files, but only ones inside the documents directory.

    A path that resolves anywhere else, through a legacy layout, a symlink or an
    edited row, is left alone and logged. A file that is already gone is not an
    error. Called after the deleting transaction commits, so a failed commit
    never leaves a row pointing at a removed file.
    """
    root = documents_root_dir()
    for raw_path in file_paths:
        if not raw_path:
            continue
        resolved = resolve_path_within_root(raw_path, root)
        if resolved is None:
            logger.warning(
                "Left document file %s in place: it is outside the documents directory %s",
                raw_path,
                root,
            )
            continue
        try:
            resolved.unlink(missing_ok=True)
        except OSError as error:
            logger.warning("Failed to delete document file %s: %s", resolved, error)


def _stored_name(file_path: str) -> str:
    return file_path.replace("\\", "/").rsplit("/", 1)[-1]


def _orphan_candidates(root: Path, cutoff: float) -> dict[str, int]:
    """Name to size of each regular file in ``root`` last modified before ``cutoff``.

    Symlinks and subdirectories are never candidates.
    """
    candidates: dict[str, int] = {}
    with os.scandir(root) as entries:
        for entry in entries:
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.st_mtime < cutoff:
                candidates[entry.name] = stat.st_size
    return candidates


def cleanup_orphaned_document_files(
    session,
    *,
    logger: logging.Logger,
    grace_hours: int = ORPHAN_DOCUMENT_GRACE_HOURS,
    now: float | None = None,
) -> int:
    """Remove files in the documents directory that no document row references.

    Reclaims files left behind by recording deletes made before those removed
    documents, by a process that died between a delete's commit and its unlink,
    and by uploads whose row never committed. Returns the number removed.

    The directory is scanned before the rows are read. A file that appears after
    the scan is not considered, and a file the scan saw is kept if its row
    committed before the read, so a restore, which commits its rows before it
    moves their files into place, cannot lose one. Rows are matched by file name,
    which holds a UUID, so a row stored with a relative or differently rooted
    path still protects its file.

    Cost, measured at 101,000 files and 100,000 rows on PostgreSQL: 1.3 s and
    18 MB peak. That is one scan with one stat per entry, then the paths streamed
    in batches against a map of the old files, once a day.
    """
    root = documents_root_dir()
    if not root.is_dir():
        return 0

    cutoff = (now if now is not None else time.time()) - grace_hours * 3600
    candidates = _orphan_candidates(root, cutoff)
    if not candidates:
        return 0

    # Streamed, so only the candidates are ever held in full.
    rows = session.exec(
        select(Document.file_path).execution_options(yield_per=_ROW_BATCH)
    )
    for path in rows:
        if path:
            candidates.pop(_stored_name(path), None)

    removed = 0
    reclaimed_bytes = 0
    for name, size in candidates.items():
        path = root / name
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as error:
            logger.warning(
                "Failed to delete orphaned document file %s: %s", path, error
            )
            continue
        removed += 1
        reclaimed_bytes += size

    logger.info(
        "Orphaned document sweep: removed %s file(s), reclaimed %s bytes",
        removed,
        reclaimed_bytes,
    )
    return removed
