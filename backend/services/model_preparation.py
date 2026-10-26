import asyncio
import logging
from typing import Any, Literal
from uuid import uuid4

from kombu.exceptions import KombuError

from backend.celery_app import celery_app
from backend.core.task_dispatch import dispatch_task
from backend.utils.config_manager import config_manager
from backend.utils.download_progress import set_download_progress
from backend.utils.nemotron_lock import (
    clear_pending,
    model_lock,
    pending_task,
    write_pending,
)
from backend.utils.nemotron_model_utils import (
    NemotronModelError,
    inspect_nemotron_model,
)

logger = logging.getLogger(__name__)

MODEL_PREPARATION_TASK = "backend.worker.tasks.download_models_task"


async def enqueue_model_preparation(
    *,
    whisper_model_size: str | None = None,
    transcription_backend: str | None = None,
    parakeet_model: str | None = None,
    canary_model: str | None = None,
    target: Literal["core", "active", "nemotron"] = "core",
) -> str:
    """Queue worker-side model preparation without importing inference code.

    Every caller is a request handler or the API's startup lifespan, and both
    of the Redis calls below block, so both are kept off the event loop.
    `ignore_result` is set because nobody awaits this task's return
    value; progress is reported through the download-progress key instead.
    `core` includes shared assets; `active` prepares only the selected ASR.
    `nemotron` exclusively prepares the native speaker model.
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
        "include_core": target == "core",
    }
    if target == "nemotron":
        kwargs = {"nemotron_only": True, "include_core": False}
    task = await dispatch_task(
        MODEL_PREPARATION_TASK, kwargs=kwargs, ignore_result=True
    )
    await asyncio.to_thread(
        set_download_progress,
        0,
        "Model preparation queued...",
        status="downloading",
        stage="nemotron" if target == "nemotron" else "queued",
    )
    logger.info("Queued model preparation task %s with args %s", task.id, kwargs)
    return str(task.id)


def request_nemotron_preparation(*, recording_id: int | None) -> str | None:
    """Best-effort first-use enqueue; never wait behind a transfer lock.

    Publishing has no retries and uses two-second connect/socket limits on a
    dedicated connection. It does not subscribe to the result backend.
    Live and final callers keep community speakers for this invocation; a later
    eligible recording can use the prepared model.
    """
    task_id = str(uuid4())
    try:
        with model_lock(blocking=False) as acquired:
            if not acquired:
                logger.info(
                    "Native speaker preparation busy (recording=%s)", recording_id
                )
                return None
            if inspect_nemotron_model().state == "verified" or pending_task():
                return None
            write_pending(task_id)
            try:
                _publish_nemotron_preparation(task_id)
            except (OSError, KombuError, ValueError):
                clear_pending(task_id)
                raise
        return task_id
    except (OSError, KombuError, ValueError, NemotronModelError):
        logger.warning(
            "Native speaker preparation dispatch failed (recording=%s)", recording_id
        )
        return None


def _publish_nemotron_preparation(task_id: str) -> None:
    options = {
        **celery_app.conf.broker_transport_options,
        "socket_connect_timeout": 2,
        "socket_timeout": 2,
        "retry_on_timeout": False,
        "max_retries": 0,
    }
    with celery_app.connection_for_write(
        connect_timeout=2, transport_options=options
    ) as connection:
        celery_app.send_task(
            MODEL_PREPARATION_TASK,
            kwargs={"nemotron_only": True, "include_core": False},
            task_id=task_id,
            connection=connection,
            retry=False,
            ignore_result=True,
        )
