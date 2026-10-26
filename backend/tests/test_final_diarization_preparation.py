"""Final first use uses the actual preparation lock and pending marker."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.processing import final_diarization as final
from backend.services import model_preparation as preparation
from backend.tests.nemotron_asset_helpers import local_files
from backend.tests.nemotron_asset_helpers import native_assets as native_assets
from backend.tests.test_final_diarization import lane as lane
from backend.utils import nemotron_lock as control
from backend.utils import nemotron_model_utils as assets

inspect_local_assets = assets.inspect_nemotron_model


def test_final_first_use_queues_once_then_next_recording_uses_union(
    lane: SimpleNamespace,
    native_assets: tuple[Path, dict[str, bytes]],
    stub_celery_dispatch: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(assets, "inspect_nemotron_model", inspect_local_assets)
    statuses = []
    for recording_id in (41, 42):
        status: dict[str, Any] = {}
        assert (
            final.diarize_final_audio(
                "meeting.wav",
                {"processing_device": "cuda"},
                status=status,
                recording_id=recording_id,
            )
            is lane.community
        )
        statuses.append(status)
    assert statuses == [{"route": "ineligible", "reason": "assets_missing"}] * 2
    assert len(stub_celery_dispatch) == 1
    assert stub_celery_dispatch[0][2] == {"nemotron_only": True, "include_core": False}
    marker = control.pending_task()
    assert marker is not None
    assert "infer" not in lane.calls and "release" not in lane.calls

    snapshot, payloads = native_assets
    local_files(snapshot, payloads)
    assets.prepare_nemotron_model()
    control.complete_pending(marker["task_id"])
    lane.probabilities[:5, 2] = 0.9
    status = {}
    result = final.diarize_final_audio(
        "meeting.wav", {"processing_device": "cuda"}, status=status, recording_id=43
    )
    assert result.labels() == ["N:L2"]
    assert status == {"route": "union", "reason": "channel_7_inactive"}
    assert len(stub_celery_dispatch) == 1
    assert control.pending_task() is None


@pytest.mark.parametrize("state", ["partial", "invalid"])
def test_final_unverified_assets_request_shared_preparation(
    lane: SimpleNamespace, state: str, stub_celery_dispatch: list
) -> None:
    lane.assets = state
    status: dict[str, Any] = {}
    assert final.diarize_final_audio("meeting.wav", status=status) is lane.community
    assert status == {"route": "ineligible", "reason": f"assets_{state}"}
    assert len(stub_celery_dispatch) == 1


@pytest.mark.parametrize("reason", ["cpu", "classic", "speaker_cap"])
def test_final_ineligible_does_not_request_preparation(
    lane: SimpleNamespace,
    reason: str,
    monkeypatch: pytest.MonkeyPatch,
    stub_celery_dispatch: list,
) -> None:
    lane.assets = "missing"
    monkeypatch.setitem(
        final.config_manager.config,
        "speaker_engine",
        "classic" if reason == "classic" else "auto",
    )
    config = {"processing_device": "cpu" if reason == "cpu" else "cuda"}
    cap = 0 if reason == "speaker_cap" else None
    assert (
        final.diarize_final_audio("meeting.wav", config, cap, recording_id=42)
        is lane.community
    )
    assert stub_celery_dispatch == []
    assert not control._control_path("pending.json").exists()


def test_final_does_not_wait_behind_preparation_lock(
    lane: SimpleNamespace, stub_celery_dispatch: list
) -> None:
    lane.assets = "missing"
    with control.model_lock():
        assert final.diarize_final_audio("meeting.wav") is lane.community
    assert stub_celery_dispatch == []
    assert "infer" not in lane.calls


def test_final_dispatch_failure_preserves_community_and_clears_pending(
    lane: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    lane.assets = "missing"

    def fail(*args: Any, **kwargs: Any) -> None:
        raise OSError("broker unavailable")

    monkeypatch.setattr(preparation.celery_app, "send_task", fail)
    status: dict[str, Any] = {}
    assert final.diarize_final_audio("meeting.wav", status=status) is lane.community
    assert status == {"route": "ineligible", "reason": "assets_missing"}
    assert control.pending_task() is None
    assert not control._control_path("pending.json").exists()
