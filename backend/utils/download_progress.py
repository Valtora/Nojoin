"""
Redis-based download progress tracking for model downloads.
This allows both preload_models.py and the Celery download task to share progress state,
enabling the frontend to see accurate progress regardless of which mechanism is downloading.
"""

import json
import logging
import time
from typing import Optional

import redis

from backend.core.redis import REDIS_URL

logger = logging.getLogger(__name__)

PROGRESS_KEY = "nojoin:model_download_progress"
PINNED_MODELS = ("nemotron", "forced_aligner")
CORE_STAGES = (
    "core",
    "queued",
    "init",
    "whisper",
    "whisper_loading",
    "parakeet",
    "canary",
    "pyannote",
    "embedding",
    "segmentation",
    "vad",
    "complete",
)
PROGRESS_MODELS = (*CORE_STAGES, *PINNED_MODELS)
PROGRESS_TTL = 3600  # 1 hour TTL to auto-cleanup stale progress

_redis_client: Optional[redis.Redis] = None


def _clear_other_core_stages(client: redis.Redis, model: str) -> None:
    """Core stages share one serial preparation; pinned models are independent."""
    if model not in PINNED_MODELS:
        for sibling in CORE_STAGES:
            if sibling != model:
                client.delete(f"{PROGRESS_KEY}:{sibling}")


def _get_redis() -> Optional[redis.Redis]:
    """Get or create Redis connection."""
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
            _redis_client.ping()
        except Exception as e:  # noqa: BLE001 -- boundary: Redis is optional for progress reporting; without it nothing is reported
            logger.warning(f"Could not connect to Redis: {e}")
            return None
    return _redis_client


def set_download_progress(
    progress: int,
    message: str,
    speed: Optional[str] = None,
    eta: Optional[str] = None,
    status: str = "downloading",
    stage: Optional[str] = None,
) -> bool:
    """
    Write download progress to Redis.

    Args:
        progress: Percentage complete (0-100)
        message: Current status message
        speed: Download speed string (e.g., "5.2 MB/s")
        eta: Estimated time remaining (e.g., "45s")
        status: Overall status - "downloading", "complete", "error"
        stage: Current download stage - "whisper", "pyannote", "embedding", "vad"

    Returns:
        True if successfully written, False otherwise
    """
    r = _get_redis()
    if r is None:
        return False

    try:
        model = stage if stage in PROGRESS_MODELS else "core"
        key = f"{PROGRESS_KEY}:{model}"
        # Core preparation is serial on the GPU lane. Only its current stage
        # remains active; independent pinned preparations keep their own keys.
        # Prevent regressions in visible progress shown to the frontend.
        # If an existing progress is recorded and the new progress is lower
        # (common when switching from "download" -> "loading" phases),
        # keep the higher value so the progress bar doesn't jump backwards.
        existing = None
        existing_stage = None
        try:
            existing_raw = r.get(key)
            if existing_raw:
                data = json.loads(existing_raw)
                existing = data.get("progress")
                existing_stage = data.get("stage")
        except Exception:  # noqa: BLE001 -- boundary: a stale or unreadable record is treated as no previous progress
            existing = None
            existing_stage = None

        # Avoids decreasing the numeric progress within the same stage.
        # If the stage changes (e.g. vad -> whisper), the progress resets to 0.
        write_progress = progress
        if status == "downloading" and existing is not None and stage == existing_stage:
            try:
                existing_int = int(existing)
                if write_progress < existing_int:
                    write_progress = existing_int
            except (TypeError, ValueError):
                # If parsing fails, fall back to provided value
                pass

        data = {
            "progress": write_progress,
            "message": message,
            "speed": speed,
            "eta": eta,
            "status": status,
            "stage": stage,
            "updated_at": time.time(),
            "in_progress": status == "downloading",
        }
        with r.pipeline() as transaction:
            _clear_other_core_stages(transaction, model)
            transaction.set(key, json.dumps(data), ex=PROGRESS_TTL)
            transaction.execute()
        return True
    except Exception as e:  # noqa: BLE001 -- boundary: progress reporting must never fail the download
        logger.error(f"Failed to set download progress: {e}")
        return False


def get_download_progress(model: str | None = None) -> Optional[dict]:
    """
    Get current download progress from Redis.

    Returns:
        Dict with progress info, or None if no active download
    """
    r = _get_redis()
    if r is None:
        return None

    try:
        models = (
            CORE_STAGES if model == "core" else (model,) if model else PROGRESS_MODELS
        )
        records = [
            json.loads(data)
            for data in r.mget([f"{PROGRESS_KEY}:{name}" for name in models])
            if data
        ]
        # A silent transfer is still downloading. A sibling completing must
        # not finish setup while that transfer is waiting on weights or a lane.
        return max(
            records,
            key=lambda record: (
                record.get("status") == "downloading",
                record.get("updated_at", 0),
            ),
            default=None,
        )
    except Exception as e:  # noqa: BLE001 -- boundary: progress reporting must never fail the download
        logger.error(f"Failed to get download progress: {e}")
        return None


def clear_download_progress(model: str | None = None) -> bool:
    """
    Clear one model's progress, or the serial core preparation's stages.

    Returns:
        True if successfully cleared, False otherwise
    """
    r = _get_redis()
    if r is None:
        return False

    try:
        for name in (model,) if model is not None else CORE_STAGES:
            r.delete(f"{PROGRESS_KEY}:{name}")
        return True
    except Exception as e:  # noqa: BLE001 -- boundary: progress reporting must never fail the download
        logger.error(f"Failed to clear download progress: {e}")
        return False


def is_download_in_progress(model: str | None = None) -> bool:
    """
    Check if a download is currently in progress.

    Returns:
        True if download is active (status == "downloading"), False otherwise
    """
    progress = get_download_progress(model)
    if progress is None:
        return False

    # Check if the progress is stale (more than 30 seconds old)
    updated_at = progress.get("updated_at", 0)
    if time.time() - updated_at > 30:
        # Stale progress, consider it inactive
        return False

    return progress.get("status") == "downloading"


def is_download_complete() -> bool:
    """
    Check if download has been marked as complete.

    Returns:
        True if status is "complete", False otherwise
    """
    progress = get_download_progress()
    if progress is None:
        return False
    return progress.get("status") == "complete"
