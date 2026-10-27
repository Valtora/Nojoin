"""Exercise the worker-to-fenced-publisher adapter on actual PostgreSQL rows."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import soundfile as sf
import torch
from sqlalchemy import Engine
from sqlmodel import Session

from backend.models.recording import Recording
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
from backend.utils.canonical_pipeline.live_settling_projection import row_words
from backend.utils.transcript_settling import capture_publication_token


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
