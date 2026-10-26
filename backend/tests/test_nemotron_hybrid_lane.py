"""Capture selection, durable publication and canonical live hybrid integration."""

from __future__ import annotations

import gc
import shutil
import sys
import weakref
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session

from backend.processing.live_diarization import (
    hybrid_execution,
    hybrid_runtime,
    native_state,
    step,
)
from backend.processing.live_diarization.files import BANK_FILENAME, STATE_FILENAME
from backend.processing.live_diarization.hybrid_state import load_hybrid
from backend.processing.nemotron_types import NativeWindowResult
from backend.tests.live_label_fakes import SAMPLE_RATE, SpeakerTrackModel, speaker_audio
from backend.tests.nemotron_fakes import ObservedRuntime, cache_with_token
from backend.tests.test_live_speaker_labels_db import (
    RECORDING_ID,
    _by_id,
    _database,
    _patch_step,
    _run,
)
from backend.utils.canonical_pipeline import (
    get_canonical_transcript_revision,
    serialize_canonical_delta,
    serialize_canonical_utterances,
    update_utterance_speaker,
    update_utterance_text,
)
from backend.utils.config_manager import config_manager


class LaneRuntime(ObservedRuntime):
    asset_fingerprint = "lane-assets"
    runtime_fingerprint = "lane-runtime"

    def infer_window(
        self, audio_window: np.ndarray, **kwargs: Any
    ) -> NativeWindowResult:
        result = super().infer_window(audio_window, **kwargs)
        probabilities = np.full_like(result.probabilities, 0.01)
        probabilities[:, 2] = 0.9
        return NativeWindowResult(probabilities, result.speaker_cache)


@pytest.fixture
def numpy_codec(monkeypatch: pytest.MonkeyPatch) -> None:
    original = native_state.restore_stream

    def restore(meta: dict[str, Any], arrays: dict[str, np.ndarray]) -> Any:
        return original(
            meta, arrays, cache_factory=lambda geometry: cache_with_token(0)
        )

    monkeypatch.setattr(native_state, "restore_stream", restore)


@pytest.fixture
def lane_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    engine = _database()
    _patch_step(monkeypatch, engine, SpeakerTrackModel())
    monkeypatch.setattr(step, "live_speaker_labels_device", lambda: "cuda")
    monkeypatch.setitem(config_manager.config, "speaker_engine", "auto")
    monkeypatch.setitem(config_manager.config, "live_speaker_labels", "auto")
    yield engine
    engine.dispose()


def _ready(monkeypatch: pytest.MonkeyPatch, runtime: LaneRuntime) -> None:
    from backend.processing import nemotron_model
    from backend.utils import nemotron_model_utils as assets

    monkeypatch.setattr(
        assets, "inspect_nemotron_model", lambda: SimpleNamespace(state="verified")
    )
    monkeypatch.setattr(nemotron_model, "load_nemotron_model", lambda **kwargs: runtime)


def _rows(engine: Engine) -> dict[str, Any]:
    with Session(engine) as session:
        return _by_id(serialize_canonical_utterances(session, RECORDING_ID))


def test_missing_assets_prepare_once_and_readiness_only_changes_a_new_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane_db: Engine,
    numpy_codec: None,
    stub_celery_dispatch: list[Any],
) -> None:
    from backend.services import model_preparation as preparation
    from backend.utils import nemotron_model_utils as assets

    runtime = LaneRuntime()
    _ready(monkeypatch, runtime)
    readiness = {"state": "missing"}
    inspect = lambda: SimpleNamespace(state=readiness["state"])
    monkeypatch.setattr(assets, "inspect_nemotron_model", inspect)
    monkeypatch.setattr(preparation, "inspect_nemotron_model", inspect)

    @contextmanager
    def available_lock(**kwargs: Any) -> Iterator[bool]:
        yield True

    monkeypatch.setattr(preparation, "model_lock", available_lock)
    monkeypatch.setattr(preparation, "pending_task", lambda: None)
    monkeypatch.setattr(preparation, "write_pending", lambda task_id: None)
    monkeypatch.setattr(
        preparation.celery_app, "connection_for_write", lambda **kwargs: nullcontext()
    )
    audio = speaker_audio([(0, 12, 1)], 12)
    _run(tmp_path, audio[: 4 * SAMPLE_RATE], 0.0, 0.0)
    _run(tmp_path, audio[4 * SAMPLE_RATE : 8 * SAMPLE_RATE], 4.0, 0.0)
    readiness["state"] = "verified"
    _run(tmp_path, audio[8 * SAMPLE_RATE :], 8.0, 0.0)
    saved = load_hybrid(tmp_path, step.CONFIG, recording_id=RECORDING_ID)
    assert saved is not None and saved.reason == "model_missing"
    assert saved.route == "clustering" and runtime.calls == []
    assert len(stub_celery_dispatch) == 1
    assert stub_celery_dispatch[0][2] == {"nemotron_only": True, "include_core": False}
    next_capture = tmp_path / "next"
    next_capture.mkdir()
    # A new recording has its own DB history as well as its own directory.
    next_database = _database()
    try:
        _patch_step(monkeypatch, next_database, SpeakerTrackModel())
        monkeypatch.setattr(step, "live_speaker_labels_device", lambda: "cuda")
        _run(next_capture, audio[: 4 * SAMPLE_RATE], 0.0, 0.0)
        saved = load_hybrid(next_capture, step.CONFIG, recording_id=RECORDING_ID)
        assert saved is not None and saved.route == "nemotron"
    finally:
        next_database.dispose()
    assert runtime.calls
    assert len(stub_celery_dispatch) == 1


@pytest.mark.parametrize("selection", ["auto", "classic"])
def test_selection_is_frozen_through_disk_reload_and_changes_for_new_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane_db: Engine,
    numpy_codec: None,
    selection: str,
) -> None:
    runtime = LaneRuntime()
    _ready(monkeypatch, runtime)
    monkeypatch.setitem(config_manager.config, "speaker_engine", selection)
    audio = speaker_audio([(0, 12, 1)], 12)
    _run(tmp_path, audio[: 8 * SAMPLE_RATE], 0.0, 0.0)
    replacement = "classic" if selection == "auto" else "auto"
    monkeypatch.setitem(config_manager.config, "speaker_engine", replacement)
    _run(tmp_path, audio[8 * SAMPLE_RATE :], 8.0, 0.0)
    saved = load_hybrid(tmp_path, step.CONFIG, recording_id=RECORDING_ID)
    assert saved is not None and saved.speaker_engine == selection
    expected = "nemotron" if selection == "auto" else "clustering"
    assert saved.route == expected
    assert _rows(lane_db)["line-a"]["speaker"] == (
        "LIVE_PROVISIONAL_3" if selection == "auto" else "LIVE_PROVISIONAL_1"
    )
    next_capture = tmp_path / "next"
    next_capture.mkdir()
    next_state = hybrid_runtime.start_capture(
        next_capture, step.CONFIG, device="cuda", recording_id=RECORDING_ID
    )
    assert next_state.speaker_engine == replacement
    assert next_state.route != expected


@pytest.mark.parametrize("gate", ["classic", "off", "cpu"])
def test_classic_off_and_cpu_do_not_import_or_prepare_native(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane_db: Engine,
    stub_celery_dispatch: list[Any],
    gate: str,
) -> None:
    import torch

    if gate == "classic":
        monkeypatch.setitem(config_manager.config, "speaker_engine", "classic")
    else:
        monkeypatch.setattr(step, "live_speaker_labels_device", _REAL_DEVICE)
        monkeypatch.setitem(config_manager.config, "live_speaker_labels", gate)
        monkeypatch.setitem(config_manager.config, "speaker_engine", "auto")
        monkeypatch.setattr(torch.cuda, "is_available", lambda: gate == "off")
        if gate == "cpu":
            monkeypatch.setitem(config_manager.config, "live_speaker_labels", "auto")
    monkeypatch.setitem(sys.modules, "backend.processing.nemotron_model", None)
    monkeypatch.setitem(sys.modules, "backend.services.model_preparation", None)
    _run(tmp_path, speaker_audio([(0, 12, 1)], 12), 0.0, 0.0)
    assert stub_celery_dispatch == []
    if gate == "classic":
        saved = load_hybrid(tmp_path, step.CONFIG, recording_id=RECORDING_ID)
        assert saved is not None and saved.route == "clustering"
        assert saved.reason == "configured"
    else:
        assert list(tmp_path.iterdir()) == []
        assert all(line["speaker"] == "UNKNOWN" for line in _rows(lane_db).values())


_REAL_DEVICE = step.live_speaker_labels_device


@pytest.mark.parametrize("failure_at", [1, 2])
def test_prepublication_snapshot_failure_never_writes_labels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane_db: Engine,
    failure_at: int,
) -> None:
    runtime = LaneRuntime()
    _ready(monkeypatch, runtime)
    save = step.save_hybrid
    calls = 0

    def failed_save(path: Path, engine: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_at:
            raise OSError("injected snapshot failure")
        save(path, engine)

    monkeypatch.setattr(step, "save_hybrid", failed_save)
    before = _rows(lane_db)
    with pytest.raises(OSError, match="snapshot failure"):
        _run(tmp_path, speaker_audio([(0, 12, 1)], 12), 0.0, 0.0)
    assert _rows(lane_db) == before


def test_failed_db_write_replays_consumed_decisions_without_duplicate_forwards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane_db: Engine,
    numpy_codec: None,
) -> None:
    runtime = LaneRuntime()
    _ready(monkeypatch, runtime)
    write = step._write_labels

    def fail_write(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("injected database failure")

    monkeypatch.setattr(step, "_write_labels", fail_write)
    audio = speaker_audio([(0, 12, 1)], 12)
    with pytest.raises(RuntimeError, match="database failure"):
        _run(tmp_path, audio, 0.0, 0.0)
    calls = runtime.calls.copy()
    saved = load_hybrid(tmp_path, step.CONFIG, recording_id=RECORDING_ID)
    assert saved is not None and saved.consumed_n == len(audio)
    assert all(line["speaker"] == "UNKNOWN" for line in _rows(lane_db).values())
    monkeypatch.setattr(step, "_write_labels", write)
    _run(tmp_path, audio, 0.0, 0.0)
    assert runtime.calls == calls
    line = _rows(lane_db)["line-a"]
    assert line["speaker"] == "LIVE_PROVISIONAL_3"
    assert line["speaker_state"] == "stable"


def test_canonical_hybrid_preserves_names_text_locks_and_publishes_revisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lane_db: Engine
) -> None:
    class QuietTailRuntime(LaneRuntime):
        def infer_window(
            self, audio_window: np.ndarray, **kwargs: Any
        ) -> NativeWindowResult:
            result = super().infer_window(audio_window, **kwargs)
            first_frame = (len(self.calls) - 1) * 72
            result.probabilities[
                np.arange(len(result.probabilities)) + first_frame >= 1000
            ] = 0
            return result

    runtime = QuietTailRuntime()
    _ready(monkeypatch, runtime)
    engine = hybrid_runtime.start_capture(
        tmp_path, step.CONFIG, device="cuda", recording_id=RECORDING_ID
    )
    audio = speaker_audio([(0, 10, 1), (10, 32, 2)], 32)
    model = SpeakerTrackModel()
    engine.feed(audio[: 12 * SAMPLE_RATE], 0, model)
    engine.fallback("audio_gap", 12 * SAMPLE_RATE)
    assert 10 < engine.native_complete_frame / 100 < 14
    engine.feed(audio[12 * SAMPLE_RATE :], 12 * SAMPLE_RATE, model)
    with Session(lane_db) as session:
        update_utterance_speaker(
            session,
            recording_id=RECORDING_ID,
            utterance_public_id="line-c",
            new_speaker_name="Cy",
        )
        update_utterance_text(
            session,
            recording_id=RECORDING_ID,
            utterance_public_id="line-b",
            text="corrected reply",
        )
        session.commit()
        before = get_canonical_transcript_revision(session, RECORDING_ID)
    named = _rows(lane_db)["line-c"]
    first = step._write_labels(RECORDING_ID, engine)
    assert first is not None and first.changed == 2
    rows = _rows(lane_db)
    assert rows["line-c"] == named
    assert rows["line-b"]["text"] == "corrected reply"
    assert rows["line-b"]["speaker"] == "LIVE_PROVISIONAL_9"
    assert rows["line-a"]["speaker"] == "LIVE_PROVISIONAL_3"
    assert rows["line-a"]["speaker_state"] == "stable"
    engine.posteriors[:] = 0
    engine.posteriors[:, 7] = 0.9
    step._write_labels(RECORDING_ID, engine)
    assert _rows(lane_db)["line-a"] == rows["line-a"]
    with Session(lane_db) as session:
        _, delta, tombstones = serialize_canonical_delta(
            session, RECORDING_ID, after_revision=before
        )
        event_types = (
            session.execute(
                text(
                    "SELECT event_type FROM transcript_utterance_events WHERE id > :rev"
                ),
                {"rev": before},
            )
            .scalars()
            .all()
        )
    assert {line["id"] for line in delta} == {"line-a", "line-b"}
    assert event_types == ["live_speaker_label", "live_speaker_label"]
    assert tombstones == []


def test_native_oom_releases_runtime_bound_by_stream_and_stays_fallback_on_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane_db: Engine,
    numpy_codec: None,
) -> None:
    import torch

    from backend.processing import nemotron_model
    from backend.utils import nemotron_model_utils as assets

    class FailingRuntime(LaneRuntime):
        def infer_window(
            self, audio_window: np.ndarray, **kwargs: Any
        ) -> NativeWindowResult:
            if len(self.calls) >= 2:
                raise RuntimeError("CUDA out of memory")
            return super().infer_window(audio_window, **kwargs)

    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(nemotron_model, "_RUNTIMES", {"cuda": FailingRuntime()})
    reference = weakref.ref(nemotron_model._RUNTIMES["cuda"])
    monkeypatch.setattr(
        assets, "inspect_nemotron_model", lambda: SimpleNamespace(state="verified")
    )
    monkeypatch.setattr(
        nemotron_model,
        "load_nemotron_model",
        lambda **kwargs: nemotron_model._RUNTIMES["cuda"],
    )
    audio = speaker_audio([(0, 12, 1)], 12)
    _run(tmp_path, audio, 0.0, 0.0)
    gc.collect()
    assert reference() is None
    saved = load_hybrid(tmp_path, step.CONFIG, recording_id=RECORDING_ID)
    assert saved is not None and saved.reason == "oom" and saved.stream is None

    def no_retry(**kwargs: Any) -> None:
        pytest.fail("a failed capture attempted native inference again")

    monkeypatch.setattr(nemotron_model, "load_nemotron_model", no_retry)
    _run(tmp_path, audio, 0.0, 0.0)
    assert _rows(lane_db)["line-a"]["speaker"].startswith("LIVE_PROVISIONAL_")


@pytest.mark.parametrize("failures", [1, 2])
def test_shadow_loader_oom_latches_and_saves_native_release_before_one_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane_db: Engine,
    numpy_codec: None,
    failures: int,
) -> None:
    runtime = LaneRuntime()
    _ready(monkeypatch, runtime)
    loads: list[int] = []
    released: list[bool] = []
    model = SpeakerTrackModel()

    def shadow_loader(device: str, config: Any) -> Any:
        loads.append(1)
        if len(loads) <= failures:
            raise RuntimeError("shadow loader out of memory")
        return model

    monkeypatch.setattr(step, "_load_model", shadow_loader)
    monkeypatch.setattr(
        hybrid_execution, "release_native", lambda: released.append(True)
    )
    audio = speaker_audio([(0, 12, 1)], 12)
    if failures == 2:
        with pytest.raises(RuntimeError, match="shadow loader out of memory"):
            _run(tmp_path, audio, 0.0, 0.0)
    else:
        _run(tmp_path, audio, 0.0, 0.0)
        assert _rows(lane_db)["line-a"]["speaker"] == "LIVE_PROVISIONAL_9"
    assert len(loads) == 2
    assert released == [True]
    saved = load_hybrid(tmp_path, step.CONFIG, recording_id=RECORDING_ID)
    assert saved is not None and saved.reason == "oom" and saved.stream is None
    assert runtime.calls == []
    # A second loader failure still leaves permanent fallback on disk.
    _run(tmp_path, audio, 0.0, 0.0)
    assert _rows(lane_db)["line-a"]["speaker"] == "LIVE_PROVISIONAL_9"
    assert released == [True]


@pytest.mark.parametrize("saturate", [False, True])
def test_shadow_forward_oom_restores_before_replaying_a_partly_consumed_backlog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane_db: Engine,
    numpy_codec: None,
    saturate: bool,
) -> None:
    class BacklogRuntime(LaneRuntime):
        def infer_window(
            self, audio_window: np.ndarray, **kwargs: Any
        ) -> NativeWindowResult:
            result = super().infer_window(audio_window, **kwargs)
            if saturate and len(self.calls) > 3:
                result.probabilities[:] = 0.8
            return result

    runtime = BacklogRuntime()
    _ready(monkeypatch, runtime)
    audio = speaker_audio([(0, 5, 1), (5, 14, 2)], 14)
    _run(tmp_path, audio[: 4 * SAMPLE_RATE], 0.0, 0.0)
    reference_dir = tmp_path / "reference"
    reference_dir.mkdir()
    for filename in (STATE_FILENAME, BANK_FILENAME):
        if (tmp_path / filename).exists():
            shutil.copy2(tmp_path / filename, reference_dir / filename)
    reference = load_hybrid(reference_dir, step.CONFIG, recording_id=RECORDING_ID)
    assert reference is not None
    prior_native_end = reference.native_complete_frame
    reference.fallback("oom", reference.consumed_n)
    reference.feed(audio[4 * SAMPLE_RATE :], 4 * SAMPLE_RATE, SpeakerTrackModel())
    model = SpeakerTrackModel()
    shadow_calls = 0
    failed_after_native_calls: list[int] = []
    released: list[bool] = []

    def failing_shadow(windows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        nonlocal shadow_calls
        shadow_calls += 1
        result = model(windows)
        if shadow_calls == 2:
            # _windows has already advanced shadow audio/step cursors; the first
            # packet of this backlog also completed native forwards.
            failed_after_native_calls.append(len(runtime.calls))
            raise RuntimeError("shadow forward out of memory")
        return result

    monkeypatch.setattr(step, "_load_model", lambda device, config: failing_shadow)
    monkeypatch.setattr(
        hybrid_execution, "release_native", lambda: released.append(True)
    )
    prior_calls = len(runtime.calls)
    _run(tmp_path, audio[4 * SAMPLE_RATE :], 4.0, 0.0)
    saved = load_hybrid(tmp_path, step.CONFIG, recording_id=RECORDING_ID)
    assert saved is not None and saved.reason == "oom" and saved.stream is None
    assert saved.native_complete_frame == prior_native_end
    assert saved.consumed_n == len(audio)
    assert saved.shadow.pieces == reference.shadow.pieces
    assert saved.units.label == reference.units.label
    assert saved.bank.rows == reference.bank.rows
    np.testing.assert_array_equal(saved.clustering.active, reference.clustering.active)
    assert failed_after_native_calls == [len(runtime.calls)]
    assert failed_after_native_calls[0] > prior_calls
    assert released == [True]
