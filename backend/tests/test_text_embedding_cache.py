"""Where the text embedding model is cached.

fastembed's default cache sits under the system temp dir, which compose keeps
private to each container and discards on recreate, so every image update
downloaded the embedding model again. It belongs in the persistent model cache
with the other models.
"""

from __future__ import annotations

import sys
import types

import pytest

from backend.processing import text_embedding


@pytest.fixture
def constructed(monkeypatch) -> list[dict]:
    """Capture every TextEmbedding construction instead of loading a model."""
    calls: list[dict] = []

    def fake_text_embedding(**kwargs):
        calls.append(kwargs)
        return object()

    stub = types.ModuleType("fastembed")
    stub.TextEmbedding = fake_text_embedding
    monkeypatch.setitem(sys.modules, "fastembed", stub)
    monkeypatch.setattr(text_embedding, "gpu_is_present", lambda: False)
    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
    return calls


def test_model_is_cached_under_the_persistent_model_cache(
    constructed, monkeypatch, tmp_path
):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    text_embedding.TextEmbeddingService()

    assert constructed[0]["cache_dir"] == str(tmp_path / "fastembed")


def test_without_xdg_cache_home_the_cache_follows_home(
    constructed, monkeypatch, tmp_path
):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    text_embedding.TextEmbeddingService()

    assert constructed[0]["cache_dir"] == str(tmp_path / ".cache" / "fastembed")


def test_fastembed_cache_path_overrides_the_default(constructed, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "explicit"))

    text_embedding.TextEmbeddingService()

    assert constructed[0]["cache_dir"] == str(tmp_path / "explicit")


def test_cpu_fallback_uses_the_same_cache(constructed, monkeypatch, tmp_path):
    """The retry after a failed first load must not fall back to /tmp either."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    def failing_verify(*args, **kwargs):
        raise RuntimeError("provider check failed")

    monkeypatch.setattr(text_embedding, "verify_gpu_providers", failing_verify)

    text_embedding.TextEmbeddingService()

    assert len(constructed) == 2
    assert constructed[1]["cache_dir"] == str(tmp_path / "fastembed")
