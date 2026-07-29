import asyncio
import logging
from typing import Any

from backend.core.task_dispatch import dispatch_task
from backend.utils.config_manager import config_manager
from backend.utils.download_progress import set_download_progress

logger = logging.getLogger(__name__)

MODEL_PREPARATION_TASK = "backend.worker.tasks.download_models_task"


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
