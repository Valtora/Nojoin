"""Resource sampling, called in a thread through the existing Docker boundary."""

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import cast

import psutil
import redis
from docker.client import DockerClient
from docker.errors import DockerException
from kombu.transport.redis import Channel
from pydantic import ValidationError
from requests.exceptions import RequestException

from backend.celery_app import celery_app
from backend.core.redis import get_redis_url
from backend.models.system_public import (
    GpuResources,
    HostResources,
    NojoinResources,
    ResourceCapabilities,
    ResourceMemory,
    SystemResources,
    WorkerResources,
)
from backend.utils.resource_worker import WORKER_RESOURCE_PREFIX

CACHE_KEY = "nojoin:resources:v1:summary"


def resource_redis() -> redis.Redis:
    return redis.from_url(get_redis_url(), socket_connect_timeout=1, socket_timeout=1)


def gpu_resources() -> list[GpuResources]:
    try:
        import pynvml
    except ImportError:
        return []
    try:
        pynvml.nvmlInit()
    except pynvml.NVMLError:
        return []
    try:
        devices = {}
        for index in range(pynvml.nvmlDeviceGetCount()):
            handle = pynvml.nvmlDeviceGetHandleByIndex(index)
            uuid = pynvml.nvmlDeviceGetUUID(handle)
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
            try:
                utilisation = pynvml.nvmlDeviceGetUtilizationRates(handle).gpu
            except pynvml.NVMLError:
                utilisation = None
            devices[uuid] = GpuResources(
                uuid=uuid,
                name=pynvml.nvmlDeviceGetName(handle),
                utilisation_percent=utilisation,
                memory=ResourceMemory(used_bytes=memory.used, total_bytes=memory.total),
            )
        return list(devices.values())
    except pynvml.NVMLError:
        return []
    finally:
        pynvml.nvmlShutdown()


def docker_resources(get_client: Callable[[], DockerClient]) -> NojoinResources | None:
    try:
        client = get_client()
        # The API's own Compose label scopes accounting to this installation.
        own = client.containers.get(os.getenv("HOSTNAME", "nojoin-api"))
        project = own.labels.get("com.docker.compose.project")
        if not project:
            return None
        containers = client.containers.list(
            filters={
                "label": f"com.docker.compose.project={project}",
            }
        )
        with ThreadPoolExecutor(max_workers=8) as executor:
            stats = list(executor.map(lambda c: c.stats(stream=False), containers))
        return _sum_container_stats(stats)
    except (DockerException, RequestException, OSError):
        return None


def _sum_container_stats(stats: list[dict]) -> NojoinResources:
    cpu = 0.0
    memory = 0
    valid_cpu = bool(stats)
    for sample in stats:
        current = sample.get("cpu_stats", {})
        previous = sample.get("precpu_stats", {})
        system_delta = current.get("system_cpu_usage", 0) - previous.get(
            "system_cpu_usage", 0
        )
        usage_delta = current.get("cpu_usage", {}).get("total_usage", 0) - previous.get(
            "cpu_usage", {}
        ).get("total_usage", 0)
        if not previous or system_delta <= 0 or usage_delta < 0:
            valid_cpu = False
        else:
            # system_cpu_usage already sums all host cores: 100% is the host.
            cpu += usage_delta / system_delta * 100
        memory += sample.get("memory_stats", {}).get("usage", 0)
    return NojoinResources(cpu_percent=cpu if valid_cpu else None, memory_bytes=memory)


def _worker_resources(
    client: redis.Redis,
) -> tuple[list[WorkerResources], dict[str, int]]:
    workers = []
    for key in client.scan_iter(match=WORKER_RESOURCE_PREFIX + "*"):
        raw = cast(str | bytes | None, client.get(key))
        if raw is not None:
            try:
                workers.append(WorkerResources.model_validate_json(raw))
            except ValidationError:
                continue
    options = celery_app.conf.broker_transport_options
    priorities = options.get("priority_steps", Channel.priority_steps)
    prefix = options.get("global_keyprefix", "")
    lanes = {"gpu", "cpu", "io", "parse"}
    lanes.update(lane for worker in workers for lane in worker.lanes)
    queues = {
        lane: sum(
            cast(int, client.llen(prefix + lane + (f"{Channel.sep}{p}" if p else "")))
            for p in priorities
        )
        for lane in sorted(lanes)
    }
    return sorted(workers, key=lambda worker: worker.identity), queues


def _sample(
    get_client: Callable[[], DockerClient], client: redis.Redis | None
) -> SystemResources:
    memory = psutil.virtual_memory()
    gpus = gpu_resources()
    nojoin = docker_resources(get_client)
    workers: list[WorkerResources] = []
    queues: dict[str, int] | None = None
    if client is not None:
        workers, queues = _worker_resources(client)
    return SystemResources(
        sampled_at=datetime.now(UTC),
        host=HostResources(
            cpu_percent=psutil.cpu_percent(interval=0.1),
            memory=ResourceMemory(
                used_bytes=memory.total - memory.available, total_bytes=memory.total
            ),
        ),
        nojoin=nojoin,
        gpus=gpus,
        workers=workers,
        queues=queues,
        capabilities=ResourceCapabilities(
            gpu=bool(gpus), docker=nojoin is not None, workers=client is not None
        ),
    )


def system_resources(get_client: Callable[[], DockerClient]) -> SystemResources:
    try:
        client = resource_redis()
        # The lock also coalesces simultaneous first polls across API processes.
        with client.lock(CACHE_KEY + ":lock", timeout=30, blocking_timeout=10):
            cached = cast(str | bytes | None, client.get(CACHE_KEY))
            if cached:
                return SystemResources.model_validate_json(cached)
            report = _sample(get_client, client)
            client.set(CACHE_KEY, report.model_dump_json(), ex=2)
            return report
    except redis.exceptions.LockError:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503, detail="Resource sampling is busy"
        ) from None
    except (redis.RedisError, ValidationError):
        return _sample(get_client, None)
