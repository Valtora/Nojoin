"""Heartbeat state comes from real Celery requests, never decoded arguments."""

import time
from types import SimpleNamespace

from celery.worker import state
from celery.worker.request import Request
from kombu import Message

from backend.celery_app import HeartbeatThread, celery_app
from backend.tests.resource_fakes import MemoryRedis
from backend.utils.resource_worker import WORKER_RESOURCE_PREFIX


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
