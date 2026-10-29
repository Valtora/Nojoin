"""Admin resources through the real route, authentication and PostgreSQL."""

import sys
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.api.deps import get_db
from backend.api.services import resources
from backend.api.v1.endpoints import system
from backend.core.db import driver_urls
from backend.core.security import WEB_SESSION_SCOPE, create_access_token
from backend.models.user import User
from backend.tests.postgres_setup import fresh_schema
from backend.tests.resource_fakes import DockerContainers, MemoryRedis, nvml
from backend.tests.test_worker_resources import publish_worker

pytestmark = pytest.mark.anyio


@pytest.fixture
async def client(postgres_test_url: str) -> AsyncIterator[AsyncClient]:
    async_url, sync_url = driver_urls(postgres_test_url)
    schema = "resource_monitor_test"
    sync = create_engine(
        sync_url, connect_args={"options": f"-csearch_path={schema},public"}
    )
    with sync.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    fresh_schema(sync, schema)
    sync.dispose()
    engine = create_async_engine(
        async_url, connect_args={"server_settings": {"search_path": f"{schema},public"}}
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add_all(
            [
                User(username=role, hashed_password="x", role=role)
                for role in ("admin", "user")
            ]
        )
        await session.commit()

    async def database() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            yield session

    app = FastAPI()
    app.include_router(system.router, prefix="/api/v1/system")
    app.dependency_overrides[get_db] = database
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as value:
            yield value
    finally:
        await engine.dispose()


def auth(role: str) -> dict[str, str]:
    token = create_access_token(
        role, token_type="session", token_version=0, scopes=[WEB_SESSION_SCOPE]
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def edges(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    broker = MemoryRedis()
    docker = DockerContainers()
    monkeypatch.setattr(resources.redis, "from_url", lambda *a, **kw: broker)
    monkeypatch.setattr(
        system.docker, "from_env", lambda: SimpleNamespace(containers=docker)
    )
    system.get_docker_client.cache_clear()
    monkeypatch.setattr(resources.psutil, "cpu_percent", lambda **kw: 60)
    monkeypatch.setattr(
        resources.psutil,
        "virtual_memory",
        lambda: SimpleNamespace(total=8 * 1024**3, available=3 * 1024**3),
    )
    monkeypatch.setitem(sys.modules, "pynvml", nvml())
    publish_worker(broker)
    try:
        yield SimpleNamespace(broker=broker, docker=docker)
    finally:
        system.get_docker_client.cache_clear()


async def test_admin_shape_accounting_and_shared_cache(
    client: AsyncClient, edges: SimpleNamespace
) -> None:
    first = await client.get("/api/v1/system/resources", headers=auth("admin"))
    assert first.status_code == 200
    body = first.json()
    assert set(body) == {
        "sampled_at",
        "host",
        "nojoin",
        "gpus",
        "workers",
        "queues",
        "capabilities",
    }
    assert body["host"] == {
        "cpu_percent": 60,
        "memory": {"used_bytes": 5 * 1024**3, "total_bytes": 8 * 1024**3},
    }
    assert body["nojoin"] == {"cpu_percent": 40, "memory_bytes": 2 * 1024**3}
    assert body["gpus"] == [
        {
            "uuid": "GPU-example",
            "name": "Example GPU",
            "utilisation_percent": 75,
            "memory": {"used_bytes": 2 * 1024**3, "total_bytes": 8 * 1024**3},
        }
    ]
    assert body["queues"] == {"gpu": 6, "cpu": 0, "io": 0, "parse": 0}
    assert body["capabilities"] == {"gpu": True, "docker": True, "workers": True}
    worker = body["workers"][0]
    assert worker["identity"] == "gpu@synthetic" and worker["lanes"] == ["gpu"]
    assert worker["sampled_at"] and worker["reserved_count"] == 1
    assert worker["running"][0]["name"] == "celery.backend_cleanup"
    assert (
        worker["running"][0]["started_at"]
        and worker["running"][0]["elapsed_seconds"] >= 10
    )
    assert worker["models"] == [] and worker["cuda_reserved_bytes"] == {}
    second = await client.get("/api/v1/system/resources", headers=auth("admin"))
    assert second.json()["sampled_at"] == body["sampled_at"]
    assert [c.reads for c in edges.docker.containers] == [1, 1, 0]
    edges.broker.now += 3
    await client.get("/api/v1/system/resources", headers=auth("admin"))
    assert [c.reads for c in edges.docker.containers] == [2, 2, 0]


async def test_non_admin_refused(client: AsyncClient, edges: SimpleNamespace) -> None:
    response = await client.get("/api/v1/system/resources", headers=auth("user"))
    assert response.status_code == 403
    assert all(c.reads == 0 for c in edges.docker.containers)


async def test_nvml_absent_keeps_cpu_monitor(
    client: AsyncClient, edges: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "pynvml", None)
    response = await client.get("/api/v1/system/resources", headers=auth("admin"))
    assert response.status_code == 200
    assert response.json()["capabilities"]["gpu"] is False
    assert response.json()["gpus"] == []
    assert response.json()["host"]["cpu_percent"] == 60


async def test_docker_unreachable_keeps_other_measurements(
    client: AsyncClient, edges: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable() -> None:
        raise system.DockerException("unreachable")

    monkeypatch.setattr(system.docker, "from_env", unavailable)
    response = await client.get("/api/v1/system/resources", headers=auth("admin"))
    assert response.status_code == 200
    body = response.json()
    assert body["nojoin"] is None and body["capabilities"]["docker"] is False
    assert body["host"]["cpu_percent"] == 60 and body["gpus"] and body["workers"]


async def test_resident_estimates_cross_real_endpoint(
    client: AsyncClient, edges: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    import torch

    from backend.processing import (
        embedding_core,
        nemotron_model,
        segmentation_model,
        transcribe,
    )
    from backend.processing.engines import whisper_engine
    from backend.processing.engines.canary_engine import CanaryEngine
    from backend.processing.engines.parakeet_engine import ParakeetEngine

    # Only the hardware edge is simulated. Real torch modules supply parameters,
    # buffers and storage identities; real in-repo caches supply residency.
    whisper = torch.nn.Linear(2, 3)
    labels = torch.nn.Linear(3, 2)
    monkeypatch.setattr(
        torch.Tensor, "device", property(lambda self: torch.device("cuda:0"))
    )
    monkeypatch.setattr(torch.cuda, "is_initialized", lambda: True)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 1)
    monkeypatch.setattr(torch.cuda, "memory_reserved", lambda index: 4096)
    monkeypatch.setitem(whisper_engine._model_cache, "base", whisper)
    monkeypatch.setitem(
        nemotron_model._RUNTIMES, "cuda", SimpleNamespace(model=whisper)
    )
    monkeypatch.setitem(
        segmentation_model._model_cache, ("segmentation", "cuda"), labels
    )
    monkeypatch.setitem(
        embedding_core._embedding_model_cache,
        ("embedding", "cuda"),
        SimpleNamespace(model=labels),
    )
    for name, engine in (("parakeet", ParakeetEngine()), ("canary", CanaryEngine())):
        engine._model_cache[name] = object()  # ORT session edge: opaque to torch.
        engine._model_on_gpu[name] = True
        monkeypatch.setitem(transcribe._ENGINE_REGISTRY, name, engine)
    publish_worker(edges.broker)

    response = await client.get("/api/v1/system/resources", headers=auth("admin"))
    assert response.status_code == 200
    worker = response.json()["workers"][0]
    models = worker["models"]
    assert len(models) == 6
    assert sum(m["estimated_bytes"] or 0 for m in models) == 68
    assert {m["device"] for m in models} == {"cuda:0", "cuda"}
    assert [m["estimated_bytes"] for m in models[:4]] == [36, 0, 32, 0]
    assert all(m["method"] == "CUDA tensor storage" for m in models[:4])
    assert all(
        m["method"] == "estimate unavailable" and m["estimated_bytes"] is None
        for m in models[4:]
    )
    assert {m["name"] for m in models} == {
        "Whisper base",
        "Nemotron",
        "Overlap detection",
        "Voice embedding",
        "Parakeet",
        "Canary 1B",
    }
    assert worker["cuda_reserved_bytes"] == {"cuda:0": 4096}
