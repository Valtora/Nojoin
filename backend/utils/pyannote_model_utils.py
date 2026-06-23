from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from backend.utils.path_manager import path_manager

PYANNOTE_MODEL_ID_TO_DIRNAME = {
    "pyannote/speaker-diarization-community-1": "speaker-diarization-community-1",
    "pyannote/wespeaker-voxceleb-resnet34-LM": "wespeaker-voxceleb-resnet34-LM",
    "pyannote/segmentation-3.0": "segmentation-3.0",
}

PYANNOTE_BUNDLED_REQUIRED_FILES = {
    "pyannote/speaker-diarization-community-1": (
        "config.yaml",
        "segmentation/pytorch_model.bin",
        "embedding/pytorch_model.bin",
        "plda/plda.npz",
        "plda/xvec_transform.npz",
    ),
    "pyannote/wespeaker-voxceleb-resnet34-LM": (
        "config.yaml",
        "pytorch_model.bin",
    ),
    "pyannote/segmentation-3.0": (
        "config.yaml",
        "pytorch_model.bin",
    ),
}


@dataclass(frozen=True)
class PyannoteModelResolution:
    model_id: str
    source: str
    load_ref: str
    path: str | None
    checked_paths: list[str]


def get_bundled_pyannote_models_root() -> Path:
    configured = os.getenv("NOJOIN_PYANNOTE_MODELS_DIR", "").strip()
    if configured:
        configured_path = Path(configured).expanduser()
        if not configured_path.is_absolute():
            configured_path = path_manager.executable_directory / configured_path
        return configured_path

    return path_manager.executable_directory / "bundled_models" / "pyannote"


def _model_dirname(model_id: str) -> str | None:
    return PYANNOTE_MODEL_ID_TO_DIRNAME.get(model_id)


def _required_files(model_id: str) -> tuple[str, ...]:
    return PYANNOTE_BUNDLED_REQUIRED_FILES.get(model_id, ("config.yaml",))


def _looks_complete_model_dir(path: Path, model_id: str) -> bool:
    return path.is_dir() and all(
        (path / rel_path).exists() for rel_path in _required_files(model_id)
    )


def get_bundled_pyannote_model_dir(model_id: str) -> Path | None:
    dirname = _model_dirname(model_id)
    if dirname is None:
        return None
    return get_bundled_pyannote_models_root() / dirname


def _hf_cache_roots() -> list[Path]:
    roots: list[Path] = []

    hf_home = os.getenv("HF_HOME", "").strip()
    if hf_home:
        roots.append(Path(hf_home).expanduser() / "hub")

    roots.append(Path.home() / ".cache" / "huggingface" / "hub")

    seen: set[str] = set()
    deduped: list[Path] = []
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(root)
    return deduped


def _hf_cache_repo_dir(model_id: str, cache_root: Path) -> Path:
    return cache_root / f"models--{model_id.replace('/', '--')}"


def _resolve_snapshot_dir(cache_repo_dir: Path) -> Path | None:
    refs_main = cache_repo_dir / "refs" / "main"
    snapshots_dir = cache_repo_dir / "snapshots"

    if refs_main.exists() and snapshots_dir.is_dir():
        try:
            ref = refs_main.read_text(encoding="utf-8").strip()
        except OSError:
            ref = ""
        if ref:
            candidate = snapshots_dir / ref
            if candidate.is_dir():
                return candidate

    if snapshots_dir.is_dir():
        snapshot_dirs = sorted(
            (path for path in snapshots_dir.iterdir() if path.is_dir()), reverse=True
        )
        if snapshot_dirs:
            return snapshot_dirs[0]

    return None


def resolve_local_pyannote_model(model_id: str) -> PyannoteModelResolution:
    checked_paths: list[str] = []

    bundled_dir = get_bundled_pyannote_model_dir(model_id)
    if bundled_dir is not None:
        checked_paths.append(str(bundled_dir))
        if _looks_complete_model_dir(bundled_dir, model_id):
            return PyannoteModelResolution(
                model_id=model_id,
                source="bundled",
                load_ref=str(bundled_dir),
                path=str(bundled_dir),
                checked_paths=checked_paths,
            )

    for cache_root in _hf_cache_roots():
        cache_repo_dir = _hf_cache_repo_dir(model_id, cache_root)
        checked_paths.append(str(cache_repo_dir))
        snapshot_dir = _resolve_snapshot_dir(cache_repo_dir)
        if snapshot_dir and _looks_complete_model_dir(snapshot_dir, model_id):
            return PyannoteModelResolution(
                model_id=model_id,
                source="cache",
                load_ref=str(snapshot_dir),
                path=str(snapshot_dir),
                checked_paths=checked_paths,
            )

    return PyannoteModelResolution(
        model_id=model_id,
        source="remote",
        load_ref=model_id,
        path=None,
        checked_paths=checked_paths,
    )


def is_repo_bundled_pyannote_path(path: str | None) -> bool:
    if not path:
        return False

    bundled_root = get_bundled_pyannote_models_root()
    try:
        resolved_path = Path(path).resolve()
        return resolved_path.is_relative_to(bundled_root.resolve())
    except (OSError, ValueError):
        return False
