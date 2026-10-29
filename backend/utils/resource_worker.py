"""Small, argument-free snapshots from Celery's local request state."""

from datetime import UTC, datetime
from typing import Any

from celery.worker import state

from backend.models.system_public import ResourceTask, WorkerResources
from backend.utils.resource_models import resident_models

WORKER_RESOURCE_PREFIX = "nojoin:resources:v1:worker:"


def collect_worker_resources(worker: Any) -> WorkerResources:
    now = datetime.now(UTC)
    running = []
    for request in list(state.active_requests):
        started = request.time_start
        running.append(
            ResourceTask(
                # Unknown names cannot disclose client-provided text.
                name=request.name
                if request.name in worker.app.tasks
                else "Unknown task",
                started_at=datetime.fromtimestamp(started, UTC) if started else None,
                elapsed_seconds=max(0, now.timestamp() - started) if started else None,
            )
        )
    consumer = getattr(worker, "consumer", None)
    task_consumer = getattr(consumer, "task_consumer", None)
    queues = getattr(task_consumer, "queues", [])
    models, reserved = resident_models()
    return WorkerResources(
        identity=worker.hostname,
        lanes=sorted(queue.name for queue in queues),
        sampled_at=now,
        running=running,
        reserved_count=len(state.reserved_requests - state.active_requests),
        models=models,
        cuda_reserved_bytes=reserved,
    )
