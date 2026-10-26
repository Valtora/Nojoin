"""File-based coordination for independently prepared immutable models."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.utils.model_cache_paths import hf_hub_cache_root

PENDING_TTL = 30 * 60
logger = logging.getLogger(__name__)


def read_json(path: Path) -> dict[str, Any]:
    """Read bounded metadata without importing model dependencies."""
    with path.open("rb") as source:
        data = source.read(65537)
    if len(data) > 65536:
        raise ValueError("Asset metadata is too large")
    parsed = json.loads(data)
    if not isinstance(parsed, dict):
        raise ValueError("Asset metadata must be an object")
    return parsed


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    """Durably write metadata before atomically publishing its name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".nojoin-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, sort_keys=True, separators=(",", ":"))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@dataclass(frozen=True)
class PinnedModelControl:
    """Coordinate one trusted identity outside its deletable repository."""

    name: str
    manifest_sha256: Callable[[], str]

    def path(self, filename: str) -> Path:
        """Resolve a control file in this model's stable namespace."""
        return Path(hf_hub_cache_root()) / ".locks" / f"nojoin-{self.name}" / filename

    @contextmanager
    def model_lock(self, *, blocking: bool = True) -> Iterator[bool]:
        """Lock a stable inode; deletion never unlinks the lock file."""
        import fcntl

        lock = self.path("prepare.lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        with lock.open("a+b") as handle:
            acquired = False
            try:
                try:
                    fcntl.flock(
                        handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
                    )
                    acquired = True
                except BlockingIOError:
                    pass
                yield acquired
            finally:
                if acquired:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def pending_task(self) -> dict[str, Any] | None:
        """Inspect atomic state read-only; mutations require the model lock."""
        try:
            marker = read_json(self.path("pending.json"))
            age = time.time() - float(marker["created_at"])
            if (
                marker.get("schema_version") == 1
                and marker["manifest_sha256"] == self.manifest_sha256()
                and isinstance(marker["task_id"], str)
                and 0 <= age < PENDING_TTL
            ):
                return marker
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def write_pending(self, task_id: str) -> None:
        """Publish ownership under the model lock."""
        atomic_json(
            self.path("pending.json"),
            {
                "schema_version": 1,
                "task_id": task_id,
                "created_at": time.time(),
                "manifest_sha256": self.manifest_sha256(),
            },
        )

    def clear_pending(self, task_id: str | None) -> None:
        """Clear the owner's marker, or a stale marker during repair."""
        path = self.path("pending.json")
        try:
            marker = read_json(path)
            if marker.get("task_id") == task_id or (
                task_id is None and self.pending_task() is None
            ):
                path.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            if task_id is None:
                path.unlink(missing_ok=True)

    def complete_pending(self, task_id: str | None) -> None:
        """Preserve the task outcome if completion housekeeping fails."""
        try:
            with self.model_lock():
                self.clear_pending(task_id)
        except OSError:
            logger.warning("Could not clear %s preparation marker", self.name)
