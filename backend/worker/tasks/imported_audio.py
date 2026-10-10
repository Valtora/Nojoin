"""Keep only the audio of an import, on the CPU lane, before it is processed.

Every import route stores the upload as it arrived, marks the recording QUEUED
with ``KEEPING_AUDIO_STEP`` and queues this task in place of processing. The
task runs ``keep_imported_audio``, points the recording at what it kept and
queues processing and the playback proxy. A file that import refuses, or a
server fault while keeping its audio, marks the recording ERROR and removes its
files, so no recording is left holding a video. See "Imported Media Input" in
docs/ARCHITECTURE.md.

Every write is conditional on the recording still waiting for this task with
the same ``audio_path``, and the upload is deleted only once the kept audio is
stored. A duplicate or re-queued copy is therefore harmless: the copy whose
write lands keeps its audio, every other copy removes only the file it
extracted itself, and a copy that fails before storing anything leaves the
upload for the next one. The cpu worker re-queues imports still waiting when
it starts (``requeue_imports_waiting_for_audio``), since a task lost with a
worker is not redelivered.
"""

import logging
import os
import shutil
from pathlib import Path

from celery.signals import worker_ready
from sqlalchemy import delete, update
from sqlalchemy.orm import Session
from sqlmodel import select

from backend.celery_app import CPU_QUEUE
from backend.core.db import sync_engine
from backend.models.pipeline import RecordingAudioChunk, RecordingAudioWindowManifest
from backend.models.recording import ClientStatus, Recording, RecordingStatus
from backend.utils.audio import get_audio_duration
from backend.utils.import_audio import (
    KEEPING_AUDIO_STEP,
    ImportRefusedError,
    ImportServerError,
    keep_imported_audio,
)
from backend.utils.recording_audio_sync import (
    list_recording_audio_chunks,
    sync_recording_audio_chunks_from_entries,
    sync_recording_audio_window_manifests,
)
from backend.utils.recording_storage import (
    delete_recording_artifacts,
    recording_upload_temp_dir,
)

from .constants import DatabaseTask, celery_app
from .pipeline import _sweeps_recordings

logger = logging.getLogger(__name__)

SERVER_FAILURE_DETAIL = (
    "The server failed while extracting this file's audio. Delete this "
    "recording and import the file again."
)

_IMPORT_SOURCE_KIND = "import"


def _waiting_for_audio(recording_id: int, audio_path: str):
    """The recording still waits for this task to keep the audio of ``audio_path``."""
    return (
        (Recording.id == recording_id)
        & (Recording.status == RecordingStatus.QUEUED)
        & (Recording.processing_step == KEEPING_AUDIO_STEP)
        & (Recording.audio_path == audio_path)
    )


@celery_app.task(
    name="backend.worker.tasks.keep_imported_audio_task",
    base=DatabaseTask,
    bind=True,
)
def keep_imported_audio_task(self, recording_id: int) -> None:
    """Keep the audio of an import, then queue it for processing."""
    session = self.session
    recording = session.get(Recording, recording_id)
    if (
        recording is None
        or recording.status != RecordingStatus.QUEUED
        or recording.processing_step != KEEPING_AUDIO_STEP
    ):
        logger.info("Recording %s is not waiting for its audio; skipped.", recording_id)
        return
    source = recording.audio_path
    proxy_path = recording.proxy_path
    # Hold no transaction open while ffmpeg runs.
    session.rollback()

    try:
        kept = keep_imported_audio(source).path
    except ImportRefusedError as exc:
        logger.info("Refused the import of recording %s: %s", recording_id, exc)
        _fail(session, recording_id, source, proxy_path, exc.detail)
        return
    except ImportServerError:
        logger.error(
            "Could not keep the audio of recording %s", recording_id, exc_info=True
        )
        _fail(session, recording_id, source, proxy_path, SERVER_FAILURE_DETAIL)
        return

    stored = False
    try:
        stored = _store_kept_audio(session, recording_id, source, kept)
    finally:
        if not stored and kept != source:
            _remove_quietly(kept)
    if not stored:
        logger.info("Recording %s was deleted or changed meanwhile.", recording_id)
        return
    if kept != source:
        # Only now: until the kept audio is stored, the upload is all there is.
        try:
            os.remove(source)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.error(
                "Could not delete the upload of recording %s, %s; nothing "
                "refers to it any more: %s",
                recording_id,
                source,
                exc,
            )

    task = celery_app.send_task(
        "backend.worker.tasks.process_recording_task", args=[recording_id]
    )
    session.execute(
        update(Recording)
        .where(Recording.id == recording_id)
        .values(celery_task_id=task.id)
    )
    session.commit()
    if not proxy_path:
        celery_app.send_task(
            "backend.worker.tasks.generate_proxy_task", args=[recording_id]
        )


def _store_kept_audio(
    session: Session, recording_id: int, source: str, kept: str
) -> bool:
    """Point the recording at ``kept`` and clear the step; False if it no
    longer waits for this task."""
    values: dict = {"processing_step": None}
    if kept != source:
        values.update(
            audio_path=kept,
            file_size_bytes=os.stat(kept).st_size,
            duration_seconds=_duration(kept),
        )
    try:
        result = session.execute(
            update(Recording)
            .where(_waiting_for_audio(recording_id, source))
            .values(**values)
        )
        if result.rowcount != 1:
            session.rollback()
            return False
        if kept != source:
            _rebuild_import_window(session, recording_id, kept)
        session.commit()
    except BaseException:
        # The row still waits, with its upload intact, for the next copy.
        session.rollback()
        raise
    return True


def _duration(path: str) -> float | None:
    """The kept audio's length; None lets processing measure it, as it does
    for any import whose length could not be read."""
    try:
        return get_audio_duration(path)
    except RuntimeError as exc:
        logger.warning("Could not read the duration of %s: %s", path, exc)
        return None


def _rebuild_import_window(
    session: Session, recording_id: int, audio_path: str
) -> None:
    """Rebuild the import's audio window from the kept audio.

    /import and chunked finalize build it from the upload
    (``_bootstrap_import_audio_windows``, which the worker cannot import);
    /upload builds none. The upload's staged link is removed with its rows.
    """
    rows = list_recording_audio_chunks(
        session, recording_id, source_kind=_IMPORT_SOURCE_KIND
    )
    if not rows:
        return
    for row in rows:
        _remove_quietly(row.storage_path)
    session.execute(
        delete(RecordingAudioChunk)
        .where(RecordingAudioChunk.recording_id == recording_id)
        .where(RecordingAudioChunk.source_kind == _IMPORT_SOURCE_KIND)
    )
    session.execute(
        delete(RecordingAudioWindowManifest)
        .where(RecordingAudioWindowManifest.recording_id == recording_id)
        .where(RecordingAudioWindowManifest.source_kind == _IMPORT_SOURCE_KIND)
    )
    staged = (
        recording_upload_temp_dir(recording_id, create=True)
        / f"0{Path(audio_path).suffix}"
    )
    try:
        os.link(audio_path, staged)
    except OSError:
        shutil.copy2(audio_path, staged)
    sync_recording_audio_chunks_from_entries(
        session,
        recording_id=recording_id,
        source_kind=_IMPORT_SOURCE_KIND,
        disk_entries=[(0, staged)],
    )
    sync_recording_audio_window_manifests(
        session,
        recording_id=recording_id,
        source_kind=_IMPORT_SOURCE_KIND,
        seal_tail=True,
    )


def _fail(
    session: Session,
    recording_id: int,
    source: str,
    proxy_path: str | None,
    detail: str,
) -> None:
    """Mark the recording ERROR with ``detail`` and remove its files, unless it
    no longer waits for this task."""
    result = session.execute(
        update(Recording)
        .where(_waiting_for_audio(recording_id, source))
        .values(
            status=RecordingStatus.ERROR,
            client_status=ClientStatus.IDLE,
            processing_step=detail[:255],
        )
    )
    if result.rowcount != 1:
        session.rollback()
        return
    session.commit()
    delete_recording_artifacts(
        recording_id=recording_id,
        audio_path=source,
        proxy_path=proxy_path,
        logger=logger,
    )


@worker_ready.connect
def requeue_imports_waiting_for_audio(sender, **kwargs) -> None:
    """On cpu worker startup, re-queue every import still waiting for its audio.

    A task is acknowledged when a worker takes it, so one lost with its worker
    is not redelivered; without this the import would wait forever, since
    reprocess refuses QUEUED. A copy still queued as well is harmless.

    It runs in the prefork pool's parent, which forks every replacement
    child. Upstream's startup sweep never had to care: it runs on the gpu
    lane, whose solo pool forks nothing. A connection left in this process's
    pool would be inherited by each such child, so several children would
    share one database connection; the pool is emptied before returning.
    """
    if not _sweeps_recordings(sender, CPU_QUEUE):
        return
    session = Session(sync_engine)
    try:
        waiting = (
            session.execute(
                select(Recording.id)
                .where(Recording.status == RecordingStatus.QUEUED)
                .where(Recording.processing_step == KEEPING_AUDIO_STEP)
            )
            .scalars()
            .all()
        )
        for recording_id in waiting:
            logger.info(
                "Re-queueing import %s, still waiting for its audio", recording_id
            )
            celery_app.send_task(
                "backend.worker.tasks.keep_imported_audio_task", args=[recording_id]
            )
    except Exception as e:
        logger.error(
            "Failed to re-queue imports waiting for audio: %s", e, exc_info=True
        )
    finally:
        session.close()
        sync_engine.dispose()


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Could not remove %s: %s", path, exc)


__all__ = ["keep_imported_audio_task", "requeue_imports_waiting_for_audio"]
