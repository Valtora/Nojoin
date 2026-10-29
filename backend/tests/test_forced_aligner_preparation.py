"""Preparation selection and model-download lifecycle without weights or Redis."""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend import preload_models
from backend.api.v1.endpoints import system
from backend.services import model_preparation, model_status
from backend.utils import forced_aligner_model_utils as assets


@pytest.fixture
def progress(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    updates: dict[str, Any] = {}

    def record(percent: int, message: str, *args: Any, **kwargs: Any) -> None:
        updates.update(
            progress=percent, message=message, updated_at=time.time(), **kwargs
        )

    monkeypatch.setattr(preload_models, "clear_download_progress", updates.clear)
    monkeypatch.setattr(preload_models, "set_download_progress", record)
    monkeypatch.setattr(model_preparation, "set_download_progress", record)
    monkeypatch.setattr(model_status, "get_download_progress", lambda: updates or None)
    return updates


@pytest.mark.parametrize("entry", ["active", "core", "preload"])
def test_active_and_preload_failures_retain_aligner_stage_for_retry(
    entry: str,
    model_cache_env: Path,
    progress: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(**_kwargs: Any) -> None:
        raise RuntimeError("Transfer failed")

    for name in (
        "_prepare_whisper_model",
        "_prepare_pyannote_models",
        "_prepare_onnx_asr_model",
        "_release_validation_caches",
    ):
        monkeypatch.setattr(preload_models, name, lambda *_args: None)
    monkeypatch.setattr(preload_models, "prepare_forced_aligner_model", fail)
    monkeypatch.setattr(
        preload_models.config_manager,
        "get",
        lambda key, default=None: (
            "canary" if key == "transcription_backend" else default
        ),
    )
    with pytest.raises(RuntimeError, match="Transfer failed"):
        if entry == "preload":
            preload_models.preload_models()
        else:
            preload_models.download_models(
                transcription_backend="canary", include_core=entry == "core"
            )
    assert assets.control.pending_task() is None
    assert progress["stage"] == "forced_aligner"
    assert progress["status"] == "error"
    status = preload_models.check_model_status()
    model_status.add_nemotron_preparation_status(status)
    assert status["forced_aligner"]["state"] == "failed"


@pytest.mark.parametrize("backend", ["whisper", "parakeet", "canary"])
@pytest.mark.parametrize("include_core", [False, True], ids=["active", "core"])
def test_only_canary_preparation_requests_word_timing_assets(
    backend: str,
    include_core: bool,
    progress: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    for name in (
        "_prepare_whisper_model",
        "_prepare_pyannote_models",
        "_prepare_onnx_asr_model",
    ):
        monkeypatch.setattr(preload_models, name, lambda *args: None)
    monkeypatch.setattr(preload_models, "_release_validation_caches", lambda: None)

    def prepare(*, progress: Callable[[str, int], None]) -> Path:
        calls.append("aligner")
        progress("Verified", 100)
        return Path("unused")

    monkeypatch.setattr(preload_models, "prepare_forced_aligner_model", prepare)
    preload_models.download_models(
        transcription_backend=backend, include_core=include_core
    )
    assert calls == (["aligner"] if backend == "canary" else [])
    assert progress["status"] == "complete"


def test_pinned_only_preparation_keeps_priority_models_and_reports_its_own_stage(
    progress: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Standalone aligner preparation touched ASR or priority caches")

    for name in (
        "_release_validation_caches",
        "_prepare_whisper_model",
        "_prepare_pyannote_models",
        "_resolve_onnx_asr_id",
    ):
        monkeypatch.setattr(preload_models, name, forbidden)

    def prepare(*, progress: Callable[[str, int], None]) -> Path:
        progress("Verified", 100)
        return Path("unused")

    monkeypatch.setattr(preload_models, "prepare_forced_aligner_model", prepare)
    preload_models.download_models(pinned_model="forced_aligner")
    assert progress["stage"] == "forced_aligner"
    assert progress["status"] == "complete"


@pytest.mark.parametrize(
    "selection",
    [
        ("canary", "active", "canary", False),
        ("parakeet", "active", "parakeet", False),
        ("whisper", "active", "whisper", True),
        ("canary", "core", "whisper", True),
        ("whisper", "canary", "canary", False),
    ],
)
def test_api_prepares_the_selected_or_explicit_engine(
    selection: tuple[str, str, str, bool],
    stub_celery_dispatch: list,
    progress: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, target, expected_backend, expected_core = selection
    monkeypatch.setattr(system, "is_download_in_progress", lambda: False)
    monkeypatch.setattr(system.config_manager, "reload", lambda: None)
    monkeypatch.setattr(
        system.config_manager,
        "get",
        lambda key, default=None: (
            backend if key == "transcription_backend" else default
        ),
    )
    response = asyncio.run(
        system.prepare_models_endpoint(
            system.ModelPreparationRequest(target=target), current_user=None
        )
    )
    assert response["status"] == "queued"
    kwargs = stub_celery_dispatch[0][2]
    assert kwargs["transcription_backend"] == expected_backend
    assert kwargs["include_core"] is expected_core


def test_explicit_api_target_dispatches_no_asr_or_core_work(
    stub_celery_dispatch: list,
    progress: dict[str, Any],
    model_cache_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(system, "is_download_in_progress", lambda: False)
    monkeypatch.setattr(system.config_manager, "reload", lambda: None)
    asyncio.run(
        system.prepare_models_endpoint(
            system.ModelPreparationRequest(target="forced_aligner"), current_user=None
        )
    )
    assert stub_celery_dispatch[0][2] == {
        "pinned_model": "forced_aligner",
        "include_core": False,
    }
    assert progress["stage"] == "forced_aligner"
    assert not assets.control.path("pending.json").exists()


@pytest.mark.parametrize("fails", [False, True], ids=["success", "failure"])
def test_worker_cleans_its_pending_marker_on_success_and_failure(
    fails: bool,
    model_cache_env: Path,
    progress: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.worker.tasks.system import download_models_task

    def prepare(**kwargs: Any) -> None:
        assert assets.control.pending_task()["task_id"] == "aligner-task"
        if fails:
            raise RuntimeError("Transfer failed")

    monkeypatch.setattr(preload_models, "download_models", prepare)
    download_models_task.push_request(id="aligner-task")
    try:
        if fails:
            with pytest.raises(RuntimeError, match="Transfer failed"):
                download_models_task.run(pinned_model="forced_aligner")
        else:
            download_models_task.run(pinned_model="forced_aligner")
    finally:
        download_models_task.pop_request()
    assert assets.control.pending_task() is None


@pytest.mark.parametrize("backend", ["whisper", "parakeet", "canary"])
def test_status_uses_shared_pending_state_after_progress_goes_stale(
    backend: str,
    model_cache_env: Path,
    progress: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        model_status.config_manager,
        "get",
        lambda key, default=None: (
            backend if key == "transcription_backend" else default
        ),
    )
    progress.update(
        stage="forced_aligner", status="downloading", updated_at=time.time() - 120
    )
    with assets.control.model_lock():
        assets.control.write_pending("silent-transfer")
    status = preload_models.check_model_status()
    model_status.add_nemotron_preparation_status(status)
    aligner = status["forced_aligner"]
    assert aligner["state"] == "downloading"
    assert aligner["size_bytes"] > 1_800_000_000
    assert bool(aligner["unused_reason"]) is (backend != "canary")
    assert status["nemotron"]["state"] == "absent"
    with assets.control.model_lock():
        assets.control.clear_pending("silent-transfer")
    model_status.add_nemotron_preparation_status(status)
    assert status["forced_aligner"]["state"] == "absent"
    progress.update(status="error")
    model_status.add_nemotron_preparation_status(status)
    assert status["forced_aligner"]["state"] == "failed"


def test_deletion_releases_an_imported_aligner_then_removes_only_its_repo(
    model_cache_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = assets.forced_aligner_repo_path()
    repo.mkdir(parents=True)
    (repo / "partial").write_bytes(b"partial download")
    releases: list[bool] = []
    monkeypatch.setitem(
        sys.modules,
        "backend.processing.forced_aligner_model",
        SimpleNamespace(
            release_forced_aligner_model_cache=lambda: releases.append(True)
        ),
    )
    with assets.control.model_lock():
        inode = assets.control.path("prepare.lock").stat().st_ino
    assert preload_models.delete_model("forced_aligner")
    assert releases == [True]
    assert not repo.exists()
    assert assets.control.path("prepare.lock").stat().st_ino == inode


def test_offline_failure_holds_off_live_requests_until_the_backoff_passes(
    model_cache_env: Path,
    progress: dict[str, Any],
    stub_celery_dispatch: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.processing import forced_aligner_model
    from backend.utils import pinned_model_control
    from backend.utils.forced_aligner_manifest import ForcedAlignerError
    from backend.worker.tasks.system import download_models_task

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    # Task progress goes to the Redis result backend.
    monkeypatch.setattr(download_models_task, "update_state", lambda **_: None)
    with pytest.raises(ForcedAlignerError):
        forced_aligner_model._prepared_path()
    name, _args, kwargs = stub_celery_dispatch[0]
    download_models_task.push_request(id=assets.control.pending_task()["task_id"])
    try:
        with pytest.raises(ForcedAlignerError):
            download_models_task.run(**kwargs)
    finally:
        download_models_task.pop_request()
    assert assets.control.pending_task() is None
    for _ in range(5):
        with pytest.raises(ForcedAlignerError):
            forced_aligner_model._prepared_path()
    assert len(stub_celery_dispatch) == 1
    later = time.time() + pinned_model_control.FAILURE_BACKOFF + 1
    monkeypatch.setattr(
        pinned_model_control, "time", SimpleNamespace(time=lambda: later)
    )
    for _ in range(3):
        with pytest.raises(ForcedAlignerError):
            forced_aligner_model._prepared_path()
    assert len(stub_celery_dispatch) == 2


@pytest.mark.parametrize(
    "case",
    [
        ("first_use", "forced_aligner", "cpu"),
        ("first_use", "nemotron", "cpu"),
        ("settings", "forced_aligner", "cpu"),
        ("settings", "active", "gpu"),
    ],
)
def test_pinned_preparation_runs_on_the_cpu_lane(
    case: tuple[str, Any, str],
    model_cache_env: Path,
    progress: dict[str, Any],
    stub_celery_dispatch: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, target, queue = case
    sent: list[dict[str, Any]] = []
    stub = model_preparation.celery_app.send_task

    def publish(name: str, **options: Any) -> Any:
        sent.append(options)
        return stub(name, **options)

    monkeypatch.setattr(model_preparation.celery_app, "send_task", publish)
    if path == "first_use":
        assert model_preparation.request_pinned_preparation(target, recording_id=1)
    else:
        asyncio.run(model_preparation.enqueue_model_preparation(target=target))
    options = {
        key: value
        for key, value in sent[0].items()
        if key not in {"args", "kwargs", "connection"}
    }
    # Resolve the destination the way Celery does when it publishes.
    route = model_preparation.celery_app.amqp.router.route(
        options, model_preparation.MODEL_PREPARATION_TASK
    )
    assert route["queue"].name == queue
