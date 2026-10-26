"""Final speaker routing, unchanged fallback ownership, and native release."""

from __future__ import annotations

import asyncio
import sys
import weakref
from concurrent.futures import CancelledError
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import torch
from celery.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded
from pyannote.core import Annotation, Segment

from backend.processing import final_diarization as final
from backend.processing import nemotron_model
from backend.utils import nemotron_model_utils as assets

pytestmark = pytest.mark.usefixtures("stub_celery_dispatch")


@pytest.fixture
def lane(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    community = Annotation(uri="meeting")
    community[Segment(0, 1)] = "SPEAKER_00"
    state = SimpleNamespace(
        community=community,
        calls=[],
        probabilities=np.zeros((11, 8), dtype=np.float32),
        failure=None,
        assets="verified",
    )
    monkeypatch.setitem(
        sys.modules,
        "backend.processing.diarize",
        SimpleNamespace(diarize_audio=lambda *a, **kw: state.community),
    )
    monkeypatch.setattr(
        assets, "inspect_nemotron_model", lambda: SimpleNamespace(state=state.assets)
    )
    monkeypatch.setitem(final.config_manager.config, "speaker_engine", "auto")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(
        final, "_release_community", lambda: state.calls.append("release")
    )
    monkeypatch.setattr(final, "_read_audio", lambda path: np.zeros(1600, np.float32))
    monkeypatch.setattr(
        final, "release_final_native", lambda: state.calls.append("clear")
    )

    def infer(audio: np.ndarray) -> np.ndarray:
        state.calls.append("infer")
        if state.failure is not None:
            raise state.failure
        return state.probabilities

    def load(*, device: str) -> Any:
        state.calls.append(device)
        return SimpleNamespace(infer_offline=infer)

    monkeypatch.setattr(nemotron_model, "load_nemotron_model", load)
    return state


def _run(state: SimpleNamespace, **kwargs: Any) -> tuple[Annotation, dict]:
    status: dict = {}
    result = final.diarize_final_audio(
        "meeting.wav", config={"processing_device": "cuda:1"}, status=status, **kwargs
    )
    assert result is not None
    return result, status


def test_union_keeps_fresh_names_and_releases_in_order(lane: SimpleNamespace) -> None:
    lane.probabilities[:5, 2] = 0.9
    result, status = _run(lane)
    assert result is not lane.community
    assert result.labels() == ["N:L2"]
    assert status == {"route": "union", "reason": "channel_7_inactive"}
    assert lane.calls == ["release", "cuda:1", "infer", "clear"]
    assert lane.community.labels() == ["SPEAKER_00"]


@pytest.mark.parametrize(
    ("probability", "expected"),
    [(0.5, "union"), (0.50001, "union"), (0.5005, "community_channel_7")],
)
def test_route_uses_float16_threshold_on_final_frame(
    lane: SimpleNamespace, probability: float, expected: str
) -> None:
    lane.probabilities[-1, 7] = probability
    result, status = _run(lane)
    assert status["route"] == expected
    assert (result is lane.community) == (expected == "community_channel_7")


@pytest.mark.parametrize(
    "reason", ["classic", "cpu", "cpu_unavailable", "speaker_cap", "missing", "invalid"]
)
def test_ineligible_never_loads_native(
    lane: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    kwargs: dict[str, Any] = {"status": {}}
    config = {"processing_device": "cuda"}
    if reason == "classic":
        monkeypatch.setitem(final.config_manager.config, "speaker_engine", "classic")
    elif reason == "cpu":
        config["processing_device"] = "cpu"
    elif reason == "cpu_unavailable":
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    elif reason == "speaker_cap":
        kwargs["max_speakers"] = 0  # raw presence, even when normalization discards it
    else:
        lane.assets = reason
    result = final.diarize_final_audio("meeting.wav", config, **kwargs)
    assert result is lane.community
    assert kwargs["status"]["route"] == "ineligible"
    assert "infer" not in lane.calls and "release" not in lane.calls


@pytest.mark.parametrize("community", [None, Annotation()])
def test_empty_community_preserves_baseline(
    lane: SimpleNamespace, community: Annotation | None
) -> None:
    lane.community = community
    status: dict = {}
    assert final.diarize_final_audio("meeting.wav", status=status) is community
    assert status == {"route": "ineligible", "reason": "community_empty"}
    assert lane.calls == []


@pytest.mark.parametrize(
    "failure", [RuntimeError("out of memory"), KeyError("bad output")]
)
def test_native_failure_returns_original_and_releases(
    lane: SimpleNamespace, failure: Exception
) -> None:
    lane.failure = failure
    result, status = _run(lane)
    assert result is lane.community
    assert status == {"route": "failed", "reason": type(failure).__name__}
    assert lane.calls[-1] == "clear"


@pytest.mark.parametrize(
    "probabilities",
    [np.zeros((0, 8), np.float32), np.full((11, 8), np.nan, np.float32)],
)
def test_malformed_native_is_fallback(
    lane: SimpleNamespace, probabilities: np.ndarray
) -> None:
    lane.probabilities = probabilities
    result, status = _run(lane)
    assert result is lane.community
    assert status["route"] == "failed"


@pytest.mark.parametrize(
    "failure",
    [
        SoftTimeLimitExceeded(),
        TimeLimitExceeded(),
        CancelledError(),
        asyncio.CancelledError(),
    ],
)
def test_interruptions_escape_and_release(
    lane: SimpleNamespace, failure: BaseException
) -> None:
    lane.failure = failure
    with pytest.raises(type(failure)):
        _run(lane)
    assert lane.calls[-1] == "clear"


def test_silent_native_keeps_community_with_fresh_namespace(
    lane: SimpleNamespace,
) -> None:
    result, status = _run(lane)
    assert status["route"] == "union"
    assert result.labels() == ["C:SPEAKER_00"]
    assert list(result.itersegments()) == list(lane.community.itersegments())


@pytest.mark.parametrize(
    "selection",
    [
        pytest.param("invalid", id="string"),
        pytest.param(["auto"], id="list"),
        pytest.param({"mode": "auto"}, id="dict"),
    ],
)
def test_invalid_install_engine_uses_auto_and_ignores_user_override(
    lane: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, selection: Any
) -> None:
    monkeypatch.setitem(final.config_manager.config, "speaker_engine", selection)
    status: dict = {}
    result = final.diarize_final_audio(
        "meeting.wav",
        {"processing_device": "auto", "speaker_engine": "classic"},
        status=status,
    )
    assert result is not lane.community
    assert status["route"] == "union"
    assert lane.calls == ["release", "cuda", "infer", "clear"]


@pytest.mark.parametrize("chained", [False, True])
def test_failed_frame_does_not_pin_runtime_during_cleanup(
    lane: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, chained: bool
) -> None:
    holder: list[Any] = []
    refs: list[Any] = []

    class Runtime:
        def infer_offline(self, audio: np.ndarray) -> np.ndarray:
            if chained:
                try:
                    self.fail()
                except ValueError as exc:
                    raise RuntimeError("wrapped native forward failure") from exc
            raise RuntimeError("native forward failure")

        def fail(self) -> None:
            raise ValueError("native tensor owner")

    def load(*, device: str) -> Runtime:
        runtime = Runtime()
        refs.append(weakref.ref(runtime))
        holder.append(runtime)
        return runtime

    def release() -> None:
        holder.clear()
        final.gc.collect()
        assert refs[0]() is None

    monkeypatch.setattr(nemotron_model, "load_nemotron_model", load)
    monkeypatch.setattr(final, "release_final_native", release)
    result, status = _run(lane)
    assert result is lane.community and status["route"] == "failed"
