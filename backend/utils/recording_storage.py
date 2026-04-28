from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path


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