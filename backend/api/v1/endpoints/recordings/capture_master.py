"""Prepare Stop's audio without retaining any publication or audio locks."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import backend.api.v1.endpoints.recordings as recordings_module
from backend.models.pipeline import RecordingAudioChunk
from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.utils.recording_audio_sync import build_recording_audio_chunk_fields
from backend.utils.stored_paths import require_recording_path

RETRY_FINALIZE_DETAIL = "Capture changed while preparing audio; retry Stop."
CaptureBoundary = tuple[str | None, str | None, bool] | None


async def _capture_boundary(db: AsyncSession, recording_id: int) -> CaptureBoundary:
    state = (
        await db.execute(
            select(Transcript.settling_state).where(
                Transcript.recording_id == recording_id
            )
        )
    ).scalar_one_or_none()
    return (
        (state.get("generation"), state.get("phase"), bool(state.get("sealed")))
        if state
        else None
    )


def _changed_capture() -> HTTPException:
    return HTTPException(status_code=409, detail=RETRY_FINALIZE_DETAIL)


def _file_identity(path: Path) -> tuple[int, int, int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _capture_files(
    recording_id: int,
) -> tuple[tuple[str, tuple[int, int, int, int]], ...]:
    directory = recordings_module.recording_upload_temp_dir(recording_id, create=False)
    if not directory.exists():
        return ()
    return tuple(
        sorted(
            (path.name, _file_identity(path))
            for path in directory.iterdir()
            if path.suffix
            in {
                *recordings_module.BROWSER_AUDIO_SEGMENT_SUFFIXES,
                ".wav",
                ".flac",
                ".corrupt",
            }
        )
    )


def _chunk_identity(rows: list[RecordingAudioChunk]) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (row.sequence_no, row.storage_path, row.sha256, row.byte_size, row.duration_ms)
        for row in rows
    )


@dataclass(frozen=True)
class CaptureSnapshot:
    """Capture facts which a prepared master is allowed to publish for."""

    recording_id: int
    epoch: int
    status: RecordingStatus
    audio_path: str
    boundary: CaptureBoundary

    def validate(self, recording: Recording) -> None:
        recordings_module._ensure_recording_can_finalize_upload(recording)
        if (
            recording.capture_epoch != self.epoch
            or recording.status != self.status
            or recording.audio_path != self.audio_path
        ):
            raise _changed_capture()


@dataclass
class PreparedCaptureMaster:
    """An isolated candidate plus the input snapshot to recheck under the fence."""

    capture: CaptureSnapshot
    chunks: list[RecordingAudioChunk]
    files: tuple[tuple[str, tuple[int, int, int, int]], ...]
    final_path: Path
    candidate_path: Path
    duration_seconds: float | None = None
    identity: tuple[int, int, int, int] | None = None

    async def build(self) -> None:
        """Join the master while other requests remain free to publish edits."""
        self.duration_seconds = await asyncio.to_thread(
            recordings_module.write_browser_master_audio,
            [require_recording_path(row.storage_path) for row in self.chunks],
            self.candidate_path,
        )
        self.identity = _file_identity(self.candidate_path)
        if self.identity[2] <= 0:
            raise _changed_capture()

    async def validate(
        self,
        db: AsyncSession,
        recording: Recording,
        *,
        master: bool = True,
        sealed: bool = False,
    ) -> None:
        """Recheck metadata only; content reads and encoding have already finished."""
        self.capture.validate(recording)
        boundary = self.capture.boundary
        if sealed and boundary is not None:
            boundary = (boundary[0], "sealed", True)
        if await _capture_boundary(db, recording.id) != boundary:
            raise _changed_capture()
        rows = await recordings_module._list_recording_audio_chunks(
            db, recording_id=recording.id, source_kind="browser"
        )
        try:
            if (
                _chunk_identity(rows) != _chunk_identity(self.chunks)
                or _capture_files(recording.id) != self.files
            ):
                raise _changed_capture()
            if master and (
                self.identity is None
                or self.identity[2] <= 0
                or _file_identity(self.candidate_path) != self.identity
            ):
                raise _changed_capture()
        except OSError as exc:
            raise _changed_capture() from exc


async def _sync_prepared_chunks(
    db: AsyncSession, recording: Recording, capture: CaptureSnapshot
) -> tuple[
    list[RecordingAudioChunk], tuple[tuple[str, tuple[int, int, int, int]], ...]
]:
    """Hash/probe files before taking the short audio bookkeeping lock."""
    directory = recordings_module.recording_upload_temp_dir(recording.id, create=False)
    files = await asyncio.to_thread(_capture_files, capture.recording_id)
    entries = sorted(
        (int(Path(name).stem), directory / name)
        for name, _ in files
        if Path(name).suffix == ".wav" and Path(name).stem.isdecimal()
    )
    fields = await asyncio.to_thread(
        lambda: {
            sequence: build_recording_audio_chunk_fields(
                sequence=sequence, source_kind="browser", storage_path=path
            )
            for sequence, path in entries
        }
    )
    await recordings_module._lock_capture(db, recording)
    capture.validate(recording)
    if _capture_files(capture.recording_id) != files:
        raise _changed_capture()
    await db.run_sync(
        lambda session: recordings_module.sync_recording_audio_chunks_from_entries(
            session,
            recording_id=recording.id,
            source_kind="browser",
            disk_entries=entries,
            prebuilt_fields=fields,
        )
    )
    rows = await recordings_module._list_recording_audio_chunks(
        db, recording_id=recording.id, source_kind="browser"
    )
    # Detached values survive commit even for callers using expire_on_commit.
    result = [RecordingAudioChunk(**row.model_dump()) for row in rows]
    await db.commit()
    return result, files


async def _prepare_capture_master(
    db: AsyncSession, recording: Recording, epoch: int | None
) -> PreparedCaptureMaster:
    """Snapshot and prepare inputs, releasing every lock before slow work."""
    await recordings_module._lock_capture(db, recording)
    recordings_module._ensure_capture_epoch(recording, epoch)
    recordings_module._ensure_recording_can_finalize_upload(recording)
    capture = CaptureSnapshot(
        recording.id,
        recording.capture_epoch,
        recording.status,
        recording.audio_path,
        await _capture_boundary(db, recording.id),
    )
    final_path = Path(recordings_module._browser_master_output_path(recording))
    await db.commit()
    # Refresh without taking a lock: async sessions may expire on commit.
    await db.refresh(recording)
    rows, files = await _sync_prepared_chunks(db, recording, capture)
    pending = await asyncio.to_thread(
        recordings_module._find_pending_transcode_sequences,
        capture.recording_id,
        chunk_rows=rows,
    )
    if pending:
        failed = (
            await recordings_module._transcode_pending_browser_segments_for_finalize(
                capture.recording_id, pending
            )
        )
        # Rescue encoding runs without either lock. Only the indexed row sync
        # below takes the audio lock; it revalidates the capture before commit.
        await db.refresh(recording)
        if failed:
            await asyncio.to_thread(
                recordings_module._quarantine_corrupt_browser_segments,
                capture.recording_id,
                failed,
            )
        rows, files = await _sync_prepared_chunks(db, recording, capture)
        pending = await asyncio.to_thread(
            recordings_module._find_pending_transcode_sequences,
            capture.recording_id,
            chunk_rows=rows,
        )
    if pending:
        raise HTTPException(
            status_code=409,
            detail="Recording upload is still in progress; finalize after all segment uploads complete.",
        )
    if not rows:
        raise HTTPException(status_code=400, detail="No valid segments found")
    corrupt = recordings_module._list_corrupt_browser_segment_sequences(
        capture.recording_id
    )
    if any(
        sequence not in corrupt
        for sequence in recordings_module._find_missing_chunk_sequences(rows)
    ):
        raise HTTPException(
            status_code=409,
            detail="Recording upload is still in progress; finalize after all segment uploads complete.",
        )
    return PreparedCaptureMaster(
        capture,
        rows,
        files,
        final_path,
        final_path.with_name(f"{final_path.name}.{uuid4().hex}.capture-tmp"),
    )


async def prepare_capture_master(
    db: AsyncSession, recording: Recording, epoch: int | None
) -> PreparedCaptureMaster:
    """Turn concurrently removed/replaced preparation inputs into a retry."""
    try:
        return await _prepare_capture_master(db, recording, epoch)
    except OSError as exc:
        raise _changed_capture() from exc
