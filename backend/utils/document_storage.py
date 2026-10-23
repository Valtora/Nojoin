"""Attached document files on disk: written before their row, removed after it.

Documents are stored flat in the documents directory, each under a name of its
own, apart from the recording's own audio, so removing a recording's audio leaves
them behind. Each delete path, a restore in Overwrite mode included, collects its
documents' paths before the rows go and removes the files once the delete has
committed. An upload writes its file before inserting
its row, and removes the file again if that insert fails or is cancelled.

The restore imports this module in the workers, whose images do not install
FastAPI, so nothing here may import it (#660). An upload whose recording has
gone raises RecordingGoneError, and the endpoint answers it with the 404.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Sequence
from functools import partial
from pathlib import Path

import anyio.to_thread
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from backend.models.document import Document
from backend.utils.db_batching import bind_batches
from backend.utils.path_manager import PathManager
from backend.utils.recording_storage import (
    resolve_path_within_root,
    resolve_stored_path,
)

# PostgreSQL's SQLSTATE for a foreign key violation.
_FOREIGN_KEY_VIOLATION = "23503"


class RecordingGoneError(LookupError):
    """A new document's recording was deleted before its row went in."""


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


def document_file_identity(raw_path: str | None) -> tuple[object, ...] | None:
    """What decides whether two stored paths name one file, as the unlink sees it.

    For a file that exists, its device and inode, so two spellings of one file
    match however they differ: ``~``, a symlink, a hard link, or the case of a
    name on a case-insensitive mount. For one that does not, the path the unlink
    resolves it to. None for a path that cannot be resolved, which the unlink
    leaves alone.
    """
    resolved = resolve_stored_path(raw_path)
    if resolved is None:
        return None
    try:
        status = resolved.stat()
    except OSError:
        return ("path", str(resolved))
    return ("inode", status.st_dev, status.st_ino)


def unlink_document_files(
    file_paths: Sequence[str | None],
    *,
    logger: logging.Logger,
    root: Path | None = None,
) -> None:
    """The synchronous core of :func:`delete_document_files`, which never raises.

    For callers already off the event loop, such as the restore. ``root`` is the
    documents directory, resolved through :class:`PathManager` when not given.
    """
    try:
        _unlink_each(
            file_paths,
            root=root if root is not None else documents_root_dir(),
            logger=logger,
        )
    except Exception:
        logger.exception("Failed to delete document files %s", file_paths)


def _unlink_each(
    file_paths: Sequence[str | None], *, root: Path, logger: logging.Logger
) -> None:
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
    leaves a row pointing at a removed file; and so, bar a cancellation, it
    never raises, as nothing may turn a committed delete into an error: any
    other failure is logged. The resolving and unlinking run in a worker
    thread, off the event loop. A cancellation can arrive before that
    thread has picked the job up, as it does while the thread pool is busy, and
    the job would then never run; so a cancelled call unlinks on the event loop
    before the cancellation propagates. If the thread had already started, the
    files are unlinked twice, which is harmless: a missing file is not an error.
    The thread comes from anyio's pool, as FastAPI's ``run_in_threadpool`` would
    take it, without importing FastAPI.
    """
    unlink = partial(unlink_document_files, list(file_paths), logger=logger)
    try:
        await anyio.to_thread.run_sync(unlink)
    except asyncio.CancelledError:
        unlink()
        raise


async def insert_document_or_remove_file(
    db: AsyncSession, document: Document, *, logger: logging.Logger
) -> None:
    """Insert a new document's row, whose file is already written; commit is the caller's.

    If the insert fails or is cancelled, nothing would ever reference the file,
    so it is removed before the error propagates. A recording deleted during the
    upload is the usual failure: the insert waits on the delete's lock, then
    fails its foreign key, and raises RecordingGoneError, which the endpoint
    turns into the 404 a missing recording gets everywhere else. Only the insert
    is guarded. A commit that raises may still have landed, so its file stays.
    """
    file_path = document.file_path
    recording_id = document.recording_id
    db.add(document)
    try:
        await db.flush()
    except BaseException as error:
        await delete_document_files([file_path], logger=logger)
        if not isinstance(error, Exception):
            # Cancelled: the request is ending, and closing the session rolls
            # back what the flush left.
            raise
        await db.rollback()
        if (
            isinstance(error, IntegrityError)
            and getattr(error.orig, "sqlstate", None) == _FOREIGN_KEY_VIOLATION
        ):
            raise RecordingGoneError(recording_id) from error
        raise
