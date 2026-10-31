"""A settling capture detects its language once, not on every step (#776).

Whisper on automatic used to detect a language on every 2 s settling step.
Now the capture is pinned to the language once three steps in a row hear it,
and later steps transcribe in it without detecting. A set language, the
recording's own or else the owner's, never detects. Only the Whisper model is
faked: the dispatcher, the engine's language choice, the settling step and
its publication on Postgres are real.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import soundfile as sf
import torch
from sqlalchemy import Engine
from sqlmodel import Session, select

from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.processing import live_settling as worker
from backend.processing import live_transcribe, vad
from backend.processing.engines import whisper_engine
from backend.processing.engines.base import CAPTURE_LANGUAGE
from backend.processing.live_settling_language import (
    CAPTURE_LANGUAGE_KEY,
    observe_language,
    step_language,
)
from backend.tests.test_live_settling_publication import _seed
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)
from backend.utils.config_manager import config_manager
from backend.utils.transcript_settling import capture_publication_token


class _FakeWhisper:
    """Whisper's model: hears ``heard`` in turn when asked to detect."""

    dims = SimpleNamespace(n_mels=128)
    device = torch.device("cpu")
    is_multilingual = True

    def __init__(self, heard: list[str]) -> None:
        self.heard = list(heard)
        self.languages: list[str | None] = []

    def transcribe(self, _path: str, **kwargs: Any) -> dict[str, Any]:
        language = kwargs.get("language")
        self.languages.append(language)
        if language is None:
            language = self.heard.pop(0) if len(self.heard) > 1 else self.heard[0]
        return {
            "text": " hello",
            "language": language,
            "segments": [
                {
                    "start": 0.1,
                    "end": 0.5,
                    "text": " hello",
                    "words": [{"word": " hello", "start": 0.1, "end": 0.5}],
                }
            ],
        }


@pytest.fixture
def capture(
    settling_engine: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Any:
    """A settling capture on the CPU (``always``) with a fake Whisper model."""
    from backend.core import db

    recording_id = _seed(settling_engine)
    with Session(settling_engine) as session:
        recording = session.get(Recording, recording_id)
        token = capture_publication_token(recording, recording.transcript)
    settings = {"live_speaker_labels": "off", "live_text_settling": "always"}
    monkeypatch.setattr(
        config_manager, "get", lambda key, default=None: settings.get(key, default)
    )
    monkeypatch.setattr(db, "get_sync_session", lambda: Session(settling_engine))
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(worker, "_speaker_evidence", lambda _: None)
    monkeypatch.setattr(
        vad.silero_vad,
        "save_audio",
        lambda path, audio, sampling_rate: sf.write(
            path, audio.squeeze(0).numpy(), sampling_rate
        ),
    )
    model = _FakeWhisper(["en"])
    monkeypatch.setattr(whisper_engine.whisper, "load_model", lambda *a, **k: model)
    monkeypatch.setattr(whisper_engine, "_model_cache", {})
    live_dir = tmp_path / "live"
    live_dir.mkdir()

    def run(sequence: int, language: str = "auto", backend: str = "whisper") -> Any:
        sf.write(
            tmp_path / f"{sequence}.wav", np.ones((32000, 2), np.float32) * 0.1, 16000
        )
        step = worker.SettlingStep(
            recording_id,
            sequence,
            tmp_path,
            live_dir,
            (sequence + 1) * 2000,
            0,
            [{"start": sequence * 2, "end": (sequence + 1) * 2}],
            token,
        )
        config = {
            "transcription_backend": backend,
            "whisper_model_size": "turbo",
            "transcription_language": language,
            "processing_device": "cpu",
        }
        result = worker.run_settling_step(step, config)
        assert result.handled and result.accepted
        return config

    def state() -> dict[str, Any]:
        with Session(settling_engine) as session:
            transcript = session.exec(
                select(Transcript).where(Transcript.recording_id == recording_id)
            ).one()
            return dict(transcript.settling_state or {})

    return SimpleNamespace(recording_id=recording_id, model=model, run=run, state=state)


def test_three_steps_that_agree_pin_the_capture_and_later_steps_skip_detection(
    capture: Any,
) -> None:
    # The first step's short input sounds like Japanese; then English holds.
    capture.model.heard = ["ja", "en"]
    for sequence in range(8):
        config = capture.run(sequence)

    assert capture.model.languages == [None, None, None, None] + ["en"] * 4
    assert capture.state()[CAPTURE_LANGUAGE_KEY] == {
        "code": "en",
        "count": 3,
        "pinned": True,
    }
    # The pin stays on the step's own copy of the live config.
    assert CAPTURE_LANGUAGE not in config


def test_a_set_language_never_detects(capture: Any) -> None:
    for sequence in range(5):
        capture.run(sequence, language="fr")

    assert capture.model.languages == ["fr"] * 5
    assert CAPTURE_LANGUAGE_KEY not in capture.state()


def test_the_recordings_own_language_reaches_the_live_lane(
    settling_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recording's language wins over the owner's, as in the final pass."""
    from backend.core import db

    recording_id = _seed(settling_engine)
    with Session(settling_engine) as session:
        recording = session.get(Recording, recording_id)
        recording.transcription_language = "de"
        session.add(recording)
        session.commit()
    monkeypatch.setattr(db, "get_sync_session", lambda: Session(settling_engine))

    config = live_transcribe._resolve_live_engine_config(
        recording_id, live_transcribe._build_live_config()
    )

    assert config["transcription_language"] == "de"
    assert not step_language(config, None).detects


def test_whisper_transcribes_in_the_capture_language_unless_one_is_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = _FakeWhisper(["ja"])
    monkeypatch.setattr(whisper_engine.whisper, "load_model", lambda *a, **k: model)
    monkeypatch.setattr(whisper_engine, "_model_cache", {})
    audio = tmp_path / "clip.wav"
    sf.write(audio, np.zeros(16000, np.float32), 16000)
    engine = whisper_engine.WhisperEngine()
    base = {"whisper_model_size": "turbo", "processing_device": "cpu"}

    pinned = engine.transcribe(str(audio), {**base, CAPTURE_LANGUAGE: "en"})
    chosen = engine.transcribe(
        str(audio),
        {**base, CAPTURE_LANGUAGE: "en", "transcription_language": "fr"},
    )

    assert model.languages == ["en", "fr"]
    assert pinned["language"] == "en" and "language_detection" not in pinned
    assert chosen["language"] == "fr"


@pytest.mark.parametrize(
    ("backend", "language"),
    [("parakeet", "auto"), ("canary", "auto"), ("whisper", "fr")],
)
def test_only_whisper_on_automatic_detects(backend: str, language: str) -> None:
    config = {"transcription_backend": backend, "transcription_language": language}
    pinned = {"code": "en", "count": 3, "pinned": True}

    step = step_language(config, pinned)

    assert not step.detects
    assert step.config == config


def test_a_disagreement_starts_the_count_again() -> None:
    capture = None
    for heard in ("en", "en", "de", "en", "en"):
        capture = observe_language(capture, heard)
    assert capture == {"code": "en", "count": 2, "pinned": False}

    capture = observe_language(capture, "en")
    assert capture == {"code": "en", "count": 3, "pinned": True}
    assert observe_language(capture, "de") is capture
    assert step_language({}, capture).config == {CAPTURE_LANGUAGE: "en"}
