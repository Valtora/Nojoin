import asyncio
import logging
from typing import Any, Literal
from uuid import uuid4

from kombu.exceptions import KombuError

from backend.celery_app import CPU_QUEUE, celery_app
from backend.core.task_dispatch import dispatch_task
from backend.utils import forced_aligner_model_utils as aligner_assets
from backend.utils.config_manager import config_manager
from backend.utils.download_progress import set_download_progress
from backend.utils.forced_aligner_manifest import ForcedAlignerError
from backend.utils.nemotron_lock import control as nemotron_control
from backend.utils.nemotron_model_utils import (
    NemotronModelError,
    inspect_nemotron_model,
)

logger = logging.getLogger(__name__)

MODEL_PREPARATION_TASK = "backend.worker.tasks.download_models_task"
# Pinned targets only download and hash, so they run on the CPU lane, which
# mounts the same model cache, rather than holding live ASR's GPU queue.
_PINNED_TASK_KWARGS: dict[str, dict[str, Any]] = {
    "nemotron": {"nemotron_only": True, "include_core": False},
    "forced_aligner": {"pinned_model": "forced_aligner", "include_core": False},
}


async def enqueue_model_preparation(
    *,
    whisper_model_size: str | None = None,
    transcription_backend: str | None = None,
    parakeet_model: str | None = None,
    canary_model: str | None = None,
    target: Literal["core", "active", "nemotron", "forced_aligner"] = "core",
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
    options: dict[str, Any] = {}
    if target in _PINNED_TASK_KWARGS:
        kwargs = dict(_PINNED_TASK_KWARGS[target])
        options["queue"] = CPU_QUEUE
    task = await dispatch_task(
        MODEL_PREPARATION_TASK, kwargs=kwargs, ignore_result=True, **options
    )
    await asyncio.to_thread(
        set_download_progress,
        0,
        "Model preparation queued...",
        status="downloading",
        stage=target if target in {"nemotron", "forced_aligner"} else "queued",
    )
    logger.info("Queued model preparation task %s with args %s", task.id, kwargs)
    return str(task.id)


def request_nemotron_preparation(*, recording_id: int | None) -> str | None:
    """Best-effort first-use enqueue of the native speaker model.

    The final pass keeps community speakers for this invocation; a later
    eligible recording can use the prepared model.
    """
    return request_pinned_preparation("nemotron", recording_id=recording_id)


def request_pinned_preparation(
    target: Literal["nemotron", "forced_aligner"], *, recording_id: int | None
) -> str | None:
    """Best-effort first-use enqueue; never wait behind a transfer lock.

    Publishing has no retries and uses two-second connect/socket limits on a
    dedicated connection. It does not subscribe to the result backend. The
    pending marker keeps later requests from queueing a second transfer, and
    a recent failure holds them off for FAILURE_BACKOFF.
    """
    if target == "nemotron":
        control, inspect = nemotron_control, inspect_nemotron_model
    else:
        control = aligner_assets.control
        inspect = aligner_assets.inspect_forced_aligner_model
    task_id = str(uuid4())
    try:
        with control.model_lock(blocking=False) as acquired:
            if not acquired:
                logger.info("%s preparation busy (recording=%s)", target, recording_id)
                return None
            if (
                inspect().state == "verified"
                or control.pending_task()
                or control.failed_recently()
            ):
                return None
            control.write_pending(task_id)
            try:
                _publish_pinned_preparation(target, task_id)
            except (OSError, KombuError, ValueError):
                control.clear_pending(task_id)
                raise
        return task_id
    except (OSError, KombuError, ValueError, NemotronModelError, ForcedAlignerError):
        logger.warning(
            "%s preparation dispatch failed (recording=%s)", target, recording_id
        )
        return None


def _publish_pinned_preparation(target: str, task_id: str) -> None:
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
            kwargs=dict(_PINNED_TASK_KWARGS[target]),
            task_id=task_id,
            queue=CPU_QUEUE,
            connection=connection,
            retry=False,
            ignore_result=True,
        )
