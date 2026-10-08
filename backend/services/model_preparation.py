import asyncio
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from backend.core.task_dispatch import dispatch_task
from backend.models.user import User
from backend.utils.config_manager import config_manager
from backend.utils.download_progress import set_download_progress

logger = logging.getLogger(__name__)

MODEL_PREPARATION_TASK = "backend.worker.tasks.download_models_task"

# The per-user transcription keys and the defaults config_manager falls back to.
_TRANSCRIPTION_DEFAULTS = {
    "transcription_backend": "whisper",
    "whisper_model_size": "turbo",
    "parakeet_model": "parakeet-tdt-0.6b-v3",
    "canary_model": "nemo-canary-1b-v2",
}


async def enqueue_model_preparation(
    *,
    whisper_model_size: str | None = None,
    transcription_backend: str | None = None,
    parakeet_model: str | None = None,
    canary_model: str | None = None,
    include_core: bool = True,
) -> str:
    """Queue worker-side model preparation without importing inference code.

    Every caller is a request handler or the API's startup lifespan, and both
    of the Redis calls below block, so both are kept off the event loop.
    `ignore_result` is set because nobody awaits this task's return
    value; progress is reported through the download-progress key instead.
    """
    kwargs: dict[str, Any] = {
        "whisper_model_size": whisper_model_size
        or str(config_manager.get("whisper_model_size", "turbo")),
        "transcription_backend": transcription_backend
        or str(config_manager.get("transcription_backend", "whisper")),
        "parakeet_model": parakeet_model
        or str(config_manager.get("parakeet_model", "parakeet-tdt-0.6b-v3")),
        "canary_model": canary_model
        or str(config_manager.get("canary_model", "nemo-canary-1b-v2")),
        "include_core": include_core,
    }
    return await _queue_preparation(kwargs)


async def _queue_preparation(kwargs: dict[str, Any]) -> str:
    task = await dispatch_task(
        MODEL_PREPARATION_TASK, kwargs=kwargs, ignore_result=True
    )
    await asyncio.to_thread(
        set_download_progress,
        0,
        "Model preparation queued...",
        status="downloading",
        stage="queued",
    )
    logger.info("Queued model preparation task %s with args %s", task.id, kwargs)
    return str(task.id)


def _effective(settings: Mapping[str, Any] | None, key: str) -> str:
    """A user's value for a transcription key, else the install config's.

    The same precedence the processing pipeline applies: the transcription keys
    are user-scoped, and an unset or empty user value falls through.
    """
    value = (settings or {}).get(key)
    if value:
        return str(value)
    return str(config_manager.get(key, _TRANSCRIPTION_DEFAULTS[key]))


def resolve_startup_model_selection(
    user_settings: Sequence[Mapping[str, Any] | None],
) -> dict[str, Any]:
    """Decide what API startup prepares, from every active user's settings.

    ``user_settings`` lists the active users' settings, owner first. The owner's
    effective engine and models are prepared, as the install's primary choice.
    Whisper is prepared only when at least one user transcribes with it, at the
    size the first such user chose. With no users yet, the install config
    decides. Pyannote is always prepared (the core batch).
    """
    rows: list[Mapping[str, Any] | None] = list(user_settings) or [None]
    primary = rows[0]
    whisper_rows = [
        row for row in rows if _effective(row, "transcription_backend") == "whisper"
    ]
    return {
        "transcription_backend": _effective(primary, "transcription_backend"),
        "whisper_model_size": _effective(
            whisper_rows[0] if whisper_rows else primary, "whisper_model_size"
        ),
        "parakeet_model": _effective(primary, "parakeet_model"),
        "canary_model": _effective(primary, "canary_model"),
        "include_core": True,
        "include_whisper": bool(whisper_rows),
    }


async def enqueue_startup_model_preparation(
    session_maker: async_sessionmaker[AsyncSession],
) -> str:
    """Queue the startup preparation for the engines this install's users chose."""
    async with session_maker() as session:
        result = await session.execute(
            select(User.settings)
            .where(col(User.is_active).is_(True))
            .order_by(case((User.role == "owner", 0), else_=1), User.id)
        )
        user_settings = [row[0] for row in result.all()]

    # Sent straight to the task: unlike the Settings paths, startup decides
    # Whisper separately from the owner's engine (include_whisper).
    return await _queue_preparation(resolve_startup_model_selection(user_settings))
