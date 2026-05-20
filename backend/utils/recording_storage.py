from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

from sqlmodel import select

from backend.models.pipeline import RecordingAudioChunk
from backend.utils.time import utc_now


RECORDING_UPLOAD_RETENTION_HOURS = 24


def chunk_cleanup_deadline() -> datetime:
    return utc_now() + timedelta(hours=RECORDING_UPLOAD_RETENTION_HOURS)


def recordings_root_dir(*, create: bool = True) -> Path:
    root = Path(os.getenv("RECORDINGS_DIR", "data/recordings"))
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def recordings_temp_dir(*, create: bool = True) -> Path:
    path = recordings_root_dir(create=create) / "temp"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def recordings_failed_dir(*, create: bool = True) -> Path:
    path = recordings_root_dir(create=create) / "failed"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def recording_upload_temp_dir(
    recording_id: int | str,
    *,
    create: bool = False,
) -> Path:
    path = recordings_temp_dir(create=create) / str(recording_id)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_path_within_recordings_root(target_path: str | None) -> Path | None:
    if not target_path:
        return None

    candidate = Path(target_path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate

    try:
        resolved = candidate.resolve()
        root = recordings_root_dir(create=False)
        if not root.is_absolute():
            root = Path.cwd() / root
        root = root.resolve()
        resolved.relative_to(root)
        return resolved
    except (OSError, RuntimeError, ValueError):
        return None


def delete_recording_artifacts(
    *,
    recording_id: int | str | None,
    audio_path: str | None,
    proxy_path: str | None,
    logger: logging.Logger,
) -> None:
    seen: set[Path] = set()

    for raw_path in (audio_path, proxy_path):
        resolved = _resolve_path_within_recordings_root(raw_path)
        if resolved is None or resolved in seen:
            continue

        seen.add(resolved)
        if not resolved.exists():
            continue

        try:
            resolved.unlink()
        except OSError as error:
            logger.warning("Failed to delete recording file %s: %s", resolved, error)

    if recording_id is None:
        return

    temp_dir = recording_upload_temp_dir(recording_id, create=False)
    if not temp_dir.exists():
        return

    try:
        shutil.rmtree(temp_dir)
    except OSError as error:
        logger.warning("Failed to delete recording temp directory %s: %s", temp_dir, error)


def _cleanup_empty_chunk_parent_dirs(path: Path, *, logger: logging.Logger) -> None:
    candidate = path.parent
    roots: list[Path] = []
    for root in (recordings_temp_dir(create=False), recordings_failed_dir(create=False)):
        try:
            roots.append(root.resolve())
        except OSError:
            continue

    while True:
        try:
            resolved_candidate = candidate.resolve()
        except OSError:
            return

        matching_root = next(
            (
                root
                for root in roots
                if resolved_candidate == root or root in resolved_candidate.parents
            ),
            None,
        )
        if matching_root is None:
            return

        try:
            next(candidate.iterdir())
            return
        except StopIteration:
            pass
        except OSError as error:
            logger.warning("Failed to inspect recording chunk directory %s: %s", candidate, error)
            return

        if resolved_candidate == matching_root:
            try:
                candidate.rmdir()
            except OSError:
                pass
            return

        try:
            candidate.rmdir()
        except OSError as error:
            logger.warning("Failed to remove empty recording chunk directory %s: %s", candidate, error)
            return
        candidate = candidate.parent


def cleanup_recording_audio_chunks(
    session,
    *,
    logger: logging.Logger,
    now: datetime | None = None,
) -> int:
    cutoff = now or utc_now()
    rows = session.exec(
        select(RecordingAudioChunk)
        .where(RecordingAudioChunk.cleanup_eligible_at.is_not(None))
        .where(RecordingAudioChunk.cleanup_eligible_at <= cutoff)
        .where(RecordingAudioChunk.upload_status.in_(["finalized", "failed"]))
    ).all()

    cleaned_count = 0
    for row in rows:
        resolved_path = _resolve_path_within_recordings_root(row.storage_path)
        if resolved_path is not None and resolved_path.exists():
            try:
                resolved_path.unlink()
                cleaned_count += 1
            except OSError as error:
                logger.warning("Failed to delete recording chunk file %s: %s", resolved_path, error)
                continue
            _cleanup_empty_chunk_parent_dirs(resolved_path, logger=logger)

        row.upload_status = "cleaned"
        row.cleanup_eligible_at = None
        session.add(row)

    if rows:
        session.commit()

    return cleaned_count


def mark_recording_audio_chunks_ready_for_cleanup(
    session,
    *,
    recording_id: int,
    upload_status: str = "finalized",
) -> int:
    rows = session.exec(
        select(RecordingAudioChunk).where(RecordingAudioChunk.recording_id == recording_id)
    ).all()
    if not rows:
        return 0

    deadline = chunk_cleanup_deadline()
    for row in rows:
        row.upload_status = upload_status
        row.cleanup_eligible_at = deadline
        session.add(row)

    return len(rows)


def move_recording_upload_to_failed(
    recording_id: int | str,
    *,
    logger: logging.Logger,
) -> Path | None:
    temp_dir = recording_upload_temp_dir(recording_id, create=False)
    if not temp_dir.exists():
        return None

    failed_path = recordings_failed_dir() / f"{recording_id}_failed_{int(time.time())}"
    shutil.move(str(temp_dir), str(failed_path))
    logger.info("Moved failed recording upload %s to %s", recording_id, failed_path)
    return failed_path


def cleanup_stale_recording_artifacts(
    *,
    max_age_hours: int = 24,
    logger: logging.Logger,
) -> int:
    cutoff_time = time.time() - (max_age_hours * 60 * 60)
    cleaned_count = 0

    for root in (recordings_temp_dir(create=False), recordings_failed_dir(create=False)):
        if not root.exists():
            continue

        for item in root.iterdir():
            try:
                if item.stat().st_mtime >= cutoff_time:
                    continue

                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
                cleaned_count += 1
                logger.info("Cleaned up old recording storage item: %s", item)
            except Exception as error:
                logger.error("Error cleaning stale recording storage item %s: %s", item, error)

    return cleaned_count