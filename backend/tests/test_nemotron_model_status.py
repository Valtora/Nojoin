"""Model downloads reports native assets without worker-side inference imports."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from backend import preload_models
from backend.api.deps import get_current_user
from backend.api.v1.endpoints import system
from backend.services import model_status
from backend.tests.nemotron_asset_helpers import local_files
from backend.tests.nemotron_asset_helpers import native_assets as native_assets
from backend.utils import nemotron_lock as control
from backend.utils import nemotron_model_utils as assets


@pytest.fixture
def status_app(monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, dict[str, Any]]:
    progress: dict[str, Any] = {}
    monkeypatch.setattr(
        model_status, "get_download_progress", lambda _model=None: progress or None
    )
    monkeypatch.setattr(
        model_status.config_manager, "get", lambda key, default=None: default
    )
    app = FastAPI()
    app.include_router(system.router, prefix="/system")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        role="owner", is_superuser=True
    )
    return app, progress


def _response(app: FastAPI) -> Response:
    async def request() -> Response:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.get("/system/models/status")

    response = asyncio.run(request())
    assert response.status_code == 200, response.text
    return response


@pytest.mark.parametrize(
    ("local_state", "expected"),
    [
        ("missing", "absent"),
        ("partial", "absent"),
        ("verified", "ready"),
        ("invalid", "failed"),
    ],
)
def test_endpoint_maps_asset_verification_states(
    native_assets: tuple[Path, dict[str, bytes]],
    status_app: tuple[FastAPI, dict[str, Any]],
    local_state: str,
    expected: str,
) -> None:
    snapshot, payloads = native_assets
    if local_state != "missing":
        local_files(snapshot, payloads)
    if local_state == "verified":
        assets.prepare_nemotron_model()
    if local_state == "invalid":
        (assets.nemotron_repo_path() / "nojoin-verification.json").write_text("broken")

    data = _response(status_app[0]).json()
    native = data["nemotron"]
    assert native["state"] == expected
    assert native["verification_state"] == local_state
    assert native["downloaded"] is (local_state == "verified")
    assert native["size_bytes"] == sum(len(payload) for payload in payloads.values())
    assert native["unused_reason"] is None
    assert "state" not in data["whisper"]
    assert "size_bytes" not in data["embedding"]


@pytest.mark.parametrize("downloaded", [False, True])
def test_matching_preparation_overrides_missing_or_ready_assets(
    native_assets: tuple[Path, dict[str, bytes]],
    status_app: tuple[FastAPI, dict[str, Any]],
    downloaded: bool,
) -> None:
    if downloaded:
        local_files(*native_assets)
        assets.prepare_nemotron_model()
    app, progress = status_app
    progress.update(stage="nemotron", status="downloading", updated_at=time.time())
    assert _response(app).json()["nemotron"]["state"] == "downloading"


@pytest.mark.parametrize("downloaded", [False, True])
def test_matching_failure_keeps_a_verified_model_ready(
    native_assets: tuple[Path, dict[str, bytes]],
    status_app: tuple[FastAPI, dict[str, Any]],
    downloaded: bool,
) -> None:
    if downloaded:
        local_files(*native_assets)
        assets.prepare_nemotron_model()
    app, progress = status_app
    progress.update(stage="nemotron", status="error", updated_at=time.time())
    expected = "ready" if downloaded else "failed"
    assert _response(app).json()["nemotron"]["state"] == expected


@pytest.mark.parametrize("progress_status", ["downloading", "error"])
def test_unrelated_asr_progress_does_not_change_native_status(
    native_assets: tuple[Path, dict[str, bytes]],
    status_app: tuple[FastAPI, dict[str, Any]],
    progress_status: str,
) -> None:
    app, progress = status_app
    progress.update(stage="whisper", status=progress_status, updated_at=time.time())
    assert _response(app).json()["nemotron"]["state"] == "absent"


def test_stale_progress_without_a_pending_task_is_not_reported_as_active(
    native_assets: tuple[Path, dict[str, bytes]],
    status_app: tuple[FastAPI, dict[str, Any]],
) -> None:
    app, progress = status_app
    progress.update(stage="nemotron", status="downloading", updated_at=time.time() - 31)
    assert _response(app).json()["nemotron"]["state"] == "absent"


@pytest.mark.parametrize("fails", [False, True], ids=["success", "failure"])
def test_explicit_worker_transfer_stays_downloading_during_silent_transfer(
    native_assets: tuple[Path, dict[str, bytes]],
    status_app: tuple[FastAPI, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    fails: bool,
) -> None:
    from backend.worker.tasks.system import download_models_task

    _, payloads = native_assets
    app, progress = status_app
    clock = [1000.0]
    observations: list[str] = []
    monkeypatch.setattr(model_status.time, "time", lambda: clock[0])
    monkeypatch.setattr(
        preload_models, "clear_download_progress", lambda *_args: progress.clear()
    )
    monkeypatch.setattr(download_models_task, "update_state", lambda **kwargs: None)

    def record(percent: int, message: str, **kwargs: Any) -> None:
        progress.update(
            progress=percent, message=message, updated_at=clock[0], **kwargs
        )

    monkeypatch.setattr(preload_models, "set_download_progress", record)

    def transfer(destination: Path) -> None:
        if destination.name == "model.safetensors":
            assert _response(app).json()["nemotron"]["state"] == "downloading"
            for elapsed in (31, 120):
                clock[0] = 1000.0 + elapsed
                observations.append(_response(app).json()["nemotron"]["state"])
                assert observations[-1] == "downloading"
            assert control.pending_task()["task_id"] == "explicit-task"
            if fails:
                raise OSError("Transfer failed after a silent interval")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payloads[destination.name])

    monkeypatch.setattr(assets, "download_asset", transfer)
    download_models_task.push_request(id="explicit-task")
    try:
        if fails:
            with pytest.raises(OSError, match="Transfer failed"):
                download_models_task.run(nemotron_only=True)
        else:
            download_models_task.run(nemotron_only=True)
    finally:
        download_models_task.pop_request()

    assert observations == ["downloading", "downloading"]
    assert control.pending_task() is None
    assert not control._control_path("pending.json").exists()
    assert _response(app).json()["nemotron"]["state"] == (
        "failed" if fails else "ready"
    )


def test_first_use_pending_marker_is_read_without_a_write_lock(
    native_assets: tuple[Path, dict[str, bytes]],
    status_app: tuple[FastAPI, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with control.model_lock():
        control.write_pending("first-use-task")

    def forbidden_lock(**kwargs: Any) -> None:
        pytest.fail("API status attempted to open the worker's write lock")

    monkeypatch.setattr(control, "model_lock", forbidden_lock)
    app, _ = status_app
    assert _response(app).json()["nemotron"]["state"] == "downloading"
    marker_path = control._control_path("pending.json")
    marker = json.loads(marker_path.read_text())
    marker["created_at"] = time.time() - control.PENDING_TTL - 1
    marker_path.write_text(json.dumps(marker))
    assert _response(app).json()["nemotron"]["state"] == "absent"


@pytest.mark.parametrize(
    ("settings", "reason"),
    [
        (
            {"speaker_engine": "classic"},
            "Unused while the classic speaker engine is selected.",
        ),
        ({"processing_device": "cpu"}, "Unused while processing is set to CPU."),
        ({"speaker_engine": "auto", "processing_device": "cuda"}, None),
    ],
)
def test_saved_settings_explain_unused_native_model(
    native_assets: tuple[Path, dict[str, bytes]],
    status_app: tuple[FastAPI, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    settings: dict[str, str],
    reason: str | None,
) -> None:
    monkeypatch.setattr(model_status.config_manager, "get", settings.get)
    assert _response(status_app[0]).json()["nemotron"]["unused_reason"] == reason


def test_manifest_size_and_non_admin_path_redaction(
    tmp_path: Path,
    status_app: tuple[FastAPI, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    app, _ = status_app
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        role="user", is_superuser=False
    )
    data = _response(app).json()
    assert data["nemotron"]["size_bytes"] == 396956673
    for value in data.values():
        assert value["path"] is None
        assert value["checked_paths"] == []
