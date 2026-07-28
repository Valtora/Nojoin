"""Detecting cached ONNX ASR models on disk.

The status heuristic matches Hugging Face cache directory names. It has to match
the *repo* name rather than the Nojoin model id, because the two diverge:
onnx-asr caches `nemo-canary-1b-v2` as `models--istupakov--canary-1b-v2-onnx`.
Matching the Nojoin id reported Canary as missing however many times it was
prepared, and made it undeletable with it, since deletion resolves its path
through this same check.
"""

from __future__ import annotations

import pytest

from backend.preload_models import check_model_status

# The directory names a real install ends up with, taken from a live cache.
CACHED_REPOS = (
    "models--istupakov--canary-1b-v2-onnx",
    "models--istupakov--parakeet-tdt-0.6b-v3-onnx",
)


@pytest.mark.parametrize("model", ["parakeet", "canary"])
def test_a_downloaded_onnx_model_is_reported_as_present(model, monkeypatch, tmp_path):
    hub = tmp_path / "hub"
    hub.mkdir()
    for repo in CACHED_REPOS:
        (hub / repo).mkdir()
    monkeypatch.setenv("HF_HOME", str(tmp_path))

    status = check_model_status(whisper_model_size="turbo")

    assert status[model]["downloaded"] is True
    assert status[model]["path"].startswith(str(hub))


@pytest.mark.parametrize("model", ["parakeet", "canary"])
def test_an_empty_cache_reports_the_model_as_missing(model, monkeypatch, tmp_path):
    (tmp_path / "hub").mkdir()
    monkeypatch.setenv("HF_HOME", str(tmp_path))

    status = check_model_status(whisper_model_size="turbo")

    assert status[model]["downloaded"] is False
    assert status[model]["path"] is None
