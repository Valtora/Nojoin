"""A newer upload must restart audio retained after a lifecycle rejection."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf
import torch
from sqlalchemy import Engine
from sqlmodel import Session, select

from backend.core import db
from backend.models.pipeline import RecordingAudioWindowManifest
from backend.models.recording import RecordingStatus
from backend.processing import (
    live_persistence,
    live_settling,
    live_transcribe,
    transcribe,
    vad,
)
from backend.processing.live_diarization import step as labels_worker
from backend.tests.test_live_settling_publication import _active, _seed
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)
from backend.utils.canonical_pipeline.live_settling import close_live_settling
from backend.utils.config_manager import config_manager
from backend.utils.transcript_settling import (
    load_capture_publication,
    update_capture_settling_state,
)


@dataclass
class ResumeLane:
    engine: Engine
    path: Path
    recording_id: int
    mode: str
    labels_enabled: bool
    hook: Callable[[], None] | None = None
    boundary: str = ""
    labels: list[Any] = field(default_factory=list)
    closed_rows: list[tuple[Any, ...]] = field(default_factory=list)

    def fire(self, boundary: str) -> None:
        if boundary == self.boundary and self.hook is not None:
            hook, self.hook = self.hook, None
            hook()

    def transition(self, *, resume: bool) -> None:
        with Session(self.engine) as session:
            transcript, recording = load_capture_publication(session, self.recording_id)
            if not resume:
                close_live_settling(session, recording_id=self.recording_id)
            recording.status = (
                RecordingStatus.UPLOADING if resume else RecordingStatus.PAUSED
            )
            update_capture_settling_state(
                transcript,
                capture_epoch=0,
                sealed=False,
                phase="live" if resume else "paused",
            )
            session.commit()
        self.closed_rows = self.rows()

    def pause_resume(self) -> None:
        self.transition(resume=False)
        self.transition(resume=True)

    def rows(self) -> list[tuple[Any, ...]]:
        with Session(self.engine) as session:
            return [
                (
                    row.public_id,
                    row.text,
                    row.start_ms,
                    row.end_ms,
                    row.confidence_payload,
                )
                for row in _active(session, self.recording_id)
            ]

    def upload(self, sequence: int, duration: float = 2) -> None:
        sf.write(
            self.path / f"{sequence}.wav",
            np.ones((round(duration * 16000), 2)) * 0.1,
            16000,
        )
        start = sum(sf.info(self.path / f"{n}.wav").duration for n in range(sequence))
        with Session(self.engine) as session:
            session.add(
                RecordingAudioWindowManifest(
                    recording_id=self.recording_id,
                    window_index=sequence,
                    target_window_ms=2000,
                    hop_ms=2000,
                    window_start_ms=round(start * 1000),
                    window_end_ms=round((start + duration) * 1000),
                    chunk_start_sequence=sequence,
                    chunk_end_sequence=sequence,
                    is_partial=duration < 2,
                    is_sealed=False,
                )
            )
            session.commit()

    def run(self, sequence: int) -> None:
        live_transcribe.transcribe_segment_live_task.run(self.recording_id, sequence)

    def files(self) -> dict[str, bytes]:
        return {
            path.name: path.read_bytes() for path in (self.path / "live").glob("*.wav")
        }


def _read_audio(path: str, **_kwargs: Any) -> torch.Tensor:
    audio, _rate = sf.read(path, dtype="float32", always_2d=True)
    return torch.from_numpy(audio.T.copy())


def _save_audio(path: str, audio: torch.Tensor, *, sampling_rate: int) -> None:
    sf.write(path, audio.numpy().T, sampling_rate, subtype="FLOAT")


def _configure_episode(lane: ResumeLane) -> None:
    with Session(lane.engine) as session:
        transcript, _recording = load_capture_publication(session, lane.recording_id)
        if lane.mode == "off":
            transcript.settling_state = None
        elif lane.mode != "on":
            transcript.settling_state = {
                **transcript.settling_state,
                "policy": {"enabled": False},
            }
        session.commit()


@pytest.fixture
def resume_lane(
    settling_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    request: Any,
) -> ResumeLane:
    mode, labels = request.param
    lane = ResumeLane(settling_engine, tmp_path, _seed(settling_engine), mode, labels)
    settings = {
        "live_speaker_labels": "auto" if labels else "off",
        "live_text_settling": "off" if mode in {"off", "resumed-off"} else "auto",
        "live_context_window_s": 0,
    }
    monkeypatch.setattr(
        config_manager, "get", lambda key, default=None: settings.get(key, default)
    )
    _configure_episode(lane)
    monkeypatch.setattr(db, "get_sync_session", lambda: Session(settling_engine))
    monkeypatch.setattr(
        live_transcribe, "recording_upload_temp_dir", lambda *_a, **_k: tmp_path
    )
    monkeypatch.setattr(
        live_transcribe, "_resolve_live_engine_config", lambda *_a, **_k: {}
    )
    monkeypatch.setattr(live_persistence, "is_meeting_edge_enabled", lambda *_a: False)
    monkeypatch.setattr(vad, "safe_read_audio", _read_audio)
    monkeypatch.setattr(vad.silero_vad, "save_audio", _save_audio)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(
        transcribe,
        "loaded_transcription_device",
        lambda _: "cpu" if mode == "cpu" else "cuda",
    )
    monkeypatch.setattr(live_settling, "_speaker_evidence", lambda _: None)
    monkeypatch.setattr(labels_worker, "run_live_speaker_labels", lane.labels.append)

    def speech(audio: torch.Tensor, **_kwargs: Any) -> list[dict[str, float]]:
        lane.fire("snapshot")
        end = audio.numel() / 16000 - 0.1
        return [{"start": 0.1, "end": end if mode == "on" else min(1.0, end)}]

    def decode(step: Any, _config: Any, _audio: Any, start: int) -> Any:
        lane.fire("decode")
        return {
            "asr_segments": [
                {
                    "words": [
                        {
                            "word": "speech",
                            "start_ms": step.audio_end_ms - 600,
                            "end_ms": step.audio_end_ms - 300,
                        }
                    ]
                }
            ]
        }, "cuda"

    def legacy_decode(_path: str, **_kwargs: Any) -> dict[str, Any]:
        lane.fire("decode")
        return {
            "text": "speech",
            "segments": [
                {
                    "start": 0.1,
                    "end": 0.3,
                    "text": "speech",
                    "words": [{"word": "speech", "start": 0.1, "end": 0.3}],
                }
            ],
        }

    persist = live_transcribe._persist_live_run

    def persist_after_transition(**kwargs: Any) -> Any:
        lane.fire("persist")
        return persist(**kwargs)

    monkeypatch.setattr(vad, "detect_speech_segments", speech)
    monkeypatch.setattr(live_settling, "_decode_words", decode)
    monkeypatch.setattr(transcribe, "transcribe_audio", legacy_decode)
    monkeypatch.setattr(live_transcribe, "_persist_live_run", persist_after_transition)
    lane.upload(0)
    lane.run(0)
    assert live_transcribe.read_live_state(tmp_path / "live")["next_expected"] == 1
    assert lane.rows()
    assert bool(lane.labels) is labels
    return lane


def _assert_newer_task_recovers(lane: ResumeLane) -> None:
    before = lane.rows()
    lane.upload(3)
    lane.run(3)  # Production dispatches the new upload, never manually retries 1.
    state = live_transcribe.read_live_state(lane.path / "live")
    assert state["next_expected"] == 4
    if lane.mode == "on":
        assert state["buffer_abs_start"] == 6.4
    assert all((lane.path / f"{sequence}.wav").exists() for sequence in range(4))
    assert lane.rows() != before
    with Session(lane.engine) as session:
        manifests = session.exec(select(RecordingAudioWindowManifest)).all()
        assert all(row.status == "live_processed" for row in manifests)
    recovered = lane.rows()
    lane.upload(4)
    lane.run(4)
    final_state = live_transcribe.read_live_state(lane.path / "live")
    assert final_state["next_expected"] == 5
    if lane.mode == "on":
        assert final_state["buffer_abs_start"] == 8.4
    rows = lane.rows()
    assert rows != recovered
    spans = [(row[1], row[2], row[3]) for row in rows]
    assert len(spans) == len(set(spans))
    assert all(start < end for _text, start, end in spans)
    assert spans == sorted(spans, key=lambda span: span[1])
    retained = {row[0]: row[1:4] for row in rows}
    assert all(retained[row[0]] == row[1:4] for row in lane.closed_rows)
    if lane.mode == "on":
        retained_rows = {row[0]: row for row in rows}
        assert all(retained_rows[row[0]] == row for row in lane.closed_rows)
    expected_last = (
        (7800, 8100)
        if lane.mode == "on"
        else ((3100, 4000) if lane.mode == "off" else (2100, 3000))
    )
    assert rows[-1][2:4] == expected_last
    assert sum(sf.info(lane.path / f"{n}.wav").duration for n in range(5)) == 8.4
    assert bool(lane.labels) is lane.labels_enabled
    if lane.labels_enabled:
        assert lane.labels[-1].sequence == 4


@pytest.mark.parametrize("resume_lane", [("on", False), ("on", True)], indirect=True)
def test_paused_snapshot_retains_mid_chunk_tail_for_newer_upload(
    resume_lane: ResumeLane,
) -> None:
    lane = resume_lane
    lane.upload(1)
    lane.upload(2, 0.4)
    files, state = lane.files(), live_transcribe.read_live_state(lane.path / "live")
    lane.boundary, lane.hook = "snapshot", lambda: lane.transition(resume=False)
    lane.run(1)
    assert lane.hook is None
    assert lane.rows() == lane.closed_rows
    assert lane.files() == files
    assert live_transcribe.read_live_state(lane.path / "live") == state
    lane.transition(resume=True)
    _assert_newer_task_recovers(lane)


@pytest.mark.parametrize(
    "resume_lane",
    [
        (mode, labels)
        for mode in ("on", "off", "cpu", "resumed-off")
        for labels in (False, True)
    ],
    indirect=True,
)
def test_resume_during_inference_preserves_fence_and_restarts_lane(
    resume_lane: ResumeLane,
) -> None:
    _race_resume(resume_lane, "decode")


@pytest.mark.parametrize(
    "resume_lane",
    [
        (mode, labels)
        for mode in ("on", "off", "cpu", "resumed-off")
        for labels in (False, True)
    ],
    indirect=True,
)
def test_resume_before_persistence_preserves_fence_and_restarts_lane(
    resume_lane: ResumeLane,
) -> None:
    _race_resume(resume_lane, "persist")


def _race_resume(lane: ResumeLane, boundary: str) -> None:
    lane.upload(1)
    lane.upload(2, 0.4)
    files, state = lane.files(), live_transcribe.read_live_state(lane.path / "live")
    lane.boundary, lane.hook = boundary, lane.pause_resume
    lane.run(1)
    assert lane.hook is None
    if lane.mode != "off":
        assert lane.rows() == lane.closed_rows
        assert lane.files() == files
        assert live_transcribe.read_live_state(lane.path / "live") == state
    else:
        assert live_transcribe.read_live_state(lane.path / "live")["next_expected"] == 3
    _assert_newer_task_recovers(lane)
