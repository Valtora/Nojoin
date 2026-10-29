"""Concurrent workers must not rewind live audio or strand a contending upload."""

from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import soundfile as sf
from celery.canvas import Signature
from celery.exceptions import Retry

from backend.core import db
from backend.models.recording import RecordingStatus
from backend.processing import live_transcribe, vad
from backend.processing.live_drain_lock import recording_live_drain_lock
from backend.processing.live_persistence import LivePublicationResult
from backend.processing.live_settling import SettlingStepResult
from backend.tests.test_live_resume_lane import (
    ResumeLane,
    _read_audio,
    _save_audio,
)
from backend.tests.test_live_resume_lane import (
    resume_lane as resume_lane,
)
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)


def _files(path: Path) -> dict[str, bytes]:
    return {
        item.name: item.read_bytes()
        for item in (path / "live").iterdir()
        if item.suffix in {".wav", ".json"}
    }


def _retry(**kwargs: Any) -> None:
    assert kwargs["countdown"] > 0
    raise Retry("live drain busy")


def _upload(path: Path, sequence: int, duration: float = 2) -> None:
    sf.write(
        path / f"{sequence}.wav", np.ones((round(duration * 16000), 2)) * 0.1, 16000
    )


def _patch_lane(
    monkeypatch: pytest.MonkeyPatch, path: Path, mode: str, *, fake_retry: bool = True
) -> None:
    recording = SimpleNamespace(
        status=RecordingStatus.UPLOADING,
        capture_epoch=0,
        transcript=SimpleNamespace(settling_state=None),
    )
    session = SimpleNamespace(get=lambda *_a: recording, close=lambda: None)
    monkeypatch.setattr(db, "get_sync_session", lambda: session)
    monkeypatch.setattr(
        live_transcribe, "recording_upload_temp_dir", lambda *_a, **_k: path
    )
    monkeypatch.setattr(live_transcribe, "record_pipeline_metric", lambda **_k: None)
    monkeypatch.setattr(
        live_transcribe, "_resolve_live_engine_config", lambda *_a, **_k: {}
    )
    monkeypatch.setattr(live_transcribe, "live_speaker_labels_requested", lambda: False)
    monkeypatch.setattr(live_transcribe, "_transcribe_live_regions", lambda **_k: [])
    monkeypatch.setattr(
        live_transcribe, "_persist_live_run", lambda **_k: LivePublicationResult(True)
    )
    monkeypatch.setattr(
        live_transcribe,
        "run_settling_step",
        lambda *_a: SettlingStepResult(mode == "on"),
    )
    monkeypatch.setattr(vad, "safe_read_audio", _read_audio)
    monkeypatch.setattr(vad.silero_vad, "save_audio", _save_audio)
    monkeypatch.setattr(
        vad, "detect_speech_segments", lambda *_a, **_k: [{"start": 0.1, "end": 1.0}]
    )
    if fake_retry:
        monkeypatch.setattr(
            live_transcribe.transcribe_segment_live_task, "retry", _retry
        )


def _hold_boundary(
    monkeypatch: pytest.MonkeyPatch, boundary: str, entered: Event, release: Event
) -> None:
    name = (
        "_build_live_combined_buffer" if boundary == "snapshot" else "write_live_state"
    )
    original = getattr(live_transcribe, name)
    armed = True

    def hold(*args: Any, **kwargs: Any) -> Any:
        nonlocal armed
        result = original(*args, **kwargs) if boundary == "snapshot" else None
        if armed:
            armed = False
            entered.set()
            assert release.wait(10), "test did not release the first drain"
        return result if boundary == "snapshot" else original(*args, **kwargs)

    monkeypatch.setattr(live_transcribe, name, hold)


def _probe_child_lock(recording_dir: str, result: Any) -> None:
    with recording_live_drain_lock(Path(recording_dir)) as acquired:
        result.put(acquired)


def _child_lock_acquired(recording_dir: Path) -> bool:
    context = get_context("spawn")
    result = context.Queue()
    child = context.Process(target=_probe_child_lock, args=(str(recording_dir), result))
    child.start()
    try:
        acquired = result.get(timeout=15)
        child.join(timeout=15)
        assert child.exitcode == 0
        return bool(acquired)
    finally:
        if child.is_alive():
            child.terminate()
            child.join(timeout=5)
        child.close()
        result.close()
        result.join_thread()


def test_drain_lock_excludes_another_process_but_not_another_recording(
    tmp_path: Path,
) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    with recording_live_drain_lock(first) as acquired:
        assert acquired
        assert not _child_lock_acquired(first)
        assert _child_lock_acquired(second)
    assert _child_lock_acquired(first)


def test_celery_schedules_contention_retry_after_more_than_three_attempts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exercise Celery's real retry limit and signature, intercepting only broker I/O."""
    _patch_lane(monkeypatch, tmp_path, "off", fake_retry=False)
    scheduled: list[Signature] = []

    def send(signature: Signature, **_kwargs: Any) -> None:
        scheduled.append(signature)

    monkeypatch.setattr(Signature, "apply_async", send)
    task = live_transcribe.transcribe_segment_live_task
    task.push_request(
        id="contending-live-drain",
        args=(42, 3),
        kwargs={},
        retries=8,
        called_directly=False,
        is_eager=False,
        delivery_info={"exchange": "", "routing_key": "gpu"},
    )
    try:
        with recording_live_drain_lock(tmp_path):
            with pytest.raises(Retry):
                task.run(42, 3)
    finally:
        task.pop_request()
    assert len(scheduled) == 1
    assert scheduled[0].args == (42, 3)
    assert scheduled[0].options["retries"] == 9
    assert scheduled[0].options["countdown"] == 1
    assert scheduled[0].options["queue"] == "gpu"


def test_missing_upload_directory_cannot_become_an_unlocked_drain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "capture"
    _patch_lane(monkeypatch, path, "off")

    def late_upload(**metric: Any) -> None:
        if metric["stage"] == "live_task_started" and not path.exists():
            path.mkdir()
            _upload(path, 0)

    monkeypatch.setattr(live_transcribe, "record_pipeline_metric", late_upload)
    task = live_transcribe.transcribe_segment_live_task
    task.run(42, 0)
    assert (path / "0.wav").exists()
    assert not (path / "live").exists()
    task.run(42, 0)
    assert live_transcribe.read_live_state(path / "live")["next_expected"] == 1


@pytest.mark.parametrize("mode", ["on", "off"])
@pytest.mark.parametrize("outcome", ["stale", "terminal", "failure"])
def test_drain_releases_lock_after_rejection_early_return_and_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str, outcome: str
) -> None:
    from backend.processing import live_run_failure

    _patch_lane(monkeypatch, tmp_path, mode)
    task = live_transcribe.transcribe_segment_live_task
    _upload(tmp_path, 0)
    task.run(42, 0)
    _upload(tmp_path, 1)
    before = _files(tmp_path)
    if outcome == "stale":
        monkeypatch.setattr(
            live_transcribe,
            "_persist_live_run",
            lambda **_k: LivePublicationResult(False),
        )
    elif outcome == "terminal":
        db.get_sync_session().get(None, 42).status = RecordingStatus.PROCESSING
    else:

        def fail(**_kwargs: Any) -> None:
            raise ValueError("failed while reading live audio")

        monkeypatch.setattr(live_transcribe, "_build_live_combined_buffer", fail)
        monkeypatch.setattr(
            live_run_failure, "record_pipeline_metric", lambda **_k: None
        )
    task.run(42, 1)
    with recording_live_drain_lock(tmp_path) as acquired:
        assert acquired
    if outcome != "failure":
        assert _files(tmp_path) == before
    else:
        state = live_transcribe.read_live_state(tmp_path / "live")
        assert state["next_expected"] == 2
        assert state["sequence_outcomes"]["1"]["outcome"] == "failed"


@pytest.mark.parametrize("mode", ["on", "off"])
@pytest.mark.parametrize("boundary", ["snapshot", "checkpoint"])
def test_contending_upload_retains_audio_until_retry_without_another_upload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str, boundary: str
) -> None:
    _patch_lane(monkeypatch, tmp_path, mode)
    task = live_transcribe.transcribe_segment_live_task
    _upload(tmp_path, 0)
    task.run(42, 0)
    _upload(tmp_path, 1)
    _upload(tmp_path, 2, 0.4)
    entered, release = Event(), Event()
    _hold_boundary(monkeypatch, boundary, entered, release)
    with ThreadPoolExecutor(max_workers=1) as workers:
        holder = workers.submit(task.run, 42, 1)
        try:
            assert entered.wait(10), "first drain never reached the race boundary"
            before = _files(tmp_path)
            _upload(tmp_path, 3)
            with pytest.raises(Retry, match="live drain busy"):
                task.run(42, 3)
            assert _files(tmp_path) == before
            assert (tmp_path / "3.wav").exists()
        finally:
            release.set()
        holder.result(timeout=10)
    task.run(42, 3)  # Deliver the scheduled retry, without a new upload.
    state = live_transcribe.read_live_state(tmp_path / "live")
    assert state["next_expected"] == 4
    assert all(
        state["sequence_outcomes"][str(n)]["outcome"] == "consumed" for n in range(4)
    )
    assert state["buffer_abs_start"] == pytest.approx(6.4 if mode == "on" else 3.0)


@pytest.mark.parametrize("resume_lane", [("on", False), ("off", False)], indirect=True)
def test_postgres_newer_task_cannot_rewind_inflight_checkpoint(
    resume_lane: ResumeLane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The audit's overtaking task must defer, then publish from a fresh checkpoint."""
    lane = resume_lane
    lane.upload(1)
    lane.upload(2, 0.4)
    monkeypatch.setattr(live_transcribe.transcribe_segment_live_task, "retry", _retry)

    def overtake() -> None:
        before = _files(lane.path)
        lane.upload(3)
        with pytest.raises(Retry, match="live drain busy"):
            lane.run(3)
        assert _files(lane.path) == before

    lane.boundary, lane.hook = "snapshot", overtake
    lane.run(1)
    assert lane.hook is None
    assert live_transcribe.read_live_state(lane.path / "live")["next_expected"] == 3
    lane.run(3)
    final = live_transcribe.read_live_state(lane.path / "live")
    assert final["next_expected"] == 4
    assert final["buffer_abs_start"] == pytest.approx(6.4 if lane.mode == "on" else 3.0)
    rows = lane.rows()
    spans = [(row[1], row[2], row[3]) for row in rows]
    assert len(spans) == len(set(spans))
    assert all(start < end for _text, start, end in spans)
