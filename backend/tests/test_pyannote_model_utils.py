from __future__ import annotations

from pathlib import Path

from backend.utils.pyannote_model_utils import (
    get_bundled_pyannote_models_root,
    is_repo_bundled_pyannote_path,
    resolve_local_pyannote_model,
)


def test_resolve_local_pyannote_model_prefers_bundled_dir(monkeypatch, tmp_path) -> None:
    bundled_root = tmp_path / "bundled"
    model_dir = bundled_root / "speaker-diarization-community-1"
    (model_dir / "segmentation").mkdir(parents=True)
    (model_dir / "embedding").mkdir(parents=True)
    (model_dir / "plda").mkdir(parents=True)
    (model_dir / "config.yaml").write_text("pipeline: {}\n", encoding="utf-8")
    (model_dir / "segmentation" / "pytorch_model.bin").write_bytes(b"seg")
    (model_dir / "embedding" / "pytorch_model.bin").write_bytes(b"emb")
    (model_dir / "plda" / "plda.npz").write_bytes(b"plda")
    (model_dir / "plda" / "xvec_transform.npz").write_bytes(b"xvec")

    monkeypatch.setenv("NOJOIN_PYANNOTE_MODELS_DIR", str(bundled_root))
    resolved = resolve_local_pyannote_model("pyannote/speaker-diarization-community-1")

    assert resolved.source == "bundled"
    assert resolved.load_ref == str(model_dir)
    assert resolved.path == str(model_dir)
    assert resolved.checked_paths == [str(model_dir)]


def test_resolve_local_pyannote_model_uses_hf_cache_snapshot(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("NOJOIN_PYANNOTE_MODELS_DIR", str(tmp_path / "empty-bundled"))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf-home"))

    cache_root = tmp_path / "hf-home" / "hub"
    snapshot_dir = cache_root / "models--pyannote--segmentation-3.0" / "snapshots" / "abc123"
    (snapshot_dir / "config.yaml").parent.mkdir(parents=True)
    (snapshot_dir / "config.yaml").write_text("model: {}\n", encoding="utf-8")
    (snapshot_dir / "pytorch_model.bin").write_bytes(b"weights")

    resolved = resolve_local_pyannote_model("pyannote/segmentation-3.0")

    assert resolved.source == "cache"
    assert resolved.load_ref == str(snapshot_dir)
    assert resolved.path == str(snapshot_dir)
    assert str(cache_root / "models--pyannote--segmentation-3.0") in resolved.checked_paths


def test_is_repo_bundled_pyannote_path(monkeypatch, tmp_path) -> None:
    bundled_root = tmp_path / "bundled"
    monkeypatch.setenv("NOJOIN_PYANNOTE_MODELS_DIR", str(bundled_root))

    model_path = bundled_root / "wespeaker-voxceleb-resnet34-LM"
    model_path.mkdir(parents=True)

    assert get_bundled_pyannote_models_root() == bundled_root
    assert is_repo_bundled_pyannote_path(str(model_path))
    assert not is_repo_bundled_pyannote_path(str(tmp_path / "elsewhere"))
