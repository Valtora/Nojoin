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
