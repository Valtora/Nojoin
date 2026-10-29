"""Settings keeps the aligner's retry backoff visible across Canary preparation."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import onnx_asr
import pytest
from httpx import AsyncClient

from backend import preload_models
from backend.services import model_preparation, model_status
from backend.tests.model_preparation_helpers import settings_client
from backend.utils import download_progress, pinned_model_control
from backend.utils.forced_aligner_manifest import ForcedAlignerError
from backend.utils.forced_aligner_model_utils import control
from backend.worker.tasks.system import download_models_task


def _fail_queued_aligner(task_id: str) -> None:
    download_models_task.push_request(id=task_id)
    try:
        with pytest.raises(ForcedAlignerError, match="offline"):
            download_models_task.run(pinned_model="forced_aligner", include_core=False)
    finally:
        download_models_task.pop_request()
    assert control.pending_task() is None


async def _aligner_status(client: AsyncClient) -> dict[str, Any]:
    response = await client.get("/system/models/status")
    assert response.status_code == 200, response.text
    return response.json()["forced_aligner"]


@pytest.fixture
def retry_clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    clock = [time.time()]
    source = SimpleNamespace(time=lambda: clock[0])
    for module in (pinned_model_control, model_status, download_progress):
        monkeypatch.setattr(module, "time", source)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    # Only the external download/inference and Celery result edges are replaced.
    monkeypatch.setattr(onnx_asr, "load_model", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(download_models_task, "update_state", lambda **_kwargs: None)
    return clock


def test_settings_reports_retry_wait_after_canary_finishes_during_backoff(
    model_cache_env: Path,
    download_progress_in_memory: dict[str, str],
    stub_celery_dispatch: list[Any],
    retry_clock: list[float],
) -> None:
    task_id = model_preparation.request_pinned_preparation(
        "forced_aligner", recording_id=1
    )
    assert task_id is not None
    _fail_queued_aligner(task_id)
    failed_at = retry_clock[0]
    preload_models.download_models(transcription_backend="canary", include_core=False)
    assert len(stub_celery_dispatch) == 1
    assert control.pending_task() is None

    async def check() -> None:
        async with settings_client() as client:
            row = await _aligner_status(client)
            assert row["state"] == "retry_wait"
            assert row["downloaded"] is False
            assert row["verification_state"] == "missing"
            retry_clock[0] = failed_at + pinned_model_control.FAILURE_BACKOFF - 0.001
            assert (
                model_preparation.request_pinned_preparation(
                    "forced_aligner", recording_id=2
                )
                is None
            )
            assert (await _aligner_status(client))["state"] == "retry_wait"
            assert len(stub_celery_dispatch) == 1
            # At the boundary another first-use request can queue the transfer.
            retry_clock[0] = failed_at + pinned_model_control.FAILURE_BACKOFF
            assert (
                model_preparation.request_pinned_preparation(
                    "forced_aligner", recording_id=3
                )
                is not None
            )
            assert (await _aligner_status(client))["state"] == "downloading"
            assert len(stub_celery_dispatch) == 2

    asyncio.run(check())


def test_settings_explicit_retry_bypasses_first_use_backoff(
    model_cache_env: Path,
    download_progress_in_memory: dict[str, str],
    stub_celery_dispatch: list[Any],
    retry_clock: list[float],
) -> None:
    task_id = model_preparation.request_pinned_preparation(
        "forced_aligner", recording_id=1
    )
    assert task_id is not None
    _fail_queued_aligner(task_id)

    async def check() -> None:
        async with settings_client() as client:
            assert (await _aligner_status(client))["state"] == "retry_wait"
            response = await client.post(
                "/system/models/prepare", json={"target": "forced_aligner"}
            )
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "queued"
            assert (await _aligner_status(client))["state"] == "downloading"
            assert len(stub_celery_dispatch) == 2
            assert stub_celery_dispatch[-1][2] == {
                "pinned_model": "forced_aligner",
                "include_core": False,
            }

    asyncio.run(check())
