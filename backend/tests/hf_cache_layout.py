"""Hugging Face hub cache layouts on a real filesystem, for the model cache tests.

Lays a repo out the way huggingface_hub does on Linux: content-addressed files
under ``blobs/``, a ``snapshots/<commit>/`` tree of relative symlinks into
them, and ``refs/main`` naming the commit.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from backend.utils.model_cache_paths import hf_repo_dirname
from backend.utils.onnx_asr_cache import ONNX_ASR_MODELS

COMMIT = "0123456789abcdef0123456789abcdef01234567"


def write_hf_repo(
    hub: Path, repo_id: str, files: dict[str, bytes], *, ref: bool = True
) -> Path:
    """Cache ``files`` (snapshot-relative path -> content) for ``repo_id``.

    Returns the repo directory. ``ref=False`` leaves out ``refs/main``.
    """
    repo = hub / hf_repo_dirname(repo_id)
    blobs = repo / "blobs"
    snapshot = repo / "snapshots" / COMMIT
    blobs.mkdir(parents=True, exist_ok=True)
    snapshot.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        blob = blobs / hashlib.sha256(content).hexdigest()
        blob.write_bytes(content)
        link = snapshot / name
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            link.unlink()
        link.symlink_to(os.path.relpath(blob, link.parent))
    if ref:
        (repo / "refs").mkdir(exist_ok=True)
        (repo / "refs" / "main").write_text(COMMIT)
    return repo


def share_blob(hub: Path, repo: Path, name: str) -> Path:
    """Move one cached file's blob into the hub cache's shared store.

    This is the layout a Xet download leaves (huggingface_hub 1.32.0,
    ``utils/_shared_blobs.py``): the payload at ``<hub>/blobs/<xx>/<hash>``,
    read-only (``_shared_blob_mode``, :192); the store marked by
    ``.huggingface-shared-blobs`` holding ``1\\n`` (:46-48, :146-166); a
    ``<hash>.refs`` manifest naming each repo blob that links to it, relative
    to the hub cache (:246-261, :352-371); and the repo's ``blobs/<etag>``
    turned into a relative symlink to the payload (:374-378, :432-484). A
    second repo with the same file links to the payload already there
    (:386-418). Returns the payload path.
    """
    snapshot_link = repo / "snapshots" / COMMIT / name
    blob = Path(os.path.normpath(snapshot_link.parent / os.readlink(snapshot_link)))
    content = blob.read_bytes()
    xet_hash = hashlib.sha256(b"xet:" + content).hexdigest()
    store = hub / "blobs"
    store.mkdir(exist_ok=True)
    (store / ".huggingface-shared-blobs").write_text("1\n")
    payload = store / xet_hash[:2] / xet_hash
    payload.parent.mkdir(exist_ok=True)
    if payload.exists():
        blob.unlink()
    else:
        blob.replace(payload)
        payload.chmod(0o444)
    with (payload.parent / f"{xet_hash}.refs").open("a") as manifest:
        manifest.write(f"{blob.relative_to(hub).as_posix()}\n")
    blob.symlink_to(os.path.relpath(payload, blob.parent))
    return payload


def onnx_asr_files(status_key: str, quantization: str | None) -> dict[str, bytes]:
    """The files the loader opens for one model at one precision."""
    model = ONNX_ASR_MODELS[status_key]
    return {
        pattern.replace("?", "."): pattern.encode()
        for pattern in model.required_files(quantization)
    }


def write_onnx_asr_repo(
    hub: Path, status_key: str, quantization: str | None = "int8"
) -> Path:
    """A complete onnx-asr download of one model at one precision."""
    return write_hf_repo(
        hub,
        ONNX_ASR_MODELS[status_key].repo_id,
        onnx_asr_files(status_key, quantization),
    )


PYANNOTE_EMBEDDING = "pyannote/wespeaker-voxceleb-resnet34-LM"


def write_pyannote_embedding(hub: Path) -> Path:
    """A complete cached copy of the Pyannote voice embedding model."""
    return write_hf_repo(
        hub,
        PYANNOTE_EMBEDDING,
        {"config.yaml": b"model: {}\n", "pytorch_model.bin": b"weights"},
    )
