"""The pyannote/segmentation-3.0 loader shared by overlap analytics and preparation.

Overlap analytics imports the loader from ``segmentation_model`` when it runs
(test_analytics_webm_audio patches ``segmentation_model.load_segmentation_model``
for that path). These tests pin the loader
itself, the preparation step that validates the bundled copy, and the two
release hooks that find the cache by module name, where a stale name would
skip the release without an error.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from backend import celery_app as celery_app_module
from backend import preload_models
from backend.processing import segmentation_model

BUNDLED_ROOT = Path(__file__).resolve().parents[2] / "bundled_models" / "pyannote"
BUNDLED_SEGMENTATION = BUNDLED_ROOT / "segmentation-3.0"


class _FakeModel:
    def __init__(self, ref: str, token: str | None) -> None:
        self.ref = ref
        self.token = token
        self.device: str | None = None

    def to(self, device) -> _FakeModel:
        self.device = str(device)
        return self


@pytest.fixture
def loads(monkeypatch, model_cache_env) -> list[tuple[str, str | None]]:
    """Stub pyannote's Model and record every from_pretrained call."""
    calls: list[tuple[str, str | None]] = []

    def from_pretrained(ref: str, token: str | None = None) -> _FakeModel:
        calls.append((ref, token))
        return _FakeModel(ref, token)

    audio = types.ModuleType("pyannote.audio")
    audio.Model = types.SimpleNamespace(from_pretrained=from_pretrained)
    monkeypatch.setitem(sys.modules, "pyannote", types.ModuleType("pyannote"))
    monkeypatch.setitem(sys.modules, "pyannote.audio", audio)
    monkeypatch.setenv("NOJOIN_PYANNOTE_MODELS_DIR", str(BUNDLED_ROOT))
    monkeypatch.setattr(segmentation_model, "_model_cache", {})
    return calls


def test_the_bundled_model_is_cached_per_device_until_released(loads) -> None:
    first = segmentation_model.load_segmentation_model("cpu")
    again = segmentation_model.load_segmentation_model("cpu")
    other_device = segmentation_model.load_segmentation_model("meta")

    assert again is first
    assert first.device == "cpu" and other_device.device == "meta"
    assert loads == [(str(BUNDLED_SEGMENTATION), None)] * 2

    segmentation_model.release_segmentation_model_cache()
    reloaded = segmentation_model.load_segmentation_model("cpu")

    assert reloaded is not first
    assert len(loads) == 3


def test_without_a_local_copy_the_loader_needs_a_token(
    loads, monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("NOJOIN_PYANNOTE_MODELS_DIR", str(tmp_path / "none"))
    monkeypatch.setattr(segmentation_model.config_manager, "get", lambda key: None)

    with pytest.raises(ValueError, match="hf_token"):
        segmentation_model.load_segmentation_model("cpu")

    model = segmentation_model.load_segmentation_model("cpu", "hf-secret")

    assert loads == [("pyannote/segmentation-3.0", "hf-secret")]
    assert model.token == "hf-secret"


def test_model_preparation_loads_the_bundled_segmentation_model(
    loads, monkeypatch
) -> None:
    for module_name, loader in (
        ("backend.processing.diarize", "load_diarization_pipeline"),
        ("backend.processing.embedding_core", "load_embedding_model"),
    ):
        stub = types.ModuleType(module_name)
        setattr(stub, loader, lambda device, token: object())
        monkeypatch.setitem(sys.modules, module_name, stub)

    preload_models._prepare_pyannote_models(None)

    assert loads == [(str(BUNDLED_SEGMENTATION), None)]


def _release_worker_caches(monkeypatch) -> None:
    monkeypatch.setattr(celery_app_module, "_should_release_model_caches", lambda: True)
    celery_app_module.release_worker_model_caches()


@pytest.mark.parametrize(
    "release",
    [
        pytest.param(
            lambda monkeypatch: preload_models._release_validation_caches(),
            id="after-preparation",
        ),
        pytest.param(_release_worker_caches, id="after-each-worker-task"),
    ],
)
def test_the_release_hooks_empty_the_segmentation_cache(monkeypatch, release) -> None:
    cache = {("bundled", "cpu"): object()}
    monkeypatch.setattr(segmentation_model, "_model_cache", cache)

    release(monkeypatch)

    assert cache == {}
