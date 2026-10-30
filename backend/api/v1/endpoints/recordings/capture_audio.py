"""Async wrappers for live capture chunk bookkeeping (issue #417).

Each segment registers in constant time under a per-recording advisory lock,
and ``recordings`` is written at most every few seconds per capture rather than
on every segment, so a segment upload never waits behind another task's work on
the recording row.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import timedelta
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

import backend.api.v1.endpoints.recordings as recordings_module
from backend.models.pipeline import RecordingAudioChunk
from backend.models.recording import Recording
from backend.utils.browser_capture_audio import (
    BrowserAudioFormatError,
    decode_browser_flac_segment,
)
from backend.utils.recording_audio_registration import (
    ChunkStream,
    advance_recording_audio_cursor,
    register_recording_audio_chunk,
    reopen_recording_audio_cursor,
    seal_recording_audio_cursor,
)
from backend.utils.recording_audio_sync import (
    SEGMENT_CORRUPT_SUFFIX,
    build_recording_audio_chunk_fields,
    lock_recording_audio,
)
from backend.utils.time import utc_now

from .constants import CAPTURE_SUPERSEDED_CODE, CAPTURE_SUPERSEDED_MESSAGE

logger = logging.getLogger(__name__)

BROWSER_CHUNK_SOURCE_KIND = "browser"

# Finest resolution kept for ``last_activity_at`` during capture. Its readers
# (the capture wall span) need seconds, not the 2 s segment cadence, and every
# write takes the recording row lock.
LAST_ACTIVITY_WRITE_INTERVAL = timedelta(seconds=15)


async def _register_recording_audio_chunk(
    db: AsyncSession,
    *,
    recording_id: int,
    sequence: int,
    storage_path: Path,
    temp_dir: Path | None,
) -> RecordingAudioChunk | None:
    # Hashing and reading the WAV header happen off the event loop and before
    # the lock, so the lock covers only a handful of indexed queries.
    fields = await asyncio.to_thread(
        build_recording_audio_chunk_fields,
        sequence=sequence,
        source_kind=BROWSER_CHUNK_SOURCE_KIND,
        storage_path=storage_path,
    )
    stream = ChunkStream(recording_id, BROWSER_CHUNK_SOURCE_KIND, temp_dir)
    return await db.run_sync(
        lambda session: register_recording_audio_chunk(
            session,
            stream,
            sequence=sequence,
            storage_path=storage_path,
            fields=fields,
        )
    )


async def _advance_recording_audio_cursor(
    db: AsyncSession, *, recording_id: int, temp_dir: Path | None
) -> None:
    stream = ChunkStream(recording_id, BROWSER_CHUNK_SOURCE_KIND, temp_dir)
    await db.run_sync(lambda session: advance_recording_audio_cursor(session, stream))


async def _seal_recording_audio_cursor(db: AsyncSession, *, recording_id: int) -> None:
    await db.run_sync(
        lambda session: seal_recording_audio_cursor(
            session,
            recording_id=recording_id,
            source_kind=BROWSER_CHUNK_SOURCE_KIND,
        )
    )


async def _reopen_recording_audio_cursor(
    db: AsyncSession, *, recording_id: int
) -> None:
    await db.run_sync(
        lambda session: reopen_recording_audio_cursor(
            session,
            recording_id=recording_id,
            source_kind=BROWSER_CHUNK_SOURCE_KIND,
        )
    )


async def _lock_capture(db: AsyncSession, recording: Recording) -> None:
    """Hold the capture still while its epoch is checked or changed.

    Takes the recording's audio lock until the transaction ends and reads the
    row again, so the epoch checked is the one any resume has committed. A
    segment lands on disk under this lock, and resume issues an epoch under it,
    so a segment from the old epoch either lands before the resume reads the
    last sequence or is refused (issue #570).
    """
    await db.run_sync(lambda session: lock_recording_audio(session, recording.id))
    await db.refresh(recording)


def _ensure_capture_epoch(recording: Recording, epoch: int | None) -> None:
    """Refuse a request from a tab whose capture was resumed elsewhere.

    A stop or discard from the resume-or-discard dialog sends the epoch of the
    offer it acts on, so it is refused once another tab or device has taken
    the capture over since (issue #789). A request without an epoch is
    accepted: tabs on a build from before epochs send none, nor does the
    recordings list for a recording that browser was not offered.
    """
    if epoch is not None and epoch != recording.capture_epoch:
        raise HTTPException(
            status_code=409,
            detail={
                "code": CAPTURE_SUPERSEDED_CODE,
                "message": CAPTURE_SUPERSEDED_MESSAGE,
            },
        )


async def _touch_recording_last_activity(db: AsyncSession, recording_id: int) -> None:
    """Record capture activity, writing the row at most once per interval."""
    now = utc_now()
    await db.execute(
        update(Recording)
        .where(Recording.id == recording_id)
        .where(
            or_(
                Recording.last_activity_at.is_(None),
                Recording.last_activity_at < now - LAST_ACTIVITY_WRITE_INTERVAL,
            )
        )
        .values(last_activity_at=now)
        .execution_options(synchronize_session=False)
    )


def _segment_filename(sequence: int, suffix: str) -> str:
    # The sequence comes from the request. FastAPI has already made it a
    # non-negative int; basename keeps any path built from it inside the
    # recording's directory regardless, as the upload route does.
    return os.path.basename(f"{int(sequence)}{suffix}")


async def _accept_browser_flac_segment(
    db: AsyncSession,
    *,
    recording_id: int,
    sequence: int,
    flac_path: Path,
    temp_dir: Path,
) -> bool:
    """Decode an uploaded FLAC segment and register it. False if it was corrupt.

    A segment that will not decode is quarantined as a tombstone and the upload
    still succeeds. Refusing it would make the browser retry the same bytes
    until its uploader stalled and paused the meeting; accepting it costs one
    2 s gap, which finalize already knows how to accept.
    """
    wav_path = temp_dir / _segment_filename(sequence, ".wav")
    try:
        await asyncio.to_thread(decode_browser_flac_segment, flac_path, wav_path)
    except BrowserAudioFormatError as exc:
        corrupt_path = temp_dir / _segment_filename(sequence, SEGMENT_CORRUPT_SUFFIX)
        os.replace(flac_path, corrupt_path)
        logger.error(
            "Quarantined undecodable FLAC segment %s for recording %s: %s",
            sequence,
            recording_id,
            exc,
        )
        try:
            recordings_module.record_pipeline_metric(
                stage="segment_corrupt_skipped",
                recording_id=recording_id,
                payload={
                    "sequence": sequence,
                    "quarantined_path": str(corrupt_path),
                    "error": str(exc),
                },
                log=logger,
            )
        except Exception as metric_error:  # noqa: BLE001 -- boundary: metrics must never fail an upload
            logger.warning(
                "Failed to record corrupt segment metric for recording %s segment %s: %s",
                recording_id,
                sequence,
                metric_error,
            )
        await _advance_recording_audio_cursor(
            db, recording_id=recording_id, temp_dir=temp_dir
        )
        return False

    flac_path.unlink(missing_ok=True)
    row = await _register_recording_audio_chunk(
        db,
        recording_id=recording_id,
        sequence=sequence,
        storage_path=wav_path,
        temp_dir=temp_dir,
    )
    return row is not None
