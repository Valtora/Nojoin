"""Exercise the worker-to-fenced-publisher adapter on actual PostgreSQL rows."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import soundfile as sf
import torch
from sqlalchemy import Engine
from sqlmodel import Session, select

from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.processing import live_settling as worker
from backend.processing import transcribe
from backend.tests.test_live_settling_publication import (
    _active,
    _seed,
)
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)
from backend.utils.canonical_pipeline.live_settling import close_live_settling
from backend.utils.canonical_pipeline.live_settling_projection import row_words
from backend.utils.transcript_settling import (
    capture_publication_token,
    load_capture_publication,
    update_capture_settling_state,
)


@pytest.fixture
def worker_db(
    settling_engine: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Any:
    from backend.core import db
    from backend.processing import vad

    recording_id = _seed(settling_engine)
    with Session(settling_engine) as session:
        recording = session.get(Recording, recording_id)
        token = capture_publication_token(recording, recording.transcript)
    monkeypatch.setattr(db, "get_sync_session", lambda: Session(settling_engine))
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(transcribe, "loaded_transcription_device", lambda _: "cuda")
    monkeypatch.setattr(worker, "_speaker_evidence", lambda _: None)
    monkeypatch.setattr(
        vad.silero_vad,
        "save_audio",
        lambda path, audio, sampling_rate: sf.write(
            path, audio.squeeze(0).numpy(), sampling_rate
        ),
    )
    monkeypatch.setattr(
        transcribe,
        "transcribe_audio",
        lambda *_args, **_kwargs: {
            "text": "hello",
            "segments": [
                {
                    "start": 0.1,
                    "end": 0.5,
                    "text": "hello",
                    "words": [{"word": "hello", "start": 0.1, "end": 0.5}],
                }
            ],
        },
    )
    live_dir = tmp_path / "live"
    live_dir.mkdir()

    def step(sequence: int) -> worker.SettlingStep:
        sf.write(
            tmp_path / f"{sequence}.wav", np.ones((32000, 2), np.float32) * 0.1, 16000
        )
        return worker.SettlingStep(
            recording_id,
            sequence,
            tmp_path,
            live_dir,
            (sequence + 1) * 2000,
            0,
            [{"start": sequence * 2, "end": (sequence + 1) * 2}],
            token,
        )

    return SimpleNamespace(recording_id=recording_id, step=step)


def test_worker_publishes_four_observations_with_captured_policy_and_bounded_payload(
    worker_db: Any, settling_engine: Engine
) -> None:
    identities: list[str] = []
    for sequence in range(4):
        result = worker.run_settling_step(
            worker_db.step(sequence), {"processing_device": "auto"}
        )
        assert result.handled and result.accepted
        with Session(settling_engine) as session:
            rows = _active(session, worker_db.recording_id)
            words = [word for row in rows for word in row_words(row)]
            assert len(words) == 1
            identities.append(words[0]["id"])
            assert words[0]["unchanged_count"] == sequence
            assert words[0]["locked"] is (sequence == 3)
            transcript = (
                session.query(Transcript)
                .filter_by(recording_id=worker_db.recording_id)
                .one()
            )
            assert transcript.segments == []
            assert (
                rows[0].confidence_payload["source_channel_activity"]["channel_count"]
                == 2
            )
    assert len(set(identities)) == 1


def test_actual_cpu_fallback_closes_last_successful_words_before_legacy_lane(
    worker_db: Any, settling_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker.run_settling_step(worker_db.step(0), {})
    monkeypatch.setattr(transcribe, "loaded_transcription_device", lambda _: "cpu")
    result = worker.run_settling_step(worker_db.step(1), {})
    assert not result.handled
    with Session(settling_engine) as session:
        transcript = (
            session.query(Transcript)
            .filter_by(recording_id=worker_db.recording_id)
            .one()
        )
        assert transcript.settling_state["policy"]["enabled"] is False
        words = [
            word
            for row in _active(session, worker_db.recording_id)
            for word in row_words(row)
        ]
        assert words[0]["locked"] and words[0]["lock_reason"] == "closure"
        assert transcript.segments[0]["text"] == "hello"


def _lifecycle(engine: Engine, recording_id: int, phase: str) -> Any:
    """Commit what the pause and resume routes do; return the episode's token."""
    with Session(engine) as session:
        transcript, recording = load_capture_publication(session, recording_id)
        state = transcript.settling_state or {}
        if state.get("last_decode_id") and state.get("phase") == "live":
            close_live_settling(session, recording_id=recording_id)
        update_capture_settling_state(
            transcript, capture_epoch=recording.capture_epoch, sealed=False, phase=phase
        )
        recording.status = (
            RecordingStatus.PAUSED if phase == "paused" else RecordingStatus.UPLOADING
        )
        session.add_all([transcript, recording])
        session.commit()
        return capture_publication_token(recording, transcript)


def _decoded(*words: tuple[str, int, int]) -> Any:
    return {
        "asr_segments": [
            {
                "words": [
                    {"word": word, "start_ms": start, "end_ms": end}
                    for word, start, end in words
                ]
            }
        ]
    }, "cuda"


@pytest.mark.parametrize("pause_at", ["before_snapshot", "during_decode"])
def test_tail_uploaded_before_pause_lands_locked_while_paused(
    worker_db: Any,
    settling_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    pause_at: str,
) -> None:
    """The browser uploads its partial last segment just before the pause."""
    assert worker.run_settling_step(worker_db.step(0), {}).accepted
    tail = worker_db.step(1)

    def decode(*_args: Any) -> Any:
        if pause_at == "during_decode":
            _lifecycle(settling_engine, worker_db.recording_id, "paused")
        return _decoded(("hello", 100, 500), (" there", 2500, 3000))

    monkeypatch.setattr(worker, "_decode_words", decode)
    if pause_at == "before_snapshot":
        _lifecycle(settling_engine, worker_db.recording_id, "paused")
    result = worker.run_settling_step(tail, {})
    # Accepted, so the live lane consumes the tail instead of holding it for
    # the first segment after Resume.
    assert result.handled and result.accepted
    with Session(settling_engine) as session:
        words = [
            word
            for row in _active(session, worker_db.recording_id)
            for word in row_words(row)
        ]
        assert [word["word"] for word in words] == ["hello", " there"]
        assert all(word["locked"] for word in words)
        assert words[1]["lock_reason"] == "closure"
        transcript = session.exec(
            select(Transcript).where(Transcript.recording_id == worker_db.recording_id)
        ).one()
        assert transcript.settling_state["phase"] == "paused"
        # The compatibility snapshot holds the tail too, as at any closure.
        assert [segment["text"] for segment in transcript.segments] == [
            "hello",
            "there",
        ]

    # Resume starts a new episode after the tail, which no later decode repeats.
    token = _lifecycle(settling_engine, worker_db.recording_id, "live")
    monkeypatch.setattr(
        worker,
        "_decode_words",
        lambda *_args: _decoded(
            ("hello", 100, 500), (" there", 2500, 3000), (" again", 4200, 4600)
        ),
    )
    assert worker.run_settling_step(
        replace(worker_db.step(2), expected_token=token), {}
    ).accepted
    with Session(settling_engine) as session:
        assert [
            word["word"]
            for row in _active(session, worker_db.recording_id)
            for word in row_words(row)
        ] == ["hello", " there", " again"]


@pytest.mark.parametrize("episode", ["off", "auto_on_cpu"])
def test_paused_tail_without_settling_goes_to_the_whole_line_lane(
    worker_db: Any,
    settling_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    episode: str,
) -> None:
    """Off and CPU Auto episodes transcribe the paused tail as upstream does."""
    with Session(settling_engine) as session:
        transcript, _ = load_capture_publication(session, worker_db.recording_id)
        transcript.settling_state = {
            **transcript.settling_state,
            "policy": {"enabled": False if episode == "off" else None},
        }
        session.add(transcript)
        session.commit()
    _lifecycle(settling_engine, worker_db.recording_id, "paused")
    monkeypatch.setattr(transcribe, "loaded_transcription_device", lambda _: "cpu")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    result = worker.run_settling_step(worker_db.step(0), {})
    assert not result.handled
    with Session(settling_engine) as session:
        transcript, _ = load_capture_publication(
            session, worker_db.recording_id, lock=False
        )
        assert transcript.settling_state["policy"]["enabled"] is False
        assert not _active(session, worker_db.recording_id)
