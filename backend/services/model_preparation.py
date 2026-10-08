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

# The engines download_models prepares outside the core batch.
ONNX_ENGINES = ("parakeet", "canary")


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
    """The engines and models these users transcribe with.

    ``user_settings`` lists the active users' settings, owner first, then by id.
    The first user's effective engine and models are the install's primary
    choice: the owner's, or the lowest-id active user's while the owner is
    deactivated. ``whisper_needed`` says whether any user's effective engine is
    Whisper, and the Whisper size is the first such user's. With no users, the
    install config decides.
    """
    rows: list[Mapping[str, Any] | None] = list(user_settings) or [None]
    primary = rows[0]
    whisper_rows = [
        row
        for row in rows
        if effective_transcription_setting(row, "transcription_backend") == "whisper"
    ]
    return {
        "whisper_model_size": effective_transcription_setting(
            whisper_rows[0] if whisper_rows else primary, "whisper_model_size"
        ),
        "transcription_backend": effective_transcription_setting(
            primary, "transcription_backend"
        ),
        "parakeet_model": effective_transcription_setting(primary, "parakeet_model"),
        "canary_model": effective_transcription_setting(primary, "canary_model"),
        "whisper_needed": bool(whisper_rows),
    }


def startup_preparation_tasks(selection: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The preparation tasks that put this selection's models on disk, in order.

    One task normally does it all: the core batch (Pyannote, and Whisper when
    the backend is Whisper) plus the primary engine. download_models prepares
    Whisper only for the Whisper backend, so when the primary engine is
    Parakeet or Canary while other users run Whisper, Whisper goes in its own
    task with the core batch and the primary engine follows alone. Every task
    uses only arguments that every worker image accepts, so a worker still on
    an older image prepares them too.
    """
    models = {
        "whisper_model_size": selection["whisper_model_size"],
        "parakeet_model": selection["parakeet_model"],
        "canary_model": selection["canary_model"],
    }
    backend = selection["transcription_backend"]
    if not selection["whisper_needed"] or backend == "whisper":
        return [{**models, "transcription_backend": backend, "include_core": True}]
    tasks = [{**models, "transcription_backend": "whisper", "include_core": True}]
    if backend in ONNX_ENGINES:
        tasks.append(
            {**models, "transcription_backend": backend, "include_core": False}
        )
    return tasks


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

    The read runs in a savepoint, so a failure rolls back only the read: the
    health check passes the request's session, whose work must survive it.
    """
    try:
        async with session.begin_nested():
            user_settings = await _read_active_user_settings(session)
    except (SQLAlchemyError, OSError) as exc:
        # The first line names the error; SQLAlchemy appends the statement and
        # its parameters below it, which a health poll should not repeat.
        logger.warning(
            "Could not read the users' transcription settings (%s: %s), so "
            "config.json decides the transcription models",
            type(exc).__name__,
            str(exc).partition("\n")[0],
        )
        user_settings = []
    return resolve_startup_model_selection(user_settings)


async def enqueue_startup_model_preparation(
    session_maker: async_sessionmaker[AsyncSession],
) -> list[str]:
    """Queue the startup preparation for the engines this install's users run.

    The tasks share the GPU lane, which runs one task at a time by default, so
    they run in the order queued. A lane with more concurrency may overlap
    them, which is safe: no two tasks prepare the same model.
    """
    async with session_maker() as session:
        selection = await resolve_install_transcription_selection(session)
    return [
        await _queue_preparation(kwargs)
        for kwargs in startup_preparation_tasks(selection)
    ]
