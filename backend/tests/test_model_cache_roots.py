"""Model status and deletion only look where the loaders actually load from.

Status used to fall back to ~/.cache/huggingface/hub and ~/.cache/whisper even
when HF_HOME or XDG_CACHE_HOME pointed elsewhere. The loaders never read those
fallbacks, so a model found there was reported as downloaded while the engine
would download it again, and deletion, which resolves its target through the
same status check, removed models from the user's personal cache on a
bare-metal install.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend import preload_models
from backend.tests.hf_cache_layout import write_onnx_asr_repo
from backend.utils import pyannote_model_utils

PARAKEET_REPO = "models--istupakov--parakeet-tdt-0.6b-v3-onnx"


@pytest.fixture
def homes(model_cache_env, tmp_path) -> dict[str, Path]:
    """An empty personal HOME and an empty managed cache, nothing else set."""
    managed = tmp_path / "managed"
    managed.mkdir()
    return {"home": model_cache_env, "managed": managed}


def _personal_hub(home: Path) -> Path:
    return home / ".cache" / "huggingface" / "hub"


def _personal_hf_model(home: Path, repo: str) -> Path:
    path = _personal_hub(home) / repo
    path.mkdir(parents=True)
    (path / "marker").write_text("personal copy")
    return path


def test_an_onnx_model_outside_hf_home_is_not_reported(homes, monkeypatch):
    monkeypatch.setenv("HF_HOME", str(homes["managed"]))
    write_onnx_asr_repo(_personal_hub(homes["home"]), "parakeet")

    status = preload_models.check_model_status(whisper_model_size="turbo")

    assert status["parakeet"]["downloaded"] is False
    assert status["parakeet"]["checked_paths"] == [
        str(homes["managed"] / "hub" / PARAKEET_REPO)
    ]


def test_the_hub_cache_follows_xdg_cache_home_like_huggingface_hub(homes, monkeypatch):
    """No HF_HOME: huggingface_hub caches under $XDG_CACHE_HOME/huggingface."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(homes["managed"]))
    model = write_onnx_asr_repo(homes["managed"] / "huggingface" / "hub", "parakeet")

    status = preload_models.check_model_status(whisper_model_size="turbo")

    assert status["parakeet"]["downloaded"] is True
    assert status["parakeet"]["path"] == str(model)


def test_a_whisper_model_outside_xdg_cache_home_is_not_reported(homes, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(homes["managed"]))
    personal = homes["home"] / ".cache" / "whisper"
    personal.mkdir(parents=True)
    (personal / "large-v3-turbo.pt").write_bytes(b"weights")

    status = preload_models.check_model_status(whisper_model_size="turbo")

    assert status["whisper"]["downloaded"] is False


def test_deleting_never_reaches_the_personal_hf_cache(homes, monkeypatch):
    monkeypatch.setenv("HF_HOME", str(homes["managed"]))
    personal = write_onnx_asr_repo(_personal_hub(homes["home"]), "parakeet")

    deleted = preload_models.delete_model("parakeet")

    assert deleted is False
    assert (personal / "refs" / "main").exists()


def test_a_pyannote_model_found_outside_the_managed_cache_is_not_deleted(
    homes, monkeypatch, tmp_path
):
    """Status may load Pyannote from the personal cache; delete must refuse it."""
    monkeypatch.setenv("HF_HOME", str(homes["managed"]))
    monkeypatch.setattr(
        pyannote_model_utils,
        "get_bundled_pyannote_models_root",
        lambda: tmp_path / "no-bundled-models",
    )
    repo = _personal_hf_model(homes["home"], "models--pyannote--segmentation-3.0")
    snapshot = repo / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (repo / "refs").mkdir()
    (repo / "refs" / "main").write_text("abc123")
    for name in ("config.yaml", "pytorch_model.bin"):
        (snapshot / name).write_text("x")

    with pytest.raises(ValueError, match="outside Nojoin's model cache"):
        preload_models.delete_model("segmentation")

    assert (snapshot / "pytorch_model.bin").exists()


def test_a_model_in_the_managed_cache_is_still_deleted(homes, monkeypatch):
    monkeypatch.setenv("HF_HOME", str(homes["managed"]))
    model = write_onnx_asr_repo(homes["managed"] / "hub", "parakeet")

    assert preload_models.delete_model("parakeet") is True
    assert not model.exists()
