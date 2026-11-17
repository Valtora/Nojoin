"""Heartbeat state comes from real Celery requests, never decoded arguments."""

import importlib
import sys
import time
from types import ModuleType, SimpleNamespace

import pytest
from celery.worker import state
from celery.worker.request import Request
from kombu import Message

from backend.celery_app import HeartbeatThread, celery_app
from backend.tests.resource_fakes import MemoryRedis
from backend.utils.resource_worker import (
    WORKER_RESOURCE_PREFIX,
    collect_worker_resources,
)


def publish_worker(broker: MemoryRedis) -> None:
    def request(identity: str) -> Request:
        return Request(
            Message(
                body=(["private argument"], {"secret": "private value"}, {}),
                headers={"id": identity, "task": "celery.backend_cleanup"},
            ),
            decoded=True,
            app=celery_app,
        )

    active, reserved = request("active"), request("reserved")
    active.time_start = time.time() - 10
    worker = SimpleNamespace(
        hostname="gpu@synthetic",
        app=celery_app,
        consumer=SimpleNamespace(
            task_consumer=SimpleNamespace(queues=[SimpleNamespace(name="gpu")])
        ),
    )
    state.task_reserved(active)
    state.task_accepted(active)
    state.task_reserved(reserved)
    try:
        HeartbeatThread("unused", worker=worker).publish(broker)
    finally:
        state.task_ready(active)
        state.task_ready(reserved)


def test_worker_report_contains_active_state_and_expires() -> None:
    import json

    broker = MemoryRedis()
    publish_worker(broker)
    key = WORKER_RESOURCE_PREFIX + "gpu@synthetic"
    raw = broker.get(key)
    body = json.loads(raw)
    assert body["running"][0]["name"] == "celery.backend_cleanup"
    assert body["running"][0]["elapsed_seconds"] >= 10
    assert body["running"][0]["started_at"]
    assert body["reserved_count"] == 1
    assert "private" not in raw and "secret" not in raw
    broker.now = 14
    assert broker.get(key) is not None
    broker.now = 16
    assert broker.get(key) is None


@pytest.mark.parametrize(
    "cache_name,public_name",
    [
        ("whisper", "Whisper"),
        ("nemotron", "Nemotron"),
        ("segmentation", "Overlap detection"),
        ("embedding", "Voice embedding"),
        ("parakeet", "Parakeet"),
        ("canary", "Canary 1B"),
    ],
)
def test_worker_model_names_exclude_private_cache_paths(
    cache_name: str, public_name: str, monkeypatch: pytest.MonkeyPatch
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

    parakeet, canary = ParakeetEngine(), CanaryEngine()
    monkeypatch.setitem(transcribe._ENGINE_REGISTRY, "parakeet", parakeet)
    monkeypatch.setitem(transcribe._ENGINE_REGISTRY, "canary", canary)
    private_path = "/home/private-person/configured-model-directory/weights.bin"
    caches = {
        "whisper": (whisper_engine._model_cache, private_path),
        "nemotron": (nemotron_model._RUNTIMES, private_path),
        "segmentation": (segmentation_model._model_cache, (private_path, "cpu")),
        "embedding": (embedding_core._embedding_model_cache, (private_path, "cpu")),
        "parakeet": (parakeet._model_cache, private_path),
        "canary": (canary._model_cache, private_path),
    }
    cache, key = caches[cache_name]
    # Real caches and tensor traversal; no loader or collector is replaced.
    monkeypatch.setitem(cache, key, torch.nn.Linear(2, 3))
    report = collect_worker_resources(
        SimpleNamespace(hostname="gpu@synthetic", app=celery_app)
    )
    assert private_path not in report.model_dump_json()
    assert public_name in {model.name for model in report.models}


@pytest.mark.parametrize("device", ["cpu", "cuda:0"])
def test_worker_reports_final_diarisation_pipeline(
    device: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import torch

    # Pyannote's optional runtime is the external edge. Import the real loader
    # module so the collector reads its actual cache, even without worker extras.
    audio = ModuleType("pyannote.audio")
    pipeline_type = type("Pipeline", (), {"device": torch.device(device)})
    audio.Pipeline = pipeline_type
    monkeypatch.setitem(sys.modules, "pyannote.audio", audio)
    monkeypatch.delitem(sys.modules, "backend.processing.diarize", raising=False)
    diarize = importlib.import_module("backend.processing.diarize")
    private_path = "/home/private-person/configured-model-directory/pipeline"
    monkeypatch.setitem(diarize._pipeline_cache, private_path, pipeline_type())

    report = collect_worker_resources(
        SimpleNamespace(hostname="gpu@synthetic", app=celery_app)
    )
    pipelines = [model for model in report.models if model.name == "Speaker separation"]
    assert len(pipelines) == 1
    assert pipelines[0].device == device
    assert pipelines[0].estimated_bytes is None
    assert pipelines[0].method == "estimate unavailable"
    assert private_path not in report.model_dump_json()
