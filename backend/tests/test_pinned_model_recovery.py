"""Successful pinned repairs clear failure backoff after real byte verification."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from backend.tests.nemotron_asset_helpers import native_assets as native_assets
from backend.tests.test_forced_aligner_assets import tiny_assets as tiny_assets
from backend.utils import forced_aligner_model_utils as aligner
from backend.utils import nemotron_model_utils as nemotron
from backend.utils.nemotron_lock import control as nemotron_control
from backend.utils.pinned_model_assets import PinnedModelAssets
from backend.utils.pinned_model_control import PinnedModelControl


@pytest.fixture(params=["nemotron", "forced_aligner"])
def pinned_repair(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> tuple[Callable[..., Path], Callable[[], Any], PinnedModelControl, Path]:
    if request.param == "nemotron":
        path, payloads = request.getfixturevalue("native_assets")
        control = nemotron_control
        prepare, inspect = (
            nemotron.prepare_nemotron_model,
            nemotron.inspect_nemotron_model,
        )
    else:
        path, _manifest = request.getfixturevalue("tiny_assets")
        payloads = {entry.name: entry.read_bytes() for entry in path.iterdir()}
        (path / "model.safetensors").unlink()
        control = aligner.control
        prepare = aligner.prepare_forced_aligner_model
        inspect = aligner.inspect_forced_aligner_model

    def transfer(destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payloads[destination.name])

    monkeypatch.setattr(nemotron, "download_asset", transfer)
    monkeypatch.setattr(
        PinnedModelAssets, "download", lambda _self, target: transfer(target)
    )
    return prepare, inspect, control, path


def test_successful_repair_clears_backoff_after_download_and_cached_verification(
    pinned_repair: tuple[
        Callable[..., Path], Callable[[], Any], PinnedModelControl, Path
    ],
) -> None:
    prepare, inspect, control, path = pinned_repair
    for _ in range(2):
        control.record_failure()
        assert control.failed_recently()
        assert prepare(progress=lambda *_args: None) == path
        assert inspect().state == "verified"
        assert not control.path("failed.json").exists()
        assert not control.failed_recently()
