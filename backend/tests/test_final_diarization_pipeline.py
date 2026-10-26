"""Final wrapper observation, task interruption and global native release hooks."""

from __future__ import annotations

import asyncio
import importlib
from concurrent.futures import CancelledError
from types import SimpleNamespace
from typing import Any

import pytest
from celery.exceptions import SoftTimeLimitExceeded, TimeLimitExceeded
from pyannote.core import Annotation, Segment

from backend import preload_models
from backend.processing import final_diarization as final
from backend.processing import nemotron_model, pipeline_metrics
from backend.tests.test_final_diarization import lane as lane
from backend.tests.test_pipeline_stages import (
    _FakeRecording,
    _FakeSession,
    _FakeTranscript,
    _install,
    _install_happy_path_modules,
    _run_task,
)
from backend.worker.tasks import pipeline, stage_vram

pytestmark = pytest.mark.usefixtures("stub_celery_dispatch")


@pytest.mark.parametrize(
    "failure",
    [
        SoftTimeLimitExceeded(),
        TimeLimitExceeded(),
        CancelledError(),
        asyncio.CancelledError(),
    ],
)
def test_interrupted_native_stage_records_real_status_and_releases(
    lane: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    lane.failure = failure
    recording = _FakeRecording(42)
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        pipeline_metrics,
        "record_pipeline_metric",
        lambda **kwargs: events.append(kwargs),
    )
    ctx = SimpleNamespace(
        task=SimpleNamespace(update_state=lambda **kwargs: None),
        session=_FakeSession(recording, _FakeTranscript(42)),
        merged_config={"processing_device": "cuda:1"},
        recording_id=42,
        device_suffix="",
    )
    with pytest.raises(type(failure)) as raised:
        pipeline._run_final_diarization_stage(ctx, recording, "meeting.wav")
    assert raised.value is failure
    assert lane.calls[-2:] == ["infer", "clear"]
    assert len(events) == 1
    assert events[0]["status"] == "error"
    assert events[0]["payload"]["speaker_pass"] == {
        "route": "interrupted",
        "reason": type(failure).__name__,
    }
    assert events[0]["payload"]["error"].strip()


def test_native_status_is_visible_before_inference(
    lane: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    status: dict[str, Any] = {}

    def load(*, device: str) -> Any:
        assert status == {"route": "native", "reason": "running"}
        return SimpleNamespace(infer_offline=lambda audio: lane.probabilities)

    monkeypatch.setattr(nemotron_model, "load_nemotron_model", load)
    final.diarize_final_audio("meeting.wav", status=status)
    assert status["route"] == "union"


def test_stage_records_route_and_keeps_phantom_filter_after_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = _FakeRecording(42)
    recording.max_speakers = 0
    session = _FakeSession(recording, _FakeTranscript(42))
    community = Annotation()
    community[Segment(0, 1)] = "N:L1"
    observed: list[Any] = []

    def wrap(
        path: str,
        config: dict,
        max_speakers: int | None,
        *,
        status: dict,
        recording_id: int | None,
    ) -> Annotation:
        observed.append(max_speakers)
        status.update(route="union", reason="channel_7_inactive")
        return community

    def phantom(ctx: Any, annotation: Annotation, path: str) -> Annotation:
        observed.append(annotation)
        return annotation

    monkeypatch.setattr(final, "diarize_final_audio", wrap)
    monkeypatch.setattr(pipeline, "run_phantom_filter", phantom)
    monkeypatch.setattr(
        pipeline, "record_pipeline_metric", lambda **kwargs: observed.append(kwargs)
    )
    from contextlib import contextmanager

    @contextmanager
    def timer(**kwargs: Any) -> Any:
        context = {"payload": kwargs["payload"]}
        yield context
        observed.append(context["payload"])

    monkeypatch.setattr(pipeline, "pipeline_metric_timer", timer)
    ctx = SimpleNamespace(
        task=SimpleNamespace(update_state=lambda **kw: None),
        session=session,
        merged_config={},
        recording_id=42,
        device_suffix="",
    )
    assert pipeline._run_final_diarization_stage(ctx, recording, "a.wav") is community
    assert observed[0] == 0
    assert observed[1]["speaker_pass"] == {
        "route": "union",
        "reason": "channel_7_inactive",
    }
    assert observed[2] is community


@pytest.mark.parametrize("failure", [SoftTimeLimitExceeded(), CancelledError()])
def test_native_limit_crosses_outer_task_catch(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    recording = _FakeRecording(44)
    session = _FakeSession(recording, _FakeTranscript(44))
    _install_happy_path_modules(monkeypatch)

    def interrupt(*args: Any, **kwargs: Any) -> Any:
        raise failure

    monkeypatch.setattr(final, "diarize_final_audio", interrupt)
    with pytest.raises(type(failure)):
        _run_task(monkeypatch, session, recording_id=44)


@pytest.mark.parametrize("hook", ["worker", "preparation", "pipeline", "stage"])
def test_global_hooks_attempt_native_before_other_release_failure(
    monkeypatch: pytest.MonkeyPatch, hook: str
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        nemotron_model, "release_nemotron_model_cache", lambda: seen.append("native")
    )
    monkeypatch.setattr(final.gc, "collect", lambda: 0)

    def fail() -> None:
        raise RuntimeError("older cache failed")

    _install(monkeypatch, "backend.processing.transcribe", release_model_cache=fail)
    _install(monkeypatch, "backend.processing.diarize", release_pipeline_cache=fail)
    worker = importlib.import_module("backend.celery_app")
    monkeypatch.setattr(worker, "_should_release_model_caches", lambda: True)
    providers = importlib.import_module("backend.processing.onnx_providers")
    monkeypatch.setattr(providers, "gpu_is_present", lambda: True)
    hooks = {
        "worker": worker.release_worker_model_caches,
        "preparation": preload_models._release_validation_caches,
        "pipeline": pipeline._release_pipeline_vram,
        "stage": stage_vram.release_diarization_vram,
    }
    if hook == "preparation":
        with pytest.raises(RuntimeError, match="older cache failed"):
            hooks[hook]()
    else:
        hooks[hook]()
    assert seen == ["native"]


@pytest.mark.parametrize(
    "failure", [SoftTimeLimitExceeded(), TimeLimitExceeded(), CancelledError()]
)
def test_stage_native_release_does_not_swallow_interruptions(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    providers = importlib.import_module("backend.processing.onnx_providers")
    monkeypatch.setattr(providers, "gpu_is_present", lambda: True)

    def interrupt() -> None:
        raise failure

    monkeypatch.setattr(final, "release_final_native", interrupt)
    with pytest.raises(type(failure)):
        stage_vram.release_diarization_vram()
