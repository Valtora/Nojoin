"""Attached document files on disk: written before their row, removed after it.

Documents are stored flat in the documents directory as ``<uuid>.<ext>``, apart
from the recording's own audio, so removing a recording's audio leaves them behind.
Each delete path collects its documents' paths before the rows go and removes the
files once the delete has committed. An upload writes its file before inserting
its row, and removes the file again if that insert fails.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Sequence
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from backend.models.document import Document
from backend.utils.db_batching import bind_batches
from backend.utils.path_manager import PathManager
from backend.utils.recording_storage import resolve_path_within_root


def documents_root_dir() -> Path:
    return PathManager().documents_directory


async def recording_document_paths(
    db: AsyncSession, recording_ids: Sequence[int]
) -> list[str]:
    """The stored file paths of every document attached to these recordings.

    Read before the recordings are deleted: the rows go with them by cascade, and
    with the rows the only record of where the files are. The caller must hold
    the recordings locked (``FOR UPDATE``) first. A document insert waits on that
    lock, so no upload can commit a row this read misses and the cascade deletes.
    """
    paths: list[str] = []
    for id_batch in bind_batches(list(recording_ids)):
        result = await db.execute(
            select(Document.file_path).where(col(Document.recording_id).in_(id_batch))
        )
        paths.extend(path for path in result.scalars().all() if path)
    return paths


def _unlink_document_files(
    file_paths: Iterable[str | None], *, logger: logging.Logger
) -> None:
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


async def delete_document_files(
    file_paths: Iterable[str | None], *, logger: logging.Logger
) -> None:
    """Remove document files, but only ones inside the documents directory.

    A path that resolves anywhere else, through a legacy layout, a symlink or an
    edited row, is left alone and logged. A file that is already gone is not an
    error, and one that cannot be removed is logged without stopping the rest.
    Called after the deleting transaction commits, so a failed commit never
    leaves a row pointing at a removed file. The resolving and unlinking run in
    a worker thread, off the event loop.
    """
    await asyncio.to_thread(_unlink_document_files, list(file_paths), logger=logger)


async def insert_document_or_remove_file(
    db: AsyncSession, document: Document, *, logger: logging.Logger
) -> None:
    """Insert a new document's row, whose file is already written; commit is the caller's.

    If the insert fails, nothing would ever reference the file, so it is removed.
    That is what happens when the recording is deleted during the upload: the
    insert waits on the delete's lock, then fails its foreign key. Only the insert
    is guarded. A commit that raises may still have landed, so its file stays.
    """
    file_path = document.file_path
    db.add(document)
    try:
        await db.flush()
    except Exception:
        await delete_document_files([file_path], logger=logger)
        await db.rollback()
        raise
