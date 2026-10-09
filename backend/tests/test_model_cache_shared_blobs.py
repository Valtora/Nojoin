"""Deleting a Hugging Face model frees the weights it shares through the hub cache.

huggingface_hub keeps a file downloaded through Xet once per hub cache, in
``<hub>/blobs/<xx>/<hash>``, and each repo's ``blobs/<etag>`` is a symlink to
it. Removing a repo directory removed only those links, so deleting Parakeet
left its weights on disk and a re-download linked them back at once.
"""

from __future__ import annotations

import os
import shutil
from hashlib import sha256
from pathlib import Path

import pytest

from backend import preload_models
from backend.tests.hf_cache_layout import (
    COMMIT,
    PYANNOTE_EMBEDDING,
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
    by commit hash could take the sibling's snapshot instead of Parakeet's.
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


@pytest.fixture
def outside(tmp_path) -> Path:
    """A directory beside the hub cache, standing in for files kept elsewhere."""
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    return outside


def test_a_snapshot_link_to_a_file_outside_the_repo_keeps_that_file(hub, outside):
    repo = write_onnx_asr_repo(hub, "parakeet")
    kept = outside / "notes.bin"
    kept.write_bytes(b"not the model's")
    (repo / "snapshots" / COMMIT / "extra.bin").symlink_to(kept)

    assert preload_models.delete_model("parakeet") is True

    assert not repo.exists()
    assert kept.read_bytes() == b"not the model's"


def test_a_snapshot_directory_linked_elsewhere_keeps_its_files(hub, outside):
    """A whole snapshot placed by hand: snapshots/<commit> -> a directory elsewhere."""
    files = onnx_asr_files("parakeet", "int8")
    for name, content in files.items():
        (outside / name).write_bytes(content)
    repo = hub / hf_repo_dirname(PARAKEET)
    (repo / "snapshots").mkdir(parents=True)
    (repo / "snapshots" / COMMIT).symlink_to(outside, target_is_directory=True)
    (repo / "refs").mkdir()
    (repo / "refs" / "main").write_text(COMMIT)
    status = preload_models.check_model_status(whisper_model_size="turbo")
    assert status["parakeet"]["downloaded"] is True

    assert preload_models.delete_model("parakeet") is True

    assert not repo.exists()
    assert all(
        (outside / name).read_bytes() == content for name, content in files.items()
    )


def test_a_snapshot_directory_linked_to_a_copy_elsewhere_keeps_the_copy(hub, outside):
    """snapshots/<commit> -> the same snapshot in a copy of the repo kept elsewhere.

    The copy's entries are relative links, which huggingface_hub reads as
    naming this repo's own blobs, so the blobs alone look like they belong here.
    """
    repo = write_onnx_asr_repo(hub, "parakeet")
    copy = outside / repo.name
    shutil.copytree(repo, copy, symlinks=True)
    shutil.rmtree(repo / "snapshots" / COMMIT)
    (repo / "snapshots" / COMMIT).symlink_to(
        copy / "snapshots" / COMMIT, target_is_directory=True
    )

    assert preload_models.delete_model("parakeet") is True

    assert not repo.exists()
    copied = copy / "snapshots" / COMMIT
    files = onnx_asr_files("parakeet", "int8")
    assert all(
        (copied / name).read_bytes() == content for name, content in files.items()
    )


def test_sideloaded_pyannote_weights_kept_elsewhere_survive(
    hub, outside, monkeypatch, tmp_path
):
    """An offline install linking the weights file to a copy on another disk."""
    monkeypatch.setenv("NOJOIN_PYANNOTE_MODELS_DIR", str(tmp_path / "no-bundled"))
    repo = write_hf_repo(hub, PYANNOTE_EMBEDDING, {"config.yaml": b"model: {}\n"})
    weights = outside / "pytorch_model.bin"
    weights.write_bytes(b"weights")
    (repo / "snapshots" / COMMIT / "pytorch_model.bin").symlink_to(weights)
    status = preload_models.check_model_status(whisper_model_size="turbo")
    assert status["embedding"]["downloaded"] is True

    assert preload_models.delete_model("embedding") is True

    assert not repo.exists()
    assert weights.read_bytes() == b"weights"


def test_a_snapshot_link_into_another_repos_blobs_keeps_that_blob(hub):
    repo = write_onnx_asr_repo(hub, "parakeet")
    sibling = write_hf_repo(
        hub, "istupakov/parakeet-tdt-0.6b-v2-onnx", {"vocab.txt": b"v2"}
    )
    # In no snapshot of the sibling, so no other cached file keeps it.
    stray = sibling / "blobs" / "0f1e2d"
    stray.write_bytes(b"the sibling's")
    snapshot = repo / "snapshots" / COMMIT
    (snapshot / "x.bin").symlink_to(os.path.relpath(stray, snapshot))

    assert preload_models.delete_model("parakeet") is True

    assert not repo.exists()
    assert stray.read_bytes() == b"the sibling's"


def test_a_repo_with_a_link_outside_still_frees_its_shared_weights(hub, outside):
    repo, payloads = _shared_parakeet(hub)
    kept = outside / "notes.bin"
    kept.write_bytes(b"not the model's")
    (repo / "snapshots" / COMMIT / "extra.bin").symlink_to(kept)

    assert preload_models.delete_model("parakeet") is True

    assert not repo.exists()
    assert kept.exists()
    assert not any(payload.exists() for payload in payloads)


def test_an_unreadable_unrelated_repo_does_not_block_deletion(hub, caplog):
    """The scan reads every repo in the cache; one it cannot read raises OSError.

    A ref naming nothing raises it for any user, root included, as root-owned
    files left by a sudo run in a personal cache do for everyone else.
    """
    repo, _ = _shared_parakeet(hub)
    unrelated = write_hf_repo(hub, "someone/else", {"a.txt": b"a"}, ref=False)
    (unrelated / "refs").mkdir()
    (unrelated / "refs" / "main").symlink_to(unrelated / "refs" / "gone")

    assert preload_models.delete_model("parakeet") is True

    assert not repo.exists()
    assert (unrelated / "snapshots" / COMMIT / "a.txt").read_bytes() == b"a"
    assert "hf cache prune" in caplog.text


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
