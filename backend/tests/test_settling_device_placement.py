"""Settling eligibility follows loaded ASR placement, including silent first runs."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.models.recording import RecordingStatus
from backend.models.transcript import Transcript
from backend.processing import live_transcribe, transcribe
from backend.processing.engines import onnx_asr_engine, whisper_engine
from backend.processing.engines.canary_engine import CanaryEngine
from backend.processing.engines.parakeet_engine import ParakeetEngine
from backend.tests.test_live_transcription import (
    _FakeRecording,
    _FakeSession,
    _make_segment_wav,
    _patch_live_deps,
    _run_live_task,
)
from backend.utils.transcript_settling import resolve_capture_settling_policy


class _ProviderSession:
    def __init__(self, providers: list[str]) -> None:
        self.providers = providers

    def get_providers(self) -> list[str]:
        return self.providers


def _onnx_engine(
    monkeypatch: pytest.MonkeyPatch, backend: str, providers: list[list[str]]
) -> CanaryEngine | ParakeetEngine:
    engine = CanaryEngine() if backend == "canary" else ParakeetEngine()
    model = object()
    engine._model_cache[engine._onnx_id_for({})] = model
    sessions = [_ProviderSession(active) for active in providers]
    monkeypatch.setattr(
        onnx_asr_engine, "iter_inference_sessions", lambda loaded: iter(sessions)
    )
    return engine


def _transcribe_region(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config: dict[str, Any]
) -> list[dict]:
    import soundfile as sf
    import torch

    from backend.processing import forced_alignment, vad

    def save(path: str, tensor: Any, *, sampling_rate: int) -> None:
        sf.write(path, tensor.squeeze(0).numpy(), sampling_rate)

    monkeypatch.setattr(vad.silero_vad, "save_audio", save)
    monkeypatch.setattr(
        forced_alignment, "align_transcription", lambda result, *_args: result
    )
    audio = torch.full((6 * 16000,), 0.1)
    return live_transcribe._transcribe_live_regions(
        recording_id=1,
        sequence=0,
        complete=[{"start": 0.0, "end": 6.0}],
        combined=audio,
        combined_channels=audio.unsqueeze(0),
        combined_abs_start=0.0,
        prev_context=torch.zeros(0),
        context_window_samples=0,
        live_dir=tmp_path,
        live_config=config,
        source_channel_labels={},
        state={},
    )


@pytest.mark.parametrize("backend", ["canary", "parakeet"])
@pytest.mark.parametrize(
    "scenario",
    [
        ([["CUDAExecutionProvider", "CPUExecutionProvider"]], "cpu", "cuda"),
        ([["CPUExecutionProvider"]], "cuda", "cpu"),
        (
            [
                ["CPUExecutionProvider"],
                ["CUDAExecutionProvider", "CPUExecutionProvider"],
            ],
            "cpu",
            "cuda",
        ),
    ],
)
def test_live_onnx_placement_uses_active_providers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    backend: str,
    scenario: tuple[list[list[str]], str, str],
) -> None:
    providers, configured, expected = scenario
    engine = _onnx_engine(monkeypatch, backend, providers)
    # Error attribution's cached flag must not stand in for active providers.
    engine._model_on_gpu[engine._onnx_id_for({})] = expected != "cuda"
    result = {
        "text": "hello",
        "segments": [{"start": 0.0, "end": 6.0, "text": "hello"}],
    }
    monkeypatch.setattr(engine, "transcribe", lambda *_args: result)
    monkeypatch.setattr(transcribe, "_ENGINE_REGISTRY", {backend: engine})
    config = {"transcription_backend": backend, "processing_device": configured}
    assert _transcribe_region(monkeypatch, tmp_path, config)
    assert config["_settling_device"] == expected


@pytest.mark.parametrize(
    ("loaded", "configured"), [("cpu", "cuda"), ("cpu", "auto"), ("cuda:1", "cpu")]
)
def test_live_whisper_placement_uses_selected_cached_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, loaded: str, configured: str
) -> None:
    import torch

    engine = whisper_engine.WhisperEngine()
    monkeypatch.setattr(
        whisper_engine,
        "_model_cache",
        {"base": SimpleNamespace(device=torch.device(loaded))},
    )
    monkeypatch.setattr(torch.cuda, "is_available", lambda: loaded == "cpu")
    result = {
        "text": "hello",
        "segments": [{"start": 0.0, "end": 6.0, "text": "hello"}],
    }
    monkeypatch.setattr(engine, "transcribe", lambda *_args: result)
    monkeypatch.setattr(transcribe, "_ENGINE_REGISTRY", {"whisper": engine})
    config = {"transcription_backend": "whisper", "processing_device": configured}
    assert _transcribe_region(monkeypatch, tmp_path, config)
    assert config["_settling_device"] == loaded.split(":", 1)[0]


@pytest.mark.parametrize("backend", ["canary", "parakeet", "whisper"])
def test_unloaded_selected_model_does_not_guess_or_load(
    monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    engine = (
        whisper_engine.WhisperEngine()
        if backend == "whisper"
        else _onnx_engine(monkeypatch, backend, [["CUDAExecutionProvider"]])
    )
    monkeypatch.setattr(
        whisper_engine, "_model_cache", {"other": SimpleNamespace(device="cuda")}
    )
    monkeypatch.setattr(transcribe, "_ENGINE_REGISTRY", {backend: engine})
    config = {"transcription_backend": backend, "whisper_model_size": "unloaded"}
    if backend != "whisper":
        config[engine.config_key] = "unloaded"
        monkeypatch.setattr(
            engine, "_get_model", lambda _: pytest.fail("classification loaded a model")
        )
    assert transcribe.loaded_transcription_device(config) is None
    assert (
        transcribe.loaded_transcription_device({"transcription_backend": "absent"})
        is None
    )


@pytest.mark.parametrize("inspection", ["empty", "error", "empty_providers"])
def test_onnx_unknown_providers_remain_pending(
    monkeypatch: pytest.MonkeyPatch, inspection: str
) -> None:
    engine = _onnx_engine(
        monkeypatch, "parakeet", [] if inspection == "empty" else [[]]
    )
    if inspection == "error":

        def fail(_model: Any) -> Any:
            raise RuntimeError("provider inspection unavailable")

        monkeypatch.setattr(onnx_asr_engine, "iter_inference_sessions", fail)
    monkeypatch.setattr(onnx_asr_engine, "gpu_is_present", lambda: True)
    engine._model_on_gpu[engine._onnx_id_for({})] = True
    assert engine.loaded_device({}) is None


@pytest.mark.parametrize("backend", ["parakeet", "whisper"])
def test_silent_first_task_does_not_freeze_a_guessed_device(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, backend: str
) -> None:
    import torch

    temp_dir = tmp_path / "42"
    temp_dir.mkdir()
    speech: list[dict[str, float]] = []
    audio_store = _patch_live_deps(monkeypatch, speech_map=lambda _: speech)
    monkeypatch.setattr(
        live_transcribe, "recording_upload_temp_dir", lambda *_args, **_kwargs: temp_dir
    )
    monkeypatch.setattr(live_transcribe, "live_speaker_labels_requested", lambda: False)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    base_config = live_transcribe._build_live_config()
    base_config.update(
        transcription_backend=backend,
        processing_device="cpu",
        whisper_model_size="base",
    )
    monkeypatch.setattr(
        live_transcribe, "_build_live_config", lambda: dict(base_config)
    )
    transcript = Transcript(
        recording_id=42,
        segments=[],
        settling_state={"policy": {"enabled": None}},
    )
    session = _FakeSession(_FakeRecording(RecordingStatus.UPLOADING, transcript))
    monkeypatch.setattr(transcribe, "_ENGINE_REGISTRY", {})
    _make_segment_wav(temp_dir, 0, 2.0, audio_store)
    _run_live_task(monkeypatch, 42, 0, session)
    assert transcript.settling_state["policy"]["enabled"] is None
    assert transcribe._ENGINE_REGISTRY == {}
    assert live_transcribe.read_live_state(temp_dir / "live")["next_expected"] == 1

    engine = (
        whisper_engine.WhisperEngine()
        if backend == "whisper"
        else _onnx_engine(monkeypatch, backend, [["CUDAExecutionProvider"]])
    )
    monkeypatch.setattr(
        whisper_engine, "_model_cache", {"base": SimpleNamespace(device="cuda:1")}
    )
    monkeypatch.setattr(transcribe, "_ENGINE_REGISTRY", {backend: engine})
    speech.append({"start": 0.0, "end": 1.0})
    _make_segment_wav(temp_dir, 1, 2.0, audio_store)
    _run_live_task(monkeypatch, 42, 1, session)
    assert transcript.segments
    assert transcript.settling_state["policy"]["enabled"] is True
    resolve_capture_settling_policy(transcript, device="cpu")
    assert transcript.settling_state["policy"]["enabled"] is True
