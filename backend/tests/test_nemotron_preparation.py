from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import preload_models
from backend.services import model_preparation as preparation
from backend.tests.nemotron_asset_helpers import local_files
from backend.tests.nemotron_asset_helpers import native_assets as native_assets
from backend.utils import nemotron_lock as control
from backend.utils import nemotron_model_utils as assets
from backend.utils.download_progress import PROGRESS_KEY


@pytest.mark.usefixtures("download_progress_in_memory")
def test_lazy_enqueue_deduplicates_and_publishes_owned_id(
    native_assets: tuple, stub_celery_dispatch: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = []
    stub = preparation.celery_app.send_task

    def publish(name: str, **kwargs: object) -> object:
        options.append(kwargs)
        return stub(name, **kwargs)

    monkeypatch.setattr(preparation.celery_app, "send_task", publish)
    task_id = preparation.request_nemotron_preparation(recording_id=1)
    assert task_id
    assert control.pending_task()["task_id"] == task_id
    assert preparation.request_nemotron_preparation(recording_id=2) is None
    assert len(stub_celery_dispatch) == 1
    assert stub_celery_dispatch[0][2] == {"nemotron_only": True, "include_core": False}

    assert options[0]["task_id"] == task_id
    assert options[0]["retry"] is False and options[0]["ignore_result"] is True
    connection = options[0]["connection"]
    assert connection.connect_timeout == 2
    assert connection.transport_options["socket_timeout"] == 2


def test_lazy_request_never_waits_for_download_lock(
    native_assets: tuple, stub_celery_dispatch: list
) -> None:
    with control.model_lock():
        assert preparation.request_nemotron_preparation(recording_id=1) is None
    assert stub_celery_dispatch == []


def test_ready_assets_do_not_dispatch(
    native_assets: tuple, stub_celery_dispatch: list
) -> None:
    snapshot, payloads = native_assets
    local_files(snapshot, payloads)
    assets.prepare_nemotron_model()
    assert preparation.request_nemotron_preparation(recording_id=1) is None
    assert stub_celery_dispatch == []


def test_failed_dispatch_clears_only_its_marker(
    native_assets: tuple,
    stub_celery_dispatch: list,
    download_progress_in_memory: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("fake unavailable broker")

    monkeypatch.setattr(preparation.celery_app, "send_task", fail)
    assert preparation.request_nemotron_preparation(recording_id=1) is None
    assert control.pending_task() is None
    assert not control._control_path("pending.json").exists()
    # The queued progress written before the publish does not linger.
    progress = json.loads(download_progress_in_memory[PROGRESS_KEY])
    assert (progress["status"], progress["stage"]) == ("error", "nemotron")


@pytest.mark.usefixtures("download_progress_in_memory")
def test_expired_task_cannot_clear_replacement_marker(
    native_assets: tuple, stub_celery_dispatch: list
) -> None:
    with control.model_lock():
        control.write_pending("old")
        path = control._control_path("pending.json")
        marker = json.loads(path.read_text())
        marker["created_at"] = time.time() - control.PENDING_TTL - 1
        path.write_text(json.dumps(marker))
    replacement = preparation.request_nemotron_preparation(recording_id=2)
    with control.model_lock():
        control.clear_pending("old")
        assert control.pending_task()["task_id"] == replacement
        control.clear_pending(None)
        assert control.pending_task()["task_id"] == replacement


def test_native_preparation_is_exclusive_and_keeps_retained_caches(
    native_assets: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    monkeypatch.setattr(preload_models, "clear_download_progress", lambda: None)
    monkeypatch.setattr(
        preload_models,
        "set_download_progress",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("native preparation entered ASR/global cache cleanup")

    monkeypatch.setattr(preload_models, "_release_validation_caches", forbidden)
    monkeypatch.setattr(preload_models, "_resolve_onnx_asr_id", forbidden)
    monkeypatch.setattr(preload_models, "_prepare_pyannote_models", forbidden)
    monkeypatch.setattr(preload_models, "_prepare_whisper_model", forbidden)
    snapshot, payloads = native_assets
    local_files(snapshot, payloads)
    preload_models.download_models(nemotron_only=True, transcription_backend="parakeet")
    assert calls[-1][1]["status"] == "complete"
    assert assets.inspect_nemotron_model().state == "verified"


def test_native_offline_failure_reports_not_prepared_without_downloading(
    native_assets: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    import huggingface_hub

    from backend.utils.nemotron_manifest import NemotronOfflineError

    updates: list[tuple[tuple, dict]] = []
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setattr(preload_models, "clear_download_progress", lambda: None)
    monkeypatch.setattr(
        preload_models,
        "set_download_progress",
        lambda *args, **kwargs: updates.append((args, kwargs)),
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("offline preparation attempted a Hub download")

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", forbidden)
    with pytest.raises(NemotronOfflineError, match="not prepared.*offline"):
        preload_models.download_models(nemotron_only=True)

    assert updates[-1] == (
        (0, "Native speaker model is not prepared (offline)."),
        {"status": "error", "stage": "nemotron"},
    )
    assert assets.inspect_nemotron_model().state != "verified"
    assert not (assets.nemotron_repo_path() / "nojoin-verification.json").exists()


def test_native_async_enqueue_does_not_pass_asr_selection(
    native_assets: tuple, stub_celery_dispatch: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    updates: list[dict] = []
    monkeypatch.setattr(
        preparation,
        "set_download_progress",
        lambda *args, **kwargs: updates.append(kwargs),
    )
    asyncio.run(preparation.enqueue_model_preparation(target="nemotron"))
    assert stub_celery_dispatch[0][2] == {"nemotron_only": True, "include_core": False}
    assert updates == [{"status": "downloading", "stage": "nemotron"}]
    # API dispatch must never write the read-only model volume marker.
    assert not control._control_path("pending.json").exists()


@pytest.mark.parametrize("selection", [(None, True), ("core", True), ("active", False)])
@pytest.mark.parametrize("backend", ["whisper", "parakeet"])
def test_preparation_selector_preserves_default_and_asr_only_batches(
    selection: tuple[str | None, bool],
    backend: str,
    stub_celery_dispatch: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, include_core = selection
    monkeypatch.setattr(
        preparation, "set_download_progress", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        preparation.config_manager,
        "get",
        lambda key, default: backend if key == "transcription_backend" else default,
    )
    options = {} if target is None else {"target": target}
    asyncio.run(preparation.enqueue_model_preparation(**options))
    assert stub_celery_dispatch[0][2] == {
        "transcription_backend": backend,
        "whisper_model_size": "turbo",
        "parakeet_model": "parakeet-tdt-0.6b-v3",
        "canary_model": "nemo-canary-1b-v2",
        "include_core": include_core,
    }


def test_invalid_model_is_visible_and_deletable_without_heavy_import(
    native_assets: tuple, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot, payloads = native_assets
    local_files(snapshot, payloads)
    (assets.nemotron_repo_path() / "nojoin-verification.json").write_text("broken")
    released = []
    monkeypatch.setitem(
        sys.modules,
        "backend.processing.nemotron_model",
        SimpleNamespace(release_nemotron_model_cache=lambda: released.append(True)),
    )
    outside = tmp_path / "external-blob"
    (snapshot / "model.safetensors").rename(outside)
    (snapshot / "model.safetensors").symlink_to(outside)
    status = preload_models.check_model_status()["nemotron"]
    assert status["partial"] and not status["downloaded"]
    assert status["verification_error"] == "receipt_or_assets_changed"
    lock_path = control._control_path("prepare.lock")
    with control.model_lock():
        inode = lock_path.stat().st_ino
    assert preload_models.delete_model("nemotron")
    assert released == [True]
    assert outside.read_bytes() == payloads["model.safetensors"]
    assert not assets.nemotron_repo_path().exists()
    assert lock_path.stat().st_ino == inode


def test_native_worker_failure_cleans_owned_pending_marker(
    native_assets: tuple, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.worker.tasks.system import download_models_task

    def fail(**kwargs: object) -> None:
        raise assets.NemotronModelError("fake asset verification failure")

    monkeypatch.setattr(preload_models, "download_models", fail)
    with control.model_lock():
        control.write_pending("our-task")
    download_models_task.push_request(id="our-task")
    try:
        with pytest.raises(assets.NemotronModelError):
            download_models_task.run(nemotron_only=True)
    finally:
        download_models_task.pop_request()
    assert control.pending_task() is None
