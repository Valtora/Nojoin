"""The processing stages honour a user's tuning values, and ignore bad ones."""

from __future__ import annotations

import logging
import types

import numpy as np
import pytest
import soundfile as sf
import torch

from backend.processing import vad as vad_module


def _fake_silero(monkeypatch) -> dict:
    """Stub Silero so only the parameters it is called with matter."""
    captured: dict = {}

    class _Model:
        def to(self, device):
            return self

    def _get_speech_timestamps(*args, **kwargs):
        captured.update(kwargs)
        return [{"start": 0, "end": 16000}]

    def _save_audio(path, tensor, sampling_rate=16000):
        sf.write(path, np.zeros(16000, dtype="float32"), sampling_rate)

    monkeypatch.setattr(vad_module.silero_vad, "load_silero_vad", lambda: _Model())
    monkeypatch.setattr(
        vad_module.silero_vad, "get_speech_timestamps", _get_speech_timestamps
    )
    monkeypatch.setattr(vad_module.silero_vad, "save_audio", _save_audio)
    return captured


def _install_vad_parameters(monkeypatch, vad_parameters: dict | None) -> None:
    from backend.utils.config_manager import config_manager

    def _get(key, default=None):
        if key == "vad_parameters" and vad_parameters is not None:
            return vad_parameters
        return default

    monkeypatch.setattr(config_manager, "get", _get)


def _mute(tmp_path, config) -> None:
    input_path = tmp_path / "in.wav"
    sf.write(input_path, np.zeros(16000, dtype="float32"), 16000)
    success, _ = vad_module.mute_non_speech_segments(
        str(input_path), str(tmp_path / "out.wav"), config=config
    )
    assert success is True


@pytest.mark.parametrize(
    ("vad_parameters", "config", "expected"),
    [
        # Nothing set anywhere: today's 0.5.
        (None, None, 0.5),
        (None, {}, 0.5),
        # The user's value applies.
        (None, {"vad_threshold": 0.3}, 0.3),
        # The install's legacy vad_parameters still applies when the user is unset.
        ({"threshold": 0.4}, {"vad_threshold": None}, 0.4),
        # The user's value beats the install's.
        ({"threshold": 0.4}, {"vad_threshold": 0.3}, 0.3),
        # An unusable stored value falls back to the install's.
        ({"threshold": 0.4}, {"vad_threshold": 1.7}, 0.4),
    ],
)
def test_final_vad_threshold_resolution(
    tmp_path, monkeypatch, vad_parameters, config, expected
):
    captured = _fake_silero(monkeypatch)
    _install_vad_parameters(monkeypatch, vad_parameters)

    _mute(tmp_path, config)

    assert captured["threshold"] == expected


def test_unusable_vad_threshold_is_logged(tmp_path, monkeypatch, caplog):
    _fake_silero(monkeypatch)
    _install_vad_parameters(monkeypatch, None)

    with caplog.at_level(logging.WARNING):
        _mute(tmp_path, {"vad_threshold": 1.7})

    assert "vad_threshold" in caplog.text


def test_live_vad_honours_the_threshold_in_its_config(monkeypatch):
    captured = _fake_silero(monkeypatch)
    _install_vad_parameters(monkeypatch, None)

    vad_module.detect_speech_segments(
        torch.zeros(16000), sample_rate=16000, config={"vad_threshold": 0.3}
    )

    assert captured["threshold"] == 0.3


def test_live_vad_without_a_threshold_keeps_the_default(monkeypatch):
    captured = _fake_silero(monkeypatch)
    _install_vad_parameters(monkeypatch, None)

    vad_module.detect_speech_segments(
        torch.zeros(16000), sample_rate=16000, config={"vad_threshold": None}
    )

    assert captured["threshold"] == 0.5


class _LiveSession:
    def __init__(self, user_settings: dict) -> None:
        self._recording = types.SimpleNamespace(user_id=5)
        self._user = types.SimpleNamespace(settings=user_settings)

    def get(self, model, _pk):
        return self._recording if model.__name__ == "Recording" else self._user

    def exec(self, *_args, **_kwargs):
        raise AssertionError("resolve_llm_config is stubbed")

    def close(self) -> None:
        pass


def _resolve_live(monkeypatch, user_settings: dict) -> dict:
    from backend.core import db as db_module
    from backend.processing import live_transcribe as lt
    from backend.worker import tasks as tasks_module

    monkeypatch.setattr(
        db_module, "get_sync_session", lambda: _LiveSession(user_settings)
    )
    # Stands in for the owner/config merge: the user's values over the base.
    monkeypatch.setattr(
        tasks_module,
        "resolve_llm_config",
        lambda _session, settings: types.SimpleNamespace(merged_config=dict(settings)),
    )
    live_config = {
        "transcription_backend": "parakeet",
        "parakeet_model": "p",
        "canary_model": "c",
        "whisper_model_size": "turbo",
        "transcription_language": "auto",
        "processing_device": "auto",
        "forced_max_s": 8.0,
        "max_segment_s": 20.0,
    }
    return lt._resolve_live_engine_config(42, live_config)


def test_live_engine_config_carries_the_owners_vad_threshold(monkeypatch):
    live_config = _resolve_live(monkeypatch, {"vad_threshold": 0.3})

    assert live_config["vad_threshold"] == 0.3


def test_live_engine_config_carries_the_owners_word_end_padding(monkeypatch):
    live_config = _resolve_live(monkeypatch, {"asr_word_end_padding_s": 0.5})

    assert live_config["asr_word_end_padding_s"] == 0.5


# --- onnx-asr word end padding -------------------------------------------------


class _Recognized:
    """Two words 1.2 s apart: more than the 0.8 s pause after the first."""

    text = "hello world"
    tokens = [" hello", " world"]
    timestamps = [0.0, 1.2]


def test_default_padding_ends_the_word_early_and_splits_the_segment() -> None:
    from backend.processing.engines.onnx_asr_engine import map_onnx_asr_recognition

    result = map_onnx_asr_recognition(_Recognized(), audio_duration=2.0)

    assert result["segments"][0]["words"][0]["end"] == 0.2
    assert len(result["segments"]) == 2


def test_wider_padding_extends_the_word_and_keeps_one_segment() -> None:
    from backend.processing.engines.onnx_asr_engine import map_onnx_asr_recognition

    result = map_onnx_asr_recognition(
        _Recognized(), audio_duration=2.0, word_end_pad_s=0.5
    )

    assert result["segments"][0]["words"][0]["end"] == 0.5
    # The 0.7 s left before the next word is under the pause threshold.
    assert len(result["segments"]) == 1


def _onnx_engine_over(tmp_path, monkeypatch, seconds: float):
    from backend.processing.engines.parakeet_engine import ParakeetEngine

    audio_path = tmp_path / "audio.wav"
    sf.write(str(audio_path), np.zeros(int(seconds * 16000), dtype="float32"), 16000)

    class _Recognizer:
        def recognize(self, path):
            return _Recognized()

    class _Model:
        def with_timestamps(self):
            return _Recognizer()

    engine = ParakeetEngine()
    monkeypatch.setattr(engine, "_get_model", lambda config: _Model())
    return engine, str(audio_path)


@pytest.mark.parametrize(
    ("config", "expected_end"),
    [(None, 0.2), ({}, 0.2), ({"asr_word_end_padding_s": 0.5}, 0.5)],
)
def test_onnx_engine_reads_padding_from_its_config(
    tmp_path, monkeypatch, config, expected_end
):
    engine, audio_path = _onnx_engine_over(tmp_path, monkeypatch, 2.0)

    result = engine.transcribe(audio_path, config)

    assert result["segments"][0]["words"][0]["end"] == expected_end


def test_onnx_engine_passes_padding_to_every_window(tmp_path, monkeypatch):
    from backend.processing.engines import onnx_asr_engine

    monkeypatch.setattr(onnx_asr_engine, "MAX_CHUNK_DURATION_S", 4.0)
    monkeypatch.setattr(onnx_asr_engine, "CHUNK_SNAP_RADIUS_S", 0.5)
    engine, audio_path = _onnx_engine_over(tmp_path, monkeypatch, 10.0)

    result = engine.transcribe(audio_path, {"asr_word_end_padding_s": 0.5})

    first_words = [
        segment["words"][0]
        for segment in result["segments"]
        if segment["words"][0]["word"] == " hello"
    ]
    assert len(first_words) == 3
    assert all(round(word["end"] - word["start"], 6) == 0.5 for word in first_words)


def test_onnx_engine_ignores_unusable_padding(tmp_path, monkeypatch):
    engine, audio_path = _onnx_engine_over(tmp_path, monkeypatch, 2.0)

    result = engine.transcribe(audio_path, {"asr_word_end_padding_s": 0.0})

    assert result["segments"][0]["words"][0]["end"] == 0.2
