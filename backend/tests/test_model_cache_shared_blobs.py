"""Deleting a Hugging Face model frees the weights it shares through the hub cache.

huggingface_hub keeps a file downloaded through Xet once per hub cache, in
``<hub>/blobs/<xx>/<hash>``, and each repo's ``blobs/<etag>`` is a symlink to
it. Removing a repo directory removed only those links, so deleting Parakeet
left its weights on disk and a re-download linked them back at once.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from backend import preload_models
from backend.tests.hf_cache_layout import (
    COMMIT,
    onnx_asr_files,
    share_blob,
    write_hf_repo,
    write_onnx_asr_repo,
)
from backend.utils.model_cache_paths import hf_repo_dirname
from backend.utils.onnx_asr_cache import ONNX_ASR_MODELS

PARAKEET = ONNX_ASR_MODELS["parakeet"].repo_id
ENCODER = "encoder-model.int8.onnx"


@pytest.fixture
def hub(model_cache_env, monkeypatch, tmp_path) -> Path:
    """The managed hub cache, empty, with nothing else set."""
    hub = tmp_path / "managed" / "hub"
    hub.mkdir(parents=True)
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))
    return hub


def _shared_parakeet(hub: Path) -> tuple[Path, list[Path]]:
    """A complete Parakeet download with every file in the shared store."""
    repo = write_onnx_asr_repo(hub, "parakeet")
    payloads = [
        share_blob(hub, repo, name) for name in onnx_asr_files("parakeet", "int8")
    ]
    return repo, payloads


def test_deleting_a_model_frees_the_weights_only_it_links_to(hub):
    repo, payloads = _shared_parakeet(hub)

    assert preload_models.delete_model("parakeet") is True

    assert not repo.exists()
    for payload in payloads:
        assert not payload.exists()
        assert not payload.with_name(f"{payload.name}.refs").exists()


def test_weights_another_repo_links_to_survive(hub):
    """Parakeet v2 and v3 ship byte-identical files; one copy serves both.

    The two repos also hold the same commit here, as a mirror would: deleting
    by commit hash across the cache would take the sibling's snapshot too.
    """
    repo, _ = _shared_parakeet(hub)
    content = onnx_asr_files("parakeet", "int8")[ENCODER]
    sibling = write_hf_repo(
        hub, "istupakov/parakeet-tdt-0.6b-v2-onnx", {ENCODER: content}
    )
    payload = share_blob(hub, sibling, ENCODER)

    assert preload_models.delete_model("parakeet") is True

    assert not repo.exists()
    assert payload.exists()
    assert (sibling / "snapshots" / COMMIT / ENCODER).read_bytes() == content
    manifest = payload.with_name(f"{payload.name}.refs").read_text()
    assert manifest.split() == [f"{sibling.name}/blobs/{sha256(content).hexdigest()}"]


def test_a_partial_download_frees_the_file_it_finished(hub):
    """Cut off after vocab.txt: one shared file linked, one blob in flight."""
    repo = write_hf_repo(hub, PARAKEET, {"vocab.txt": b"vocab"})
    payload = share_blob(hub, repo, "vocab.txt")
    (repo / "blobs" / "0f1e2d.incomplete").write_bytes(b"half an encoder")

    status = preload_models.check_model_status(whisper_model_size="turbo")
    assert status["parakeet"]["partial"] is True

    assert preload_models.delete_model("parakeet") is True

    assert not repo.exists()
    assert not payload.exists()


def test_a_download_cut_off_before_its_first_file_is_cleared(hub):
    """snapshot_download writes refs and its file listing before any file.

    With no snapshots directory huggingface_hub does not list the repo, so it
    is removed as a directory.
    """
    repo = hub / hf_repo_dirname(PARAKEET)
    (repo / "refs").mkdir(parents=True)
    (repo / "refs" / "main").write_text(COMMIT)
    (repo / "trees").mkdir()
    (repo / "trees" / f"{COMMIT}.json").write_text("[]")
    (repo / "blobs").mkdir()
    (repo / "blobs" / "0f1e2d.incomplete").write_bytes(b"half an encoder")

    assert preload_models.delete_model("parakeet") is True

    assert not repo.exists()
    status = preload_models.check_model_status(whisper_model_size="turbo")
    assert "partial" not in status["parakeet"]


def test_a_copy_in_another_hub_cache_keeps_its_weights(hub, model_cache_env):
    """The personal cache has its own shared store; deletion never reaches it."""
    personal_hub = model_cache_env / ".cache" / "huggingface" / "hub"
    personal_repo, personal_payloads = _shared_parakeet(personal_hub)
    repo, payloads = _shared_parakeet(hub)

    assert preload_models.delete_model("parakeet") is True

    assert not repo.exists()
    assert not any(payload.exists() for payload in payloads)
    assert (personal_repo / "refs" / "main").exists()
    assert all(payload.exists() for payload in personal_payloads)


def test_the_segmentation_model_is_deleted_with_its_weights(hub, monkeypatch, tmp_path):
    monkeypatch.setenv("NOJOIN_PYANNOTE_MODELS_DIR", str(tmp_path / "no-bundled"))
    repo = write_hf_repo(
        hub,
        "pyannote/segmentation-3.0",
        {"config.yaml": b"model: {}\n", "pytorch_model.bin": b"segmentation"},
    )
    payload = share_blob(hub, repo, "pytorch_model.bin")

    status = preload_models.check_model_status(whisper_model_size="turbo")
    assert status["segmentation"]["downloaded"] is True

    assert preload_models.delete_model("segmentation") is True

    assert not repo.exists()
    assert not payload.exists()
