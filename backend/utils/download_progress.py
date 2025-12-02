"""
Redis-based download progress tracking for model downloads.
This allows both preload_models.py and the Celery download task to share progress state,
enabling the frontend to see accurate progress regardless of which mechanism is downloading.
"""
import os
import json
import time
import logging
from typing import Optional
import redis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
PROGRESS_KEY = "nojoin:model_download_progress"
PROGRESS_TTL = 3600  # 1 hour TTL to auto-cleanup stale progress

_redis_client: Optional[redis.Redis] = None


def _get_redis() -> Optional[redis.Redis]:
    """Get or create Redis connection."""
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
            _redis_client.ping()
        except Exception as e:
            logger.warning(f"Could not connect to Redis: {e}")
            return None
    return _redis_client


def set_download_progress(
    progress: int,
    message: str,
    speed: Optional[str] = None,
    eta: Optional[str] = None,
    status: str = "downloading",
    stage: Optional[str] = None
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
        data = {
            "progress": progress,
            "message": message,
            "speed": speed,
            "eta": eta,
            "status": status,
            "stage": stage,
            "updated_at": time.time(),
            "in_progress": status == "downloading"
        }
        r.set(PROGRESS_KEY, json.dumps(data), ex=PROGRESS_TTL)
        return True
    except Exception as e:
        logger.error(f"Failed to set download progress: {e}")
        return False


def get_download_progress() -> Optional[dict]:
    """
    Get current download progress from Redis.
    
    Returns:
        Dict with progress info, or None if no active download
    """
    r = _get_redis()
    if r is None:
        return None
    
    try:
        data = r.get(PROGRESS_KEY)
        if data:
            return json.loads(data)
        return None
    except Exception as e:
        logger.error(f"Failed to get download progress: {e}")
        return None


def clear_download_progress() -> bool:
    """
    Clear download progress from Redis.
    
    Returns:
        True if successfully cleared, False otherwise
    """
    r = _get_redis()
    if r is None:
        return False
    
    try:
        r.delete(PROGRESS_KEY)
        return True
    except Exception as e:
        logger.error(f"Failed to clear download progress: {e}")
        return False


def is_download_in_progress() -> bool:
    """
    Check if a download is currently in progress.
    
    Returns:
        True if download is active (status == "downloading"), False otherwise
    """
    progress = get_download_progress()
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

