"""Deleting a cached model.

Deletion used to run inside the API process, which mounts the shared model
volume read-only, so every attempt failed with EROFS on a Docker install. The
work now goes to a worker lane, which is the only place with write access, and
these tests pin the dispatch plus the mapping of its result back onto the status
codes the UI already handles.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from celery.exceptions import TimeoutError as CeleryTimeoutError
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.api.deps import get_current_user
from backend.api.v1.endpoints import system

DELETE_TASK = "backend.worker.tasks.delete_model_task"


class _FakeTask:
    def __init__(self, result=None, raises=None):
        self._result = result
        self._raises = raises

    def get(self, timeout=None):
        del timeout
        if self._raises:
            raise self._raises
        return self._result


@pytest.fixture()
def delete_app(monkeypatch):
    """System router with the delete task recorded rather than sent."""
    sent: list[tuple[str, dict]] = []
    box = SimpleNamespace(task=_FakeTask({"status": "deleted", "message": "Gone."}))

    def fake_send_task(name, kwargs=None, **_):
        sent.append((name, kwargs or {}))
        return box.task

    monkeypatch.setattr(system.celery_app, "send_task", fake_send_task)

    app = FastAPI()
    app.include_router(system.router, prefix="/system")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=1, role="owner", is_superuser=True, settings={}
    )
    return app, sent, box


async def _delete(app, model="canary", query=""):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as client:
        return await client.delete(f"/system/models/{model}{query}")


def test_deletion_is_dispatched_to_a_worker(delete_app):
    app, sent, _ = delete_app

    response = asyncio.run(_delete(app, "whisper", "?variant=medium"))

    assert response.status_code == 200
    # The worker resolves the path itself; only the model identity is sent.
    assert sent == [(DELETE_TASK, {"model_name": "whisper", "variant": "medium"})]


def test_a_missing_model_is_reported_as_not_found(delete_app):
    app, _, box = delete_app
    box.task = _FakeTask({"status": "not_found", "message": "nope"})

    response = asyncio.run(_delete(app))

    assert response.status_code == 404


def test_a_bundled_asset_is_refused_rather_than_deleted(delete_app):
    app, _, box = delete_app
    box.task = _FakeTask({"status": "forbidden", "message": "bundled with the repo"})

    response = asyncio.run(_delete(app, "pyannote"))

    assert response.status_code == 400
    assert "bundled" in response.json()["detail"]


def test_a_worker_failure_is_surfaced(delete_app):
    app, _, box = delete_app
    box.task = _FakeTask({"status": "error", "message": "disk exploded"})

    response = asyncio.run(_delete(app))

    assert response.status_code == 500


def test_an_unreachable_worker_does_not_hang_the_request(delete_app):
    app, _, box = delete_app
    box.task = _FakeTask(raises=CeleryTimeoutError("no worker"))

    response = asyncio.run(_delete(app))

    assert response.status_code == 504
    assert "still be running" in response.json()["detail"]


def test_an_unknown_model_name_is_rejected_before_dispatch(delete_app):
    app, sent, _ = delete_app

    response = asyncio.run(_delete(app, "../../etc"))

    assert response.status_code in (400, 404)
    assert sent == []


def test_a_non_admin_cannot_delete_a_model(delete_app):
    app, sent, _ = delete_app
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=2, role="user", is_superuser=False, settings={}
    )

    response = asyncio.run(_delete(app))

    assert response.status_code == 403
    assert sent == []


# --- The worker side --------------------------------------------------------------


def test_the_task_reports_a_refusal_rather_than_raising(monkeypatch):
    """Celery's JSON serialiser would not carry the exception type across."""
    from backend.worker.tasks import system as worker_system

    def boom(model_name, whisper_model_size=None):
        raise ValueError("bundled with the repository")

    monkeypatch.setattr("backend.preload_models.delete_model", boom)

    result = worker_system.delete_model_task.run(model_name="pyannote", variant=None)

    assert result["status"] == "forbidden"
    assert "bundled" in result["message"]


def test_the_task_reports_success(monkeypatch):
    from backend.worker.tasks import system as worker_system

    monkeypatch.setattr(
        "backend.preload_models.delete_model",
        lambda model_name, whisper_model_size=None: True,
    )

    result = worker_system.delete_model_task.run(model_name="canary", variant=None)

    assert result["status"] == "deleted"
