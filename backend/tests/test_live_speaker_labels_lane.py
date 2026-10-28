"""The label step inside the live task: where it runs, and what it cannot break."""

from __future__ import annotations

import sys

import backend.processing.live_diarization as live_diarization
from backend.models.recording import RecordingStatus
from backend.processing import live_transcribe as lt
from backend.processing.live_diarization import step
from backend.tests.test_live_transcription import (
    _FakeRecording,
    _FakeSession,
    _FakeTranscript,
    _make_segment_wav,
    _patch_live_deps,
    _run_live_task,
)
from backend.utils.config_manager import config_manager


def _lane(monkeypatch, tmp_path):
    temp_dir = tmp_path / "42"
    temp_dir.mkdir()
    monkeypatch.setattr(
        lt, "recording_upload_temp_dir", lambda rid, create=False: temp_dir
    )
    audio_store = _patch_live_deps(
        monkeypatch, speech_map=lambda audio: [{"start": 0.0, "end": 1.0}]
    )
    transcript = _FakeTranscript()
    session = _FakeSession(_FakeRecording(RecordingStatus.UPLOADING, transcript))
    monkeypatch.setitem(config_manager.config, "live_speaker_labels", "auto")
    # The lane's minimal DB fake has no utterance-event query interface.
    monkeypatch.setattr(step, "has_label_history", lambda recording_id: False)
    return temp_dir, audio_store, transcript, session


def test_a_failing_label_step_leaves_the_runs_text_and_lane_state_intact(
    monkeypatch, tmp_path
):
    temp_dir, audio_store, transcript, session = _lane(monkeypatch, tmp_path)
    monkeypatch.setattr(step, "live_speaker_labels_device", lambda: "cpu")
    loads = []

    def exploding_model(device, config):
        loads.append(device)
        raise RuntimeError("label model exploded")

    monkeypatch.setattr(step, "_load_model", exploding_model)

    for sequence in (0, 1):
        _make_segment_wav(temp_dir, sequence, 2.0, audio_store)
        _run_live_task(monkeypatch, 42, sequence, session)

    state = lt.read_live_state(temp_dir / "live")
    assert loads == ["cpu", "cpu"]
    assert [segment["text"] for segment in transcript.segments] == ["hello", "hello"]
    assert state["next_expected"] == 2
    assert {o["outcome"] for o in state["sequence_outcomes"].values()} == {"consumed"}


def test_the_label_step_runs_after_the_text_and_lane_state_are_saved(
    monkeypatch, tmp_path
):
    temp_dir, audio_store, transcript, session = _lane(monkeypatch, tmp_path)
    seen = []

    def record(run):
        saved = lt.read_live_state(run.live_dir)
        seen.append(
            (
                len(transcript.segments),
                saved["next_expected"],
                run.audio_start_s,
                run.next_text_start_s,
                int(run.audio.numel()),
            )
        )

    monkeypatch.setattr(step, "run_live_speaker_labels", record)
    for sequence in (0, 1):
        _make_segment_wav(temp_dir, sequence, 2.0, audio_store)
        _run_live_task(monkeypatch, 42, sequence, session)

    # Each run hands over the carried second plus its new chunk, on the
    # recording's timeline, once its own line and lane state are saved.
    assert seen == [(1, 1, 0.0, 1.0, 32000), (2, 2, 1.0, 2.0, 48000)]


def test_with_labels_off_no_model_loads_and_no_label_state_is_written(
    monkeypatch, tmp_path
):
    temp_dir, audio_store, transcript, session = _lane(monkeypatch, tmp_path)
    monkeypatch.setitem(config_manager.config, "live_speaker_labels", "off")
    loads = []
    monkeypatch.setattr(
        step, "_load_model", lambda device, config: loads.append(device)
    )

    _make_segment_wav(temp_dir, 0, 2.0, audio_store)
    _run_live_task(monkeypatch, 42, 0, session)

    assert loads == []
    assert sorted(p.name for p in (temp_dir / "live").iterdir()) == [
        "buffer.wav",
        "context.wav",
        "state.json",
    ]
    assert [segment["speaker"] for segment in transcript.segments] == ["UNKNOWN"]


def test_with_labels_off_the_live_task_never_imports_the_label_code(
    monkeypatch, tmp_path
):
    # The step module pulls in the engine and SciPy. An explicit opt-out must
    # keep live runs from importing it at all.
    temp_dir, audio_store, transcript, session = _lane(monkeypatch, tmp_path)
    monkeypatch.setitem(config_manager.config, "live_speaker_labels", "off")
    monkeypatch.delattr(live_diarization, "step", raising=False)
    monkeypatch.setitem(sys.modules, "backend.processing.live_diarization.step", None)
    failures = []
    monkeypatch.setattr(lt.logger, "exception", lambda *args: failures.append(args))

    _make_segment_wav(temp_dir, 0, 2.0, audio_store)
    _run_live_task(monkeypatch, 42, 0, session)

    assert failures == []
    assert [segment["text"] for segment in transcript.segments] == ["hello"]
