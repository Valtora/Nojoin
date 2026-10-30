"""Verified receipts and Hub transfers shared by pinned native models."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from backend.utils.model_cache_paths import hf_hub_cache_root, hf_repo_dirname
from backend.utils.pinned_model_control import (
    PinnedModelControl,
    atomic_json,
    read_json,
)


@dataclass(frozen=True)
class PinnedModelResolution:
    state: Literal["missing", "partial", "verified", "invalid"]
    path: Path | None
    revision: str
    manifest_sha256: str
    error: str | None


def file_matches(path: Path, entry: dict[str, Any]) -> bool:
    """Hash bytes through ordinary Hub blob symlinks."""
    if not path.is_file() or path.stat().st_size != entry["size"]:
        return False
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest() == entry["sha256"]


@dataclass(frozen=True)
class PinnedModelAssets:
    """Apply an immutable manifest and model-specific metadata validator."""

    control: PinnedModelControl
    manifest: Callable[[], dict[str, Any]]
    validate: Callable[[Path], None]
    error_type: type[RuntimeError]
    label: str
    discardable_files: tuple[str, ...] = ()

    def repo_path(self) -> Path:
        return Path(hf_hub_cache_root()) / hf_repo_dirname(
            self.manifest()["repository"]
        )

    def snapshot(self) -> Path:
        return self.repo_path() / "snapshots" / self.manifest()["revision"]

    def metadata(self, path: Path) -> dict[str, dict[str, int]]:
        entries = self.manifest()["files"]
        if {entry.name for entry in path.iterdir()} != {
            entry["filename"] for entry in entries
        }:
            raise ValueError("Snapshot must contain only manifest files")
        result = {}
        for entry in entries:
            target = path / entry["filename"]
            stat = target.stat()
            if not target.is_file() or stat.st_size != entry["size"]:
                raise ValueError("Asset size changed")
            result[entry["filename"]] = {
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "ctime_ns": stat.st_ctime_ns,
                "inode": stat.st_ino,
            }
        return result

    def inspect(self) -> PinnedModelResolution:
        """Inspect receipts read-only, without downloading or weight hashing."""
        identity = self.control.manifest_sha256()
        revision = self.manifest()["revision"]
        repo, snapshot = self.repo_path(), self.snapshot()
        if not repo.exists() and not repo.is_symlink():
            return PinnedModelResolution("missing", None, revision, identity, None)
        receipt_path = repo / "nojoin-verification.json"
        if not receipt_path.exists():
            return PinnedModelResolution(
                "partial", None, revision, identity, "receipt_missing"
            )
        try:
            receipt = read_json(receipt_path)
            expected = {
                "schema_version": 1,
                "revision": revision,
                "manifest_sha256": identity,
            }
            if any(receipt.get(key) != value for key, value in expected.items()):
                raise ValueError("Receipt identity changed")
            if receipt.get("files") != self.metadata(snapshot):
                raise ValueError("Asset metadata changed")
            return PinnedModelResolution("verified", snapshot, revision, identity, None)
        except (OSError, ValueError, KeyError, TypeError):
            return PinnedModelResolution(
                "invalid", None, revision, identity, "receipt_or_assets_changed"
            )

    def verify(self, path: Path) -> None:
        """Independently hash every file and refuse unsupported metadata."""
        try:
            self.metadata(path)
            if any(
                not file_matches(path / entry["filename"], entry)
                for entry in self.manifest()["files"]
            ):
                raise ValueError("Asset does not match manifest")
            self.validate(path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise self.error_type(f"{self.label} assets failed verification") from exc

    def prepare(self, *, progress: Callable[[str, int], None] | None = None) -> Path:
        """Repair pinned files and publish a receipt after verification."""
        report = progress or (lambda message, percent: None)
        with self.control.model_lock():
            self.control.clear_pending(None)
            owned = None if self.control.pending_task() else str(uuid4())
            if owned:
                self.control.write_pending(owned)
            try:
                return self._prepare_files(report)
            finally:
                if owned:
                    self.control.clear_pending(owned)

    def _prepare_files(self, report: Callable[[str, int], None]) -> Path:
        path = self.snapshot()
        receipt = self.repo_path() / "nojoin-verification.json"
        receipt.unlink(missing_ok=True)
        for filename in self.discardable_files:
            (path / filename).unlink(missing_ok=True)
        report(f"Preparing {self.label.lower()} assets...", 0)
        entries = self.manifest()["files"]
        for index, entry in enumerate(entries):
            target = path / entry["filename"]
            if not file_matches(target, entry):
                report(
                    f"Preparing {self.label.lower()} assets...",
                    int(index * 95 / len(entries)),
                )
                self.download(target)
        try:
            before = self.metadata(path)
            self.verify(path)
            if before != self.metadata(path):
                raise ValueError("Assets changed during verification")
        except (OSError, ValueError) as exc:
            raise self.error_type(f"{self.label} assets failed verification") from exc
        atomic_json(
            receipt,
            {
                "schema_version": 1,
                "revision": self.manifest()["revision"],
                "manifest_sha256": self.control.manifest_sha256(),
                "files": before,
            },
        )
        self.control.clear_failure()
        report(f"{self.label} assets are verified.", 100)
        return path

    def download(self, destination: Path) -> None:
        """Fetch one pinned file without writing through a snapshot symlink."""
        from httpx import HTTPError
        from huggingface_hub import constants, hf_hub_download

        flag = os.getenv("HF_HUB_OFFLINE")
        offline = (
            flag.upper() in {"1", "ON", "YES", "TRUE"}
            if flag is not None
            else constants.HF_HUB_OFFLINE
        )
        if offline:
            raise self.error_type(f"{self.label} model is not prepared (offline).")
        try:
            repair = destination.exists() or destination.is_symlink()
            destination.unlink(missing_ok=True)
            manifest = self.manifest()
            hf_hub_download(
                repo_id=manifest["repository"],
                filename=destination.name,
                revision=manifest["revision"],
                cache_dir=hf_hub_cache_root(),
                force_download=repair,
                local_files_only=False,
            )
        except (OSError, ValueError, HTTPError) as exc:
            raise self.error_type(f"{self.label} asset transfer failed") from exc
