"""Whisper detects an automatic language on speech (#671).

Whisper picks the language from the first 30 s of the file it reads: room noise
in the unmuted file, mostly zeros in the muted one. Whenever VAD ran, the engine
detects on the first 30 s of speech in the VAD-muted file instead. Only the
Whisper model is faked: the routing, the audio read and the log-mel are the
engine's own.
"""

from __future__ import annotations

import types

import numpy as np
import pytest
import soundfile
import torch
import whisper

from backend.processing.engines import whisper_engine
from backend.processing.engines.base import LANGUAGE_DETECTION_AUDIO, VAD_MUTED_INPUT
from backend.processing.engines.whisper_engine import WhisperEngine
from backend.tests.test_pipeline_stages import (
    _MUTED,
    _FakeRecording,
    _FakeSession,
    _FakeTranscript,
    _install,
    _install_happy_path_modules,
    _run_task,
    _tuned_llm_config,
)

RATE = 16000


def _tone(seconds: float, level: float, hz: float) -> np.ndarray:
    """A signal that never hits an exact zero, so muting is the only zero."""
    t = np.arange(int(seconds * RATE), dtype=np.float32) / RATE
    return (level + 0.05 * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * RATE), dtype=np.float32)


class _FakeModel:
    """Stands in for Whisper's model: records what it is asked to hear."""

    dims = types.SimpleNamespace(n_mels=80)
    device = torch.device("cpu")

    def __init__(self):
        self.detected_on: list[torch.Tensor] = []
        self.transcribed: list[tuple[str, dict]] = []

    def detect_language(self, mel):
        self.detected_on.append(mel)
        return torch.tensor(0), {"en": 0.9, "ja": 0.1}

    def transcribe(self, audio_path, **kwargs):
        self.transcribed.append((audio_path, kwargs))
        return {"text": "", "language": kwargs.get("language", "ja"), "segments": []}


@pytest.fixture
def model(monkeypatch):
    fake = _FakeModel()
    monkeypatch.setattr(whisper_engine.whisper, "load_model", lambda *a, **k: fake)
    monkeypatch.setattr(whisper_engine, "_model_cache", {})
    return fake


def _write_pair(tmp_path, muted: np.ndarray) -> dict:
    """The VAD stage's two files: ``muted``, and it with room noise everywhere."""
    noise = np.random.default_rng(0).normal(0, 0.01, muted.size).astype(np.float32)
    paths = {"muted": str(tmp_path / "rec_vad_processed.wav")}
    paths["unmuted"] = str(tmp_path / "rec_vad.wav")
    soundfile.write(paths["muted"], muted, RATE, subtype="FLOAT")
    soundfile.write(paths["unmuted"], muted + noise, RATE, subtype="FLOAT")
    return paths


@pytest.fixture
def files(tmp_path):
    """Speech at 5-15 s and 25-55 s; its first 30 s are the first stretch and
    20 s of the second."""
    first, second = _tone(10, 0.1, 220), _tone(30, 0.2, 440)
    paths = _write_pair(
        tmp_path, np.concatenate([_silence(5), first, _silence(10), second])
    )
    paths["speech"] = np.concatenate([first, second[: 20 * RATE]])
    return paths


def _auto(files, *, muted: bool) -> dict:
    """The config the pipeline hands an automatic-language run after VAD."""
    return {
        "processing_device": "cpu",
        VAD_MUTED_INPUT: muted,
        LANGUAGE_DETECTION_AUDIO: files["muted"],
    }


@pytest.mark.parametrize("muted", [False, True])
def test_auto_language_is_detected_on_the_speech(model, files, muted):
    """Muted or not, the engine transcribes its own input in the language heard
    in the first 30 s of speech, not in its input's first 30 s."""
    audio = files["muted" if muted else "unmuted"]

    result = WhisperEngine().transcribe(audio, _auto(files, muted=muted))

    expected = whisper.log_mel_spectrogram(files["speech"], 80)
    assert len(model.detected_on) == 1
    assert torch.equal(model.detected_on[0], expected)
    [(path, kwargs)] = model.transcribed
    assert path == audio
    assert kwargs["language"] == "en"
    assert result["language"] == "en"


def test_under_30_s_of_speech_is_padded_to_whispers_window(model, tmp_path):
    speech = _tone(12, 0.1, 220)
    files = _write_pair(tmp_path, np.concatenate([_silence(3), speech, _silence(3)]))

    WhisperEngine().transcribe(files["unmuted"], _auto(files, muted=False))

    expected = whisper.log_mel_spectrogram(whisper.pad_or_trim(speech), 80)
    assert torch.equal(model.detected_on[0], expected)
    assert model.transcribed[0][1]["language"] == "en"


def test_a_set_language_is_used_without_detection(model, files):
    config = {
        "transcription_language": "fr",
        VAD_MUTED_INPUT: False,
        LANGUAGE_DETECTION_AUDIO: files["muted"],
    }

    WhisperEngine().transcribe(files["unmuted"], config)

    assert model.detected_on == []
    assert model.transcribed[0][1]["language"] == "fr"


def test_with_vad_off_whisper_detects_on_its_input_as_before(model, files):
    WhisperEngine().transcribe(files["unmuted"], {VAD_MUTED_INPUT: False})

    assert model.detected_on == []
    assert "language" not in model.transcribed[0][1]


def _detection_audio_handed_to_asr(monkeypatch, **settings) -> dict:
    recording = _FakeRecording(731)
    session = _FakeSession(recording, _FakeTranscript(731))
    _install_happy_path_modules(monkeypatch)
    seen: dict = {}

    def _asr(path, config=None):
        seen["config"] = dict(config)
        return {"text": "hello", "language": "en", "segments": []}

    _install(
        monkeypatch,
        "backend.processing.transcribe",
        transcribe_audio=_asr,
        release_model_cache=lambda: None,
    )
    _run_task(
        monkeypatch, session, recording_id=731, llm_config=_tuned_llm_config(**settings)
    )
    return seen["config"]


@pytest.mark.parametrize("mute_transcription", [True, False])
def test_the_vad_stage_hands_asr_the_muted_file_for_detection(
    monkeypatch, mute_transcription
):
    config = _detection_audio_handed_to_asr(
        monkeypatch, vad_mute_transcription=mute_transcription
    )

    assert config[LANGUAGE_DETECTION_AUDIO] == _MUTED
    assert config[VAD_MUTED_INPUT] is mute_transcription


def test_with_vad_off_the_vad_stage_hands_asr_no_detection_audio(monkeypatch):
    config = _detection_audio_handed_to_asr(monkeypatch, enable_vad=False)

    assert LANGUAGE_DETECTION_AUDIO not in config
