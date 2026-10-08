import asyncio
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import case, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from backend.core.task_dispatch import dispatch_task
from backend.models.user import User
from backend.utils.config_manager import DEFAULT_SYSTEM_CONFIG, config_manager
from backend.utils.download_progress import set_download_progress

logger = logging.getLogger(__name__)

MODEL_PREPARATION_TASK = "backend.worker.tasks.download_models_task"

# The user-scoped settings that decide which transcription models to prepare.
TRANSCRIPTION_KEYS = (
    "transcription_backend",
    "whisper_model_size",
    "parakeet_model",
    "canary_model",
)


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


def effective_transcription_setting(
    settings: Mapping[str, Any] | None, key: str
) -> str:
    """A user's value for a transcription key, else the install config's.

    The transcription keys are user-scoped: Settings > Transcription stores the
    choosing administrator's value on that administrator's own row, and a user
    whose row holds none falls back to config.json. The processing pipeline
    merges a user's settings over the config the same way
    (``utils/llm_config._merge_llm_config``): any stored value other than None
    wins, an empty string included.
    """
    value = settings.get(key) if settings else None
    if value is not None:
        return str(value)
    return str(config_manager.get(key, DEFAULT_SYSTEM_CONFIG[key]))


def resolve_startup_model_selection(
    user_settings: Sequence[Mapping[str, Any] | None],
) -> dict[str, Any]:
    """The preparation-task arguments for these users' effective engines.

    ``user_settings`` lists the active users' settings, owner first, then by id.
    The first user's effective engine and models are prepared as the install's
    primary choice: the owner's, or the lowest-id active user's while the owner
    is deactivated. Whisper is prepared only while at least one user's effective
    engine is Whisper, at the size of the first such user. With no users, the
    install config decides. Pyannote is always prepared (the core batch).
    """
    rows: list[Mapping[str, Any] | None] = list(user_settings) or [None]
    primary = rows[0]
    whisper_rows = [
        row
        for row in rows
        if effective_transcription_setting(row, "transcription_backend") == "whisper"
    ]
    selection: dict[str, Any] = {
        "whisper_model_size": effective_transcription_setting(
            whisper_rows[0] if whisper_rows else primary, "whisper_model_size"
        ),
        "transcription_backend": effective_transcription_setting(
            primary, "transcription_backend"
        ),
        "parakeet_model": effective_transcription_setting(primary, "parakeet_model"),
        "canary_model": effective_transcription_setting(primary, "canary_model"),
        "include_core": True,
    }
    # Without the flag, download_models prepares Whisper exactly when the backend
    # (an empty one falling back to config) is Whisper. Send the flag only when
    # the users need otherwise, so a worker on an image that predates it still
    # accepts the common case.
    inferred = (
        selection["transcription_backend"]
        or effective_transcription_setting(None, "transcription_backend")
    ) == "whisper"
    if bool(whisper_rows) != inferred:
        selection["include_whisper"] = bool(whisper_rows)
    return selection


async def _read_active_user_settings(
    session: AsyncSession,
) -> list[Mapping[str, Any] | None]:
    """Every active user's transcription keys, owner first, then by id.

    Only those keys are selected, so the database does not ship and the API
    does not decode every user's whole settings blob on each health poll. A row
    whose settings are not a JSON object yields no keys, and a value that is
    not a string is dropped: that user falls back to config.json for the key,
    and nobody else's selection changes.
    """
    result = await session.execute(
        select(*(User.settings[key] for key in TRANSCRIPTION_KEYS))
        .where(col(User.is_active).is_(True))
        .order_by(case((User.role == "owner", 0), else_=1), User.id)
    )
    return [
        {
            key: value
            for key, value in zip(TRANSCRIPTION_KEYS, row, strict=True)
            if isinstance(value, str)
        }
        for row in result.all()
    ]


async def resolve_install_transcription_selection(
    session: AsyncSession,
) -> dict[str, Any]:
    """What this install's users transcribe with, as startup prepares it.

    Startup preparation and the admin health check both read it, so the health
    check reports the engine that was actually prepared. When the users cannot
    be read (a database error), the install config decides, as it did before
    startup read the users.
    """
    try:
        user_settings = await _read_active_user_settings(session)
    except (SQLAlchemyError, OSError) as exc:
        await session.rollback()
        logger.warning(
            "Could not read the users' transcription settings, so config.json "
            "decides the transcription models: %s",
            exc,
        )
        user_settings = []
    return resolve_startup_model_selection(user_settings)


async def enqueue_startup_model_preparation(
    session_maker: async_sessionmaker[AsyncSession],
) -> str:
    """Queue the startup preparation for the engines this install's users run."""
    async with session_maker() as session:
        selection = await resolve_install_transcription_selection(session)
    return await _queue_preparation(selection)
