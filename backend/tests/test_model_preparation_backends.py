"""Which models a preparation run puts on disk.

API startup queues preparation with the core batch on and the install's
configured transcription backend. Only the Whisper engine loads Whisper, so an
install on Parakeet or Canary must not download it at every start; the
diarisation and voice-embedding models are needed whatever the engine.
"""

from __future__ import annotations

import pytest

from backend import preload_models


@pytest.fixture
def prepared(monkeypatch) -> list[str]:
    """Record each preparation step in place of the real downloads."""
    steps: list[str] = []
    monkeypatch.setattr(preload_models, "clear_download_progress", lambda: None)
    monkeypatch.setattr(
        preload_models, "set_download_progress", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(preload_models, "_release_validation_caches", lambda: None)
    monkeypatch.setattr(
        preload_models,
        "_prepare_whisper_model",
        lambda size: steps.append(f"whisper:{size}"),
    )
    monkeypatch.setattr(
        preload_models,
        "_prepare_pyannote_models",
        lambda token: steps.append("pyannote"),
    )
    monkeypatch.setattr(
        preload_models,
        "_prepare_onnx_asr_model",
        lambda model_id: steps.append(f"onnx:{model_id}"),
    )
    monkeypatch.setattr(
        preload_models,
        "_resolve_onnx_asr_id",
        lambda backend, model_id: f"{backend}/{model_id}",
    )
    return steps


@pytest.mark.parametrize(
    ("backend", "model_kwarg", "model_id"),
    [
        ("parakeet", "parakeet_model", "parakeet-tdt-0.6b-v3"),
        ("canary", "canary_model", "nemo-canary-1b-v2"),
    ],
)
def test_core_batch_on_an_onnx_engine_skips_whisper(
    prepared, backend, model_kwarg, model_id
):
    preload_models.download_models(
        transcription_backend=backend,
        whisper_model_size="turbo",
        include_core=True,
        **{model_kwarg: model_id},
    )

    assert prepared == ["pyannote", f"onnx:{backend}/{model_id}"]


def test_core_batch_on_whisper_prepares_the_configured_whisper_size(prepared):
    preload_models.download_models(
        transcription_backend="whisper", whisper_model_size="small", include_core=True
    )

    assert prepared == ["whisper:small", "pyannote"]


def test_startup_defaults_follow_the_install_config(prepared, monkeypatch):
    """Startup passes no backend, so the install config decides."""
    config = {"transcription_backend": "parakeet"}
    monkeypatch.setattr(
        preload_models.config_manager,
        "get",
        lambda key, default=None: config.get(key, default),
    )

    preload_models.download_models(include_core=True)

    assert prepared == ["pyannote", "onnx:parakeet/parakeet-tdt-0.6b-v3"]
