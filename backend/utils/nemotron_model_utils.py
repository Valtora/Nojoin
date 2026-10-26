"""Inspect, verify and prepare native speaker assets without ML imports.

Inspection describes the last completed verification and detects metadata
changes. Loading must independently hash the actual bytes through verify.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from backend.utils.model_cache_paths import hf_hub_cache_root, hf_repo_dirname
from backend.utils.nemotron_download import download_asset
from backend.utils.nemotron_lock import atomic_json, clear_pending, model_lock
from backend.utils.nemotron_manifest import (
    FILENAMES,
    REPOSITORY,
    REVISION,
    NemotronModelError,
    load_asset_manifest,
    manifest_sha256,
    read_json,
    validate_geometry,
)

__all__ = [
    "NemotronModelError",
    "NemotronModelResolution",
    "inspect_nemotron_model",
    "verify_nemotron_model",
    "prepare_nemotron_model",
    "load_asset_manifest",
    "manifest_sha256",
    "nemotron_repo_path",
]


@dataclass(frozen=True)
class NemotronModelResolution:
    state: Literal["missing", "partial", "verified", "invalid"]
    path: Path | None
    revision: str
    manifest_sha256: str
    error: str | None


def nemotron_repo_path() -> Path:
    """Resolve only the managed cache, never personal fallback snapshots."""
    return Path(hf_hub_cache_root()) / hf_repo_dirname(REPOSITORY)


def _snapshot() -> Path:
    return nemotron_repo_path() / "snapshots" / REVISION


def _metadata(path: Path) -> dict[str, dict[str, int]]:
    """Use target-file metadata through ordinary HF blob symlinks."""
    _check_snapshot_files(path)
    result = {}
    for entry in load_asset_manifest()["files"]:
        target = path / entry["filename"]
        stat = target.stat()
        if not target.is_file() or stat.st_size != entry["size"]:
            raise ValueError("Native speaker asset size changed")
        result[entry["filename"]] = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "ctime_ns": stat.st_ctime_ns,
            "inode": stat.st_ino,
        }
    return result


def _check_snapshot_files(path: Path) -> None:
    if {entry.name for entry in path.iterdir()} != FILENAMES:
        raise ValueError("Native speaker snapshot must contain only manifest files")


def inspect_nemotron_model() -> NemotronModelResolution:
    """Read receipt/metadata only; never write, download or hash weights."""
    identity = manifest_sha256()
    repo, snapshot = nemotron_repo_path(), _snapshot()
    if not repo.exists() and not repo.is_symlink():
        return NemotronModelResolution("missing", None, REVISION, identity, None)
    receipt_path = repo / "nojoin-verification.json"
    if not receipt_path.exists():
        return NemotronModelResolution(
            "partial", None, REVISION, identity, "receipt_missing"
        )
    try:
        receipt = read_json(receipt_path)
        expected = {
            "schema_version": 1,
            "revision": REVISION,
            "manifest_sha256": identity,
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise ValueError("Receipt identity changed")
        if receipt.get("files") != _metadata(snapshot):
            raise ValueError("Asset metadata changed")
        return NemotronModelResolution("verified", snapshot, REVISION, identity, None)
    except (OSError, ValueError, KeyError, TypeError):
        return NemotronModelResolution(
            "invalid", None, REVISION, identity, "receipt_or_assets_changed"
        )


def _file_matches(path: Path, entry: dict[str, Any]) -> bool:
    if not path.is_file() or path.stat().st_size != entry["size"]:
        return False
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest() == entry["sha256"]


def verify_nemotron_model(path: Path) -> None:
    """Hash every file and validate native JSON before process-first load."""
    try:
        _check_snapshot_files(path)
        for entry in load_asset_manifest()["files"]:
            if not _file_matches(path / entry["filename"], entry):
                raise ValueError("Native speaker asset does not match manifest")
        validate_geometry(path)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise NemotronModelError("Native speaker assets failed verification") from exc


def prepare_nemotron_model(
    *, progress: Callable[[str, int], None] | None = None
) -> Path:
    """Prepare only immutable native assets and publish a verified receipt."""
    report = progress or (lambda message, percent: None)
    with model_lock():
        clear_pending(None)  # Explicit repair may remove only a stale marker.
        snapshot = _snapshot()
        manifest = load_asset_manifest()
        # Even a receipt cannot substitute for byte verification in a worker.
        if inspect_nemotron_model().state == "verified":
            try:
                verify_nemotron_model(snapshot)
                report("Native speaker assets are verified.", 100)
                return snapshot
            except NemotronModelError:
                pass
        _prepare_files(snapshot, manifest, report)
        return snapshot


def _prepare_files(
    path: Path, manifest: dict[str, Any], report: Callable[[str, int], None]
) -> None:
    receipt = nemotron_repo_path() / "nojoin-verification.json"
    receipt.unlink(missing_ok=True)
    entries = manifest["files"]
    for index, entry in enumerate(entries):
        target = path / entry["filename"]
        if not _file_matches(target, entry):
            report("Preparing native speaker assets...", int(index * 95 / len(entries)))
            download_asset(target)
    try:
        before = _metadata(path)
        verify_nemotron_model(path)
        if before != _metadata(path):
            raise NemotronModelError(
                "Native speaker assets changed during verification"
            )
    except (OSError, ValueError) as exc:
        raise NemotronModelError("Native speaker assets failed verification") from exc
    atomic_json(
        receipt,
        {
            "schema_version": 1,
            "revision": REVISION,
            "manifest_sha256": manifest_sha256(),
            "files": before,
        },
    )
    report("Native speaker assets are verified.", 100)
