"""Concurrent preparations keep their progress independent at the API boundary."""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

import pytest

from backend import preload_models
from backend.api.services import health_service
from backend.tests.model_preparation_helpers import settings_client
from backend.tests.nemotron_asset_helpers import native_assets as native_assets
from backend.tests.test_forced_aligner_assets import tiny_assets as tiny_assets
from backend.utils import download_progress
from backend.utils import nemotron_model_utils as nemotron
from backend.utils.download_progress import set_download_progress
from backend.utils.forced_aligner_model_utils import control as aligner_control
from backend.utils.pinned_model_assets import PinnedModelAssets


@pytest.fixture
def blocked_downloads(
    native_assets: tuple[Path, dict[str, bytes]],
    tiny_assets: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, Event], dict[str, Event]]:
    native_path, native_payloads = native_assets
    aligner_path, _manifest = tiny_assets
    aligner_payloads = {
        entry.name: entry.read_bytes() for entry in aligner_path.iterdir()
    }
    for entry in aligner_path.iterdir():
        entry.unlink()
    entered = {name: Event() for name in ("nemotron", "forced_aligner")}
    release = {name: Event() for name in entered}

    def transfer(destination: Path) -> None:
        model = "nemotron" if destination.parent == native_path else "forced_aligner"
        entered[model].set()
        assert release[model].wait(15), "Test did not release the download edge"
        payloads = native_payloads if model == "nemotron" else aligner_payloads
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payloads[destination.name])

    monkeypatch.setattr(nemotron, "download_asset", transfer)
    monkeypatch.setattr(
        PinnedModelAssets, "download", lambda _self, path: transfer(path)
    )
    return entered, release


@pytest.mark.parametrize(
    "stage",
    ["whisper", "parakeet", "canary", "pyannote", "embedding", "segmentation", "vad"],
)
def test_concurrent_preparations_keep_separate_progress_through_real_readers(
    stage: str,
    blocked_downloads: tuple[dict[str, Event], dict[str, Event]],
    download_progress_in_memory: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release = blocked_downloads
    clock = [time.time()]
    monkeypatch.setattr(
        download_progress, "time", SimpleNamespace(time=lambda: clock[0])
    )
    set_download_progress(37, "Core transfer", stage=stage)

    async def observe() -> None:
        async with settings_client() as client:
            for model in entered:
                response = await client.get(
                    "/system/download-progress", params={"model": model}
                )
                assert response.status_code == 200, response.text
                assert response.json()["stage"] == model
                assert response.json()["status"] == "downloading"
            status = await client.get("/system/models/status")
            assert status.status_code == 200, status.text
            assert all(
                status.json()[model]["state"] == "downloading" for model in entered
            )
            release["nemotron"].set()
            clock[0] += 31
            native.result(timeout=15)
            aggregate = (await client.get("/system/download-progress")).json()
            assert aggregate["status"] == "downloading"
            core = (
                await client.get("/system/download-progress", params={"model": stage})
            ).json()
            assert core["stage"] == stage and core["progress"] == 37
            assert health_service._current_download_summary()["stage"] == stage
            # A new core stage clears only core progress, leaving both pinned records.
            set_download_progress(
                100, "Core complete", stage="complete", status="complete"
            )
            status = (await client.get("/system/models/status")).json()
            assert status["nemotron"]["state"] == "ready"
            assert status["forced_aligner"]["state"] == "downloading"
            aggregate = (await client.get("/system/download-progress")).json()
            assert aggregate["stage"] == "forced_aligner"
            assert aggregate["status"] == "downloading"

    with ThreadPoolExecutor(max_workers=2) as pool:
        native = pool.submit(preload_models.download_models, nemotron_only=True)
        aligner = pool.submit(
            preload_models.download_models, pinned_model="forced_aligner"
        )
        try:
            assert all(event.wait(15) for event in entered.values())
            asyncio.run(observe())
        finally:
            for event in release.values():
                event.set()
        native.result(timeout=15)
        aligner.result(timeout=15)


@pytest.mark.parametrize("pending", [False, True], ids=["worker-lost", "queued"])
def test_a_stale_pinned_transfer_outranks_a_newer_completion_only_while_pending(
    pending: bool,
    download_progress_in_memory: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [time.time()]
    monkeypatch.setattr(
        download_progress, "time", SimpleNamespace(time=lambda: clock[0])
    )
    set_download_progress(40, "Aligner transfer", stage="forced_aligner")
    if pending:
        aligner_control.write_pending("aligner-task")
    # Silent past the freshness window: with no pending marker its worker is gone.
    clock[0] += 31
    set_download_progress(100, "Core complete", stage="complete", status="complete")

    async def observe() -> dict[str, Any]:
        async with settings_client() as client:
            response = await client.get("/system/download-progress")
            assert response.status_code == 200, response.text
            return response.json()

    aggregate = asyncio.run(observe())
    assert aggregate["stage"] == ("forced_aligner" if pending else "complete")
    assert aggregate["status"] == ("downloading" if pending else "complete")


def test_download_progress_accepts_only_known_model_names(
    download_progress_in_memory: dict[str, str],
) -> None:
    set_download_progress(37, "Core transfer", stage="whisper")

    async def observe() -> None:
        async with settings_client() as client:
            for model in ("core", "whisper", "nemotron", "forced_aligner"):
                response = await client.get(
                    "/system/download-progress", params={"model": model}
                )
                assert response.status_code == 200, response.text
            for model in ("bogus", "nojoin:model_download_progress", ""):
                response = await client.get(
                    "/system/download-progress", params={"model": model}
                )
                assert response.status_code == 422, (model, response.text)

    asyncio.run(observe())


def test_settings_reads_each_pinned_row_from_its_own_record(
    download_progress_in_memory: dict[str, str],
) -> None:
    for model in ("nemotron", "forced_aligner"):
        set_download_progress(0, "Preparation failed.", status="error", stage=model)
    # Newer and downloading, so it is what the aggregate reading returns.
    set_download_progress(20, "Core transfer", stage="whisper")

    async def observe() -> dict[str, Any]:
        async with settings_client() as client:
            response = await client.get("/system/models/status")
            assert response.status_code == 200, response.text
            return response.json()

    status = asyncio.run(observe())
    assert status["nemotron"]["state"] == "failed"
    assert status["forced_aligner"]["state"] == "failed"
