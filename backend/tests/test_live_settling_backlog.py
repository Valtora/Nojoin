"""Backlog speech gets one decode while legacy capture keeps upstream wording."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import soundfile as sf
import torch
from sqlalchemy import Engine
from sqlmodel import Session, select

from backend.models.recording import RecordingStatus
from backend.models.transcript import Transcript
from backend.processing import forced_aligner_model, live_transcribe, transcribe, vad
from backend.processing import live_settling as worker
from backend.processing.live_diarization import step as labels_worker
from backend.processing.live_persistence import LivePublicationResult
from backend.tests.test_live_settling_publication import _active
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)
from backend.tests.test_live_settling_worker_postgres import worker_db as worker_db
from backend.tests.test_live_transcription import (
    _FakeRecording,
    _FakeSession,
    _make_segment_wav,
    _patch_live_deps,
    _run_live_task,
)
from backend.utils.canonical_pipeline.live_settling_projection import row_words
from backend.utils.config_manager import config_manager
from backend.utils.transcript_settling import (
    new_capture_settling_state,
    resolve_capture_settling_policy,
    update_capture_settling_state,
)
from backend.utils.word_text import reconstruct_text_from_words


@pytest.mark.parametrize("settling", [False, True])
@pytest.mark.parametrize("labels", [False, True])
@pytest.mark.parametrize("speech_source", ["vad", "diariser", "delayed"])
def test_backlog_speech_survives_silent_newest_chunk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settling: bool,
    labels: bool,
    speech_source: str,
) -> None:
    """Drain four chunks, including speech followed by six seconds of silence."""
    # Legacy capture uses VAD; diariser-only gates belong to the rolling lane.
    vad = speech_source == "vad" or not settling
    store = _patch_live_deps(
        monkeypatch,
        speech_map=lambda _: [{"start": 0.2, "end": 1.0}] if vad else [],
    )
    for sequence in range(4):
        path = _make_segment_wav(tmp_path, sequence, 2, store)
        store.save_audio(str(path), torch.ones(32000) * (0.1 if sequence == 0 else 0))
    monkeypatch.setattr(
        live_transcribe, "recording_upload_temp_dir", lambda *_a, **_k: tmp_path
    )
    monkeypatch.setattr(
        live_transcribe, "_resolve_live_engine_config", lambda *_a, **_k: {}
    )
    monkeypatch.setattr(
        live_transcribe, "live_speaker_labels_requested", lambda: labels
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(transcribe, "loaded_transcription_device", lambda _: "cuda")
    spans = [(0.2, 1.0)] if labels and speech_source == "diariser" else []
    monkeypatch.setattr(
        worker,
        "_speaker_evidence",
        lambda _: SimpleNamespace(speech_spans=lambda: spans, decide=None),
    )
    snapshot = worker.EpisodeSnapshot({"enabled": True, "window_s": 10, "k": 3}, [], 7)
    monkeypatch.setattr(
        worker,
        "_episode_snapshot",
        lambda _: snapshot if settling else worker.SettlingStepResult(False),
    )
    decodes: list[tuple[int, int]] = []
    publications: list[worker.EpisodeSnapshot] = []
    label_runs: list[Any] = []

    def decode(step: worker.SettlingStep, _config: Any, _audio: Any, start: int) -> Any:
        decodes.append((step.sequence, start))
        return {"asr_segments": []}, "cuda"

    def publish(_step: Any, snap: Any, _payload: Any, _evidence: Any) -> Any:
        publications.append(snap)
        return worker.SettlingStepResult(True)

    monkeypatch.setattr(worker, "_decode_words", decode)
    monkeypatch.setattr(worker, "_publish_step", publish)
    monkeypatch.setattr(labels_worker, "run_live_speaker_labels", label_runs.append)
    transcript = SimpleNamespace(
        segments=[],
        settling_state={"eligible": True, "policy": snapshot.policy}
        if settling
        else None,
    )
    session = _FakeSession(_FakeRecording(RecordingStatus.UPLOADING, transcript))
    _run_live_task(monkeypatch, 42, 0, session)
    assert live_transcribe.read_live_state(tmp_path / "live")["next_expected"] == 4
    assert len(label_runs) == int(labels)
    if not settling:
        assert not decodes
        assert [segment["text"] for segment in transcript.segments] == ["hello"]
        return
    expected_decode = vad or bool(spans)
    assert decodes == ([(3, 0)] if expected_decode else [])
    chunks = publications[-1].gate_chunks
    assert [chunk["sequence"] for chunk in chunks] == [0, 1, 2, 3]
    assert chunks[0]["consumed"] is expected_decode
    if speech_source == "delayed" and labels:
        # The first drained chunk must remain eligible for later diariser evidence.
        snapshot = publications[-1]
        spans.append((0.2, 1.0))
        path = _make_segment_wav(tmp_path, 4, 2, store)
        store.save_audio(str(path), torch.zeros(32000))
        _run_live_task(monkeypatch, 42, 4, session)
        assert decodes == [(4, 0)]
        assert publications[-1].gate_chunks[0]["consumed"]


@pytest.mark.parametrize("labels", [False, True])
def test_settling_off_preserves_upstream_repetition_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, labels: bool
) -> None:
    """Native tokens need not equal engine text; legacy filtering stays textual."""
    store = _patch_live_deps(monkeypatch, speech_map=lambda _: [{"start": 0, "end": 1}])
    _make_segment_wav(tmp_path, 0, 2, store)
    monkeypatch.setattr(
        live_transcribe, "recording_upload_temp_dir", lambda *_a, **_k: tmp_path
    )
    monkeypatch.setattr(
        live_transcribe, "_resolve_live_engine_config", lambda *_a, **_k: {}
    )
    monkeypatch.setattr(
        live_transcribe, "live_speaker_labels_requested", lambda: labels
    )
    monkeypatch.setattr(labels_worker, "run_live_speaker_labels", lambda _: None)
    monkeypatch.setattr(
        worker, "_episode_snapshot", lambda _: worker.SettlingStepResult(False)
    )
    monkeypatch.setattr(
        transcribe,
        "transcribe_audio",
        lambda *_a, **_k: {
            "segments": [
                {
                    "start": 0,
                    "end": 1,
                    "text": "Yes! Yes! Yes! please",
                    "words": [{"word": "Yes", "start": 0, "end": 0.5}],
                }
            ],
        },
    )
    transcript = SimpleNamespace(segments=[], settling_state=None)
    session = _FakeSession(_FakeRecording(RecordingStatus.UPLOADING, transcript))
    _run_live_task(monkeypatch, 42, 0, session)
    assert transcript.segments[0]["text"] == "Yes! please"
    assert transcript.segments[0]["confidence_payload"]["asr_segments"][0]["words"] == [
        {"word": "Yes", "start_ms": 0, "end_ms": 500}
    ]


def test_drained_backlog_publishes_once_and_spends_history_under_fence(
    worker_db: Any, settling_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One backlog decode is one observation, even when later evidence repeats."""
    for sequence in range(4):
        step = worker_db.step(sequence)
    step = replace(step, first_sequence=0, vad_regions=[{"start": 0.2, "end": 1.0}])
    calls: list[int] = []

    def decode(current: worker.SettlingStep, *_args: Any) -> Any:
        calls.append(current.sequence)
        return {
            "asr_segments": [
                {"words": [{"word": "backlog", "start_ms": 200, "end_ms": 1000}]}
            ]
        }, "cuda"

    monkeypatch.setattr(worker, "_decode_words", decode)
    assert worker.run_settling_step(step, {}).accepted
    assert worker.run_settling_step(step, {}).accepted
    monkeypatch.setattr(
        worker,
        "_speaker_evidence",
        lambda _: SimpleNamespace(
            speech_spans=lambda: [(0.2, 1.0)],
            decide=None,
        ),
    )
    assert worker.run_settling_step(
        replace(worker_db.step(4), vad_regions=[]), {}
    ).accepted
    assert calls == [3]
    with Session(settling_engine) as session:
        transcript = session.exec(select(Transcript)).one()
        chunks = transcript.settling_state["gate_chunks"]
        assert [chunk["sequence"] for chunk in chunks] == [0, 1, 2, 3, 4]
        assert all(chunk["consumed"] for chunk in chunks[:4])
        words = [
            word
            for row in _active(session, worker_db.recording_id)
            for word in row_words(row)
        ]
        assert [word["word"] for word in words] == ["backlog"]
        assert words[0]["unchanged_count"] == 0


@pytest.mark.parametrize("source", ["vad", "diariser"])
@pytest.mark.parametrize(
    "case",
    [(0.2, False, False), (2.2, False, False), (3.2, False, True), (3.2, True, False)],
)
def test_newly_drained_gate_respects_cap_partial_chunk_and_mute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    case: tuple[float, bool, bool],
) -> None:
    """A nine-second cap clips the oldest chunk halfway at three seconds."""
    start, muted, expected = case
    store = _patch_live_deps(monkeypatch, speech_map=lambda _: [])
    for sequence in range(6):
        path = _make_segment_wav(tmp_path, sequence, 2, store)
        store.save_audio(str(path), torch.ones(32000) * (0 if muted else 0.1))
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    snapshot = worker.EpisodeSnapshot({"enabled": True, "window_s": 9, "k": 3}, [], 7)
    monkeypatch.setattr(worker, "_episode_snapshot", lambda _: snapshot)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(transcribe, "loaded_transcription_device", lambda _: "cuda")
    monkeypatch.setattr(
        worker,
        "_speaker_evidence",
        lambda _: SimpleNamespace(
            speech_spans=lambda: [(start, start + 0.4)] if source == "diariser" else [],
        ),
    )
    decodes: list[int] = []
    publications: list[worker.EpisodeSnapshot] = []

    def decode(step: worker.SettlingStep, *_args: Any) -> Any:
        decodes.append(step.sequence)
        return {"asr_segments": []}, "cuda"

    def publish(_step: Any, snap: Any, _payload: Any, _evidence: Any) -> Any:
        publications.append(snap)
        return worker.SettlingStepResult(True)

    monkeypatch.setattr(worker, "_decode_words", decode)
    monkeypatch.setattr(worker, "_publish_step", publish)
    step = worker.SettlingStep(
        42,
        5,
        tmp_path,
        live_dir,
        12000,
        0,
        [{"start": start, "end": start + 0.4}] if source == "vad" else [],
        worker.CapturePublicationToken(0, "episode"),
        first_sequence=0,
    )
    assert worker.run_settling_step(step, {}).accepted
    assert decodes == ([5] if expected else [])
    chunks = publications[-1].gate_chunks
    assert [chunk["sequence"] for chunk in chunks] == [1, 2, 3, 4, 5]
    assert chunks[0]["start_ms"] == 3000


@pytest.mark.parametrize("failure", ["vad", "asr"])
def test_marked_disabled_episode_preserves_upstream_label_failure_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Settling off dispatches labels only after successful legacy ingestion."""
    store = _patch_live_deps(monkeypatch, speech_map=lambda _: [{"start": 0, "end": 1}])
    _make_segment_wav(tmp_path, 0, 2, store)
    monkeypatch.setattr(
        live_transcribe, "recording_upload_temp_dir", lambda *_a, **_k: tmp_path
    )
    monkeypatch.setattr(
        live_transcribe, "_resolve_live_engine_config", lambda *_a, **_k: {}
    )
    monkeypatch.setattr(live_transcribe, "live_speaker_labels_requested", lambda: True)
    monkeypatch.setattr(
        worker, "_episode_snapshot", lambda _: worker.SettlingStepResult(False)
    )
    labels: list[Any] = []
    monkeypatch.setattr(labels_worker, "run_live_speaker_labels", labels.append)

    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("legacy failure")

    if failure == "asr":
        monkeypatch.setattr(live_transcribe, "_transcribe_live_regions", fail)
    else:
        from backend.processing import vad

        monkeypatch.setattr(vad, "detect_speech_segments", fail)
    transcript = SimpleNamespace(
        segments=[],
        settling_state={"eligible": True, "policy": {"enabled": False}},
    )
    session = _FakeSession(_FakeRecording(RecordingStatus.UPLOADING, transcript))
    _run_live_task(monkeypatch, 42, 0, session)
    assert not labels


@pytest.mark.parametrize("labels", [False, True])
@pytest.mark.parametrize("resumed_off", [False, True])
def test_paused_disabled_episode_drains_queued_legacy_speech(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    labels: bool,
    resumed_off: bool,
) -> None:
    """CPU Auto and a resumed Off episode both retain the paused legacy tail."""
    settings = {"live_text_settling": "auto"}
    monkeypatch.setattr(
        config_manager, "get", lambda key, default=None: settings.get(key, default)
    )
    transcript = SimpleNamespace(
        segments=[], settling_state=new_capture_settling_state(capture_epoch=0)
    )
    original_generation = transcript.settling_state["generation"]
    epoch = int(resumed_off)
    if resumed_off:
        update_capture_settling_state(
            transcript, capture_epoch=0, sealed=False, phase="paused"
        )
        settings["live_text_settling"] = "off"
        update_capture_settling_state(
            transcript, capture_epoch=epoch, sealed=False, phase="live"
        )
        assert transcript.settling_state["generation"] != original_generation
    else:
        resolve_capture_settling_policy(transcript, device="cpu")
    assert transcript.settling_state["policy"]["enabled"] is False
    update_capture_settling_state(
        transcript, capture_epoch=epoch, sealed=False, phase="paused"
    )
    store = _patch_live_deps(monkeypatch, speech_map=lambda _: [{"start": 0, "end": 1}])
    _make_segment_wav(tmp_path, 0, 2, store)
    monkeypatch.setattr(
        live_transcribe, "recording_upload_temp_dir", lambda *_a, **_k: tmp_path
    )
    monkeypatch.setattr(
        live_transcribe, "_resolve_live_engine_config", lambda *_a, **_k: {}
    )
    monkeypatch.setattr(
        live_transcribe, "live_speaker_labels_requested", lambda: labels
    )
    label_runs: list[Any] = []
    monkeypatch.setattr(labels_worker, "run_live_speaker_labels", label_runs.append)
    recording = _FakeRecording(RecordingStatus.PAUSED, transcript)
    recording.capture_epoch = epoch
    session = _FakeSession(recording)
    # Reach the real ownership guard; this fake cannot execute canonical SQL.
    monkeypatch.setattr(session, "execute", lambda *_a, **_k: None, raising=False)

    def persist(**kwargs: Any) -> LivePublicationResult:
        transcript.segments.extend(kwargs["new_segments"])
        return LivePublicationResult(True)

    monkeypatch.setattr(live_transcribe, "_persist_live_run", persist)
    _run_live_task(monkeypatch, 42, 0, session)
    assert live_transcribe.read_live_state(tmp_path / "live")["next_expected"] == 1
    assert [segment["text"] for segment in transcript.segments] == ["hello"]
    assert len(label_runs) == int(labels)


@pytest.mark.parametrize("labels", [False, True])
def test_canary_settling_off_repeated_words_agree_after_live_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, labels: bool
) -> None:
    """Keep the real Canary dispatcher and forced text-to-word mapping."""
    dispatcher = transcribe.transcribe_audio
    store = _patch_live_deps(monkeypatch, speech_map=lambda _: [{"start": 0, "end": 1}])
    _make_segment_wav(tmp_path, 0, 2, store)
    monkeypatch.setattr(
        live_transcribe, "recording_upload_temp_dir", lambda *_a, **_k: tmp_path
    )
    monkeypatch.setattr(
        live_transcribe, "_resolve_live_engine_config", lambda *_a, **_k: {}
    )
    live_config = {
        **live_transcribe._build_live_config(),
        "transcription_backend": "canary",
        "processing_device": "cpu",
        "context_window_s": 0,
    }
    monkeypatch.setattr(
        live_transcribe, "_build_live_config", lambda: dict(live_config)
    )
    monkeypatch.setattr(
        live_transcribe, "live_speaker_labels_requested", lambda: labels
    )
    label_runs: list[Any] = []
    monkeypatch.setattr(labels_worker, "run_live_speaker_labels", label_runs.append)
    source = {
        "text": "yes yes yes please",
        "segments": [{"start": 0, "end": 1, "text": "yes yes yes please"}],
    }
    monkeypatch.setattr(
        transcribe,
        "_ENGINE_REGISTRY",
        {
            "canary": SimpleNamespace(
                transcribe=lambda *_a: source, loaded_device=lambda _: "cpu"
            )
        },
    )
    monkeypatch.setattr(transcribe, "transcribe_audio", dispatcher)
    monkeypatch.setattr(
        forced_aligner_model,
        "align_audio",
        lambda *_a, **_k: [
            {"text": token, "start_time": i * 0.2, "end_time": i * 0.2 + 0.15}
            for i, token in enumerate(source["text"].split())
        ],
    )

    def save(path: str, audio: Any, *, sampling_rate: int) -> None:
        sf.write(path, audio.squeeze(0).numpy(), sampling_rate, subtype="FLOAT")

    monkeypatch.setattr(vad.silero_vad, "save_audio", save)
    transcript = SimpleNamespace(segments=[], settling_state=None)
    session = _FakeSession(_FakeRecording(RecordingStatus.UPLOADING, transcript))
    _run_live_task(monkeypatch, 42, 0, session)
    assert live_transcribe.read_live_state(tmp_path / "live")["next_expected"] == 1
    assert len(label_runs) == int(labels)
    line = transcript.segments[0]
    evidence = line["confidence_payload"]["asr_segments"][0]
    assert line["text"] == evidence["text"] == "yes please"
    assert reconstruct_text_from_words(evidence["words"]) == "yes please"
    assert [word["word"].strip() for word in evidence["words"]] == ["yes", "please"]
