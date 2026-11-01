"""A speaker cap keeps the union when it fits and community-1 when it does not (#871).

A cap used to skip Nemotron altogether. Now a capped recording prepares and
runs it as an uncapped one does, and ``choose_capped_speakers`` keeps the
union only if its speaker count is within the cap. Community-1 runs a second
time, with the cap, only when the uncapped run does not already fit.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import torch
from celery.exceptions import SoftTimeLimitExceeded
from pyannote.core import Annotation, Segment

from backend.processing import final_diarization as final
from backend.processing import nemotron_model
from backend.processing.speaker_cap import normalize_speaker_cap
from backend.tests.nemotron_asset_helpers import local_files
from backend.tests.nemotron_asset_helpers import native_assets as native_assets
from backend.tests.test_final_diarization import lane as lane
from backend.utils import nemotron_lock as control
from backend.utils import nemotron_model_utils as assets

pytestmark = pytest.mark.usefixtures("stub_celery_dispatch")

inspect_local_assets = assets.inspect_nemotron_model

SECONDS = 4
FRAMES = SECONDS * 100 + 1


def _speakers(count: int, prefix: str = "SPEAKER") -> Annotation:
    """One second of speech each, in turn, so every speaker has its own span."""
    annotation = Annotation(uri="meeting")
    for index in range(count):
        annotation[Segment(index, index + 1)] = f"{prefix}_{index:02d}"
    return annotation


@pytest.fixture
def capped(lane: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """The lane, with community-1 answering differently with and without a cap."""
    lane.community = _speakers(2)
    lane.capped = _speakers(2, prefix="CAPPED")
    lane.probabilities = np.zeros((FRAMES, 8), dtype=np.float32)

    def diarize_audio(
        audio_path: str, config: dict | None = None, max_speakers: int | None = None
    ) -> Annotation | None:
        lane.calls.append(f"community:{max_speakers}")
        # diarize_audio treats an unusable cap as none, as here.
        return (
            lane.community
            if normalize_speaker_cap(max_speakers) is None
            else lane.capped
        )

    monkeypatch.setitem(
        sys.modules,
        "backend.processing.diarize",
        SimpleNamespace(diarize_audio=diarize_audio),
    )
    monkeypatch.setattr(
        final, "_read_audio", lambda path: np.zeros(SECONDS * 16000, np.float32)
    )
    return lane


def _native(lane: SimpleNamespace, channels: int) -> None:
    """Nemotron hears ``channels`` voices, one per second, as community-1 does."""
    for channel in range(channels):
        lane.probabilities[channel * 100 : (channel + 1) * 100, channel] = 0.9


def _run(lane: SimpleNamespace, cap: int | None) -> tuple[Annotation | None, dict]:
    status: dict[str, Any] = {}
    result = final.diarize_final_audio(
        "meeting.wav",
        config={"processing_device": "cuda:1"},
        max_speakers=cap,
        status=status,
        recording_id=42,
    )
    return result, status


def test_union_within_the_cap_is_kept_from_one_community_run(
    capped: SimpleNamespace,
) -> None:
    _native(capped, 2)
    result, status = _run(capped, cap=2)
    assert result is not None and result.labels() == ["N:L0", "N:L1"]
    assert status == {
        "route": "union",
        "reason": "union_within_cap",
        "cap": 2,
        "native_route": "union",
        "native_reason": "channel_7_inactive",
        "union_speakers": 2,
        "community_speakers": 2,
        "speakers": 2,
    }
    assert capped.calls == ["community:None", "release", "cuda:1", "infer", "clear"]


def test_union_over_the_cap_reuses_community_that_fits(
    capped: SimpleNamespace,
) -> None:
    # Nemotron hears a third voice that community-1 put with the second.
    _native(capped, 3)
    result, status = _run(capped, cap=2)
    assert result is capped.community
    assert status["route"] == "community_capped"
    assert status["reason"] == "union_over_cap"
    assert status["union_speakers"] == 3
    assert status["community_speakers"] == 2
    assert status["community_reused"] is True
    assert status["speakers"] == 2
    assert capped.calls.count("community:None") == 1
    assert "community:2" not in capped.calls


def test_union_and_community_over_the_cap_run_community_with_it(
    capped: SimpleNamespace,
) -> None:
    capped.community = _speakers(3)
    _native(capped, 3)
    result, status = _run(capped, cap=2)
    assert result is capped.capped
    assert status["route"] == "community_capped"
    assert status["reason"] == "union_over_cap"
    assert status["union_speakers"] == 3
    assert status["community_speakers"] == 3
    assert status["community_reused"] is False
    assert status["speakers"] == 2
    # The capped run waits until Nemotron has been released.
    assert capped.calls == [
        "community:None",
        "release",
        "cuda:1",
        "infer",
        "clear",
        "community:2",
    ]


def test_a_cap_above_the_union_count_is_the_uncapped_result(
    capped: SimpleNamespace,
) -> None:
    _native(capped, 2)
    uncapped, _ = _run(capped, cap=None)
    capped.calls.clear()
    result, status = _run(capped, cap=50)
    assert result is not None and uncapped is not None
    assert list(result.itertracks(yield_label=True)) == list(
        uncapped.itertracks(yield_label=True)
    )
    assert status["route"] == "union"
    assert capped.calls.count("community:None") == 1


@pytest.mark.parametrize(
    ("community", "rerun"), [(2, False), (3, True)], ids=["fits", "over"]
)
def test_channel_seven_falls_back_to_community_with_the_cap(
    capped: SimpleNamespace, community: int, rerun: bool
) -> None:
    capped.community = _speakers(community)
    capped.probabilities[-1, 7] = 0.9
    result, status = _run(capped, cap=2)
    assert result is (capped.capped if rerun else capped.community)
    assert status["route"] == "community_capped"
    assert status["reason"] == "union_unavailable"
    assert status["native_route"] == "community_channel_7"
    assert status["union_speakers"] is None
    assert status["community_reused"] is not rerun
    assert ("community:2" in capped.calls) is rerun


def test_native_failure_falls_back_to_community_with_the_cap(
    capped: SimpleNamespace,
) -> None:
    capped.community = _speakers(3)
    capped.failure = RuntimeError("out of memory")
    result, status = _run(capped, cap=2)
    assert result is capped.capped
    assert status["route"] == "community_capped"
    assert status["native_route"] == "failed"
    assert status["native_reason"] == "RuntimeError"
    assert capped.calls[-2:] == ["clear", "community:2"]


def test_unreadable_assets_take_the_native_route_and_record_its_failure(
    capped: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail() -> Any:
        raise ValueError("bad asset config")

    monkeypatch.setattr(assets, "inspect_nemotron_model", fail)
    result, status = _run(capped, cap=2)
    assert result is capped.community
    assert status["native_route"] == "failed"
    assert status["native_reason"] == "ValueError"
    assert status["community_reused"] is True
    assert "infer" not in capped.calls


def test_interruption_releases_native_and_runs_nothing_more(
    capped: SimpleNamespace,
) -> None:
    capped.community = _speakers(3)
    capped.failure = SoftTimeLimitExceeded()
    with pytest.raises(SoftTimeLimitExceeded):
        _run(capped, cap=2)
    assert capped.calls[-2:] == ["infer", "clear"]
    assert "community:2" not in capped.calls


@pytest.mark.parametrize("reason", ["classic", "cpu", "cpu_unavailable"])
def test_no_union_means_one_community_run_with_the_cap(
    capped: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    config = {"processing_device": "cpu" if reason == "cpu" else "cuda"}
    if reason == "classic":
        monkeypatch.setitem(final.config_manager.config, "speaker_engine", "classic")
    elif reason == "cpu_unavailable":
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    status: dict[str, Any] = {}
    result = final.diarize_final_audio("meeting.wav", config, 2, status=status)
    assert result is capped.capped
    expected = "classic" if reason == "classic" else "cpu"
    assert status == {
        "route": "ineligible",
        "reason": expected,
        "cap": 2,
        "speakers": 2,
    }
    assert capped.calls == ["community:2"]


@pytest.mark.parametrize("cap", [0, 51, True, "four"])
def test_an_unusable_cap_is_no_cap(capped: SimpleNamespace, cap: Any) -> None:
    _native(capped, 3)
    result, status = _run(capped, cap=cap)
    assert result is not None and len(result.labels()) == 3
    assert status == {"route": "union", "reason": "channel_7_inactive"}


@pytest.mark.parametrize("empty", [None, Annotation()])
def test_empty_uncapped_community_skips_native(
    capped: SimpleNamespace, empty: Annotation | None
) -> None:
    capped.community = empty
    result, status = _run(capped, cap=2)
    assert result is empty
    assert status == {"route": "ineligible", "reason": "community_empty", "cap": 2}
    assert capped.calls == ["community:None"]


def test_choice_is_logged_with_counts_only(
    capped: SimpleNamespace, caplog: pytest.LogCaptureFixture
) -> None:
    capped.community = _speakers(3)
    _native(capped, 3)
    with caplog.at_level(logging.INFO, logger=final.__name__):
        _run(capped, cap=2)
    lines = [r.getMessage() for r in caplog.records if "Capped final" in r.getMessage()]
    assert lines == [
        "Capped final speakers for recording 42: route=community_capped "
        "reason=union_over_cap cap=2 union_speakers=3 community_speakers=3 "
        "community_reused=False speakers=2 native_route=union "
        "native_reason=channel_7_inactive"
    ]


@pytest.mark.usefixtures("download_progress_in_memory")
def test_capped_first_use_prepares_once_then_uses_the_union(
    capped: SimpleNamespace,
    native_assets: tuple[Path, dict[str, bytes]],
    stub_celery_dispatch: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """As an uncapped recording does: queue the download, keep community-1."""
    monkeypatch.setattr(assets, "inspect_nemotron_model", inspect_local_assets)
    for recording_id in (41, 42):
        status: dict[str, Any] = {}
        result = final.diarize_final_audio(
            "meeting.wav",
            {"processing_device": "cuda"},
            2,
            status=status,
            recording_id=recording_id,
        )
        assert result is capped.capped
        assert status["route"] == "ineligible"
        assert status["reason"] == "assets_missing"
    assert len(stub_celery_dispatch) == 1
    assert stub_celery_dispatch[0][2] == {"nemotron_only": True, "include_core": False}
    # Without assets community-1 runs once per recording, with the cap.
    assert capped.calls == ["community:2", "community:2"]
    marker = control.pending_task()
    assert marker is not None

    snapshot, payloads = native_assets
    local_files(snapshot, payloads)
    assets.prepare_nemotron_model()
    control.complete_pending(marker["task_id"])
    _native(capped, 2)
    capped.calls.clear()
    status = {}
    result = final.diarize_final_audio(
        "meeting.wav", {"processing_device": "cuda"}, 2, status=status, recording_id=43
    )
    assert result is not None and result.labels() == ["N:L0", "N:L1"]
    assert status["route"] == "union"
    assert capped.calls == ["community:None", "release", "cuda", "infer", "clear"]
    assert len(stub_celery_dispatch) == 1


def test_native_loads_with_the_same_device_as_uncapped(
    capped: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    devices: list[str] = []

    def load(*, device: str) -> Any:
        devices.append(device)
        return SimpleNamespace(infer_offline=lambda audio: capped.probabilities)

    monkeypatch.setattr(nemotron_model, "load_nemotron_model", load)
    _run(capped, cap=None)
    _run(capped, cap=4)
    assert devices == ["cuda:1", "cuda:1"]
