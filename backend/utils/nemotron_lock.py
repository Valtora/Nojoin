"""Worker-only native model locking and owned pending task markers."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from backend.utils.model_cache_paths import hf_hub_cache_root
from backend.utils.nemotron_manifest import manifest_sha256, read_json

PENDING_TTL = 30 * 60
logger = logging.getLogger(__name__)


def _control_path(name: str) -> Path:
    return Path(hf_hub_cache_root()) / ".locks" / "nojoin-nemotron" / name


@contextmanager
def model_lock(*, blocking: bool = True) -> Iterator[bool]:
    """Lock a stable inode outside the deletable repo; never unlink it."""
    import fcntl

    lock = _control_path("prepare.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+b") as handle:
        acquired = False
        try:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
                acquired = True
            except BlockingIOError:
                pass
            yield acquired
        finally:
            if acquired:
                fcntl.flock(handle, fcntl.LOCK_UN)


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


def pending_task() -> dict[str, Any] | None:
    """Read a valid unexpired marker; caller holds the model lock."""
    try:
        marker = read_json(_control_path("pending.json"))
        age = time.time() - float(marker["created_at"])
        if (
            marker.get("schema_version") == 1
            and marker["manifest_sha256"] == manifest_sha256()
            and isinstance(marker["task_id"], str)
            and 0 <= age < PENDING_TTL
        ):
            return marker
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def write_pending(task_id: str) -> None:
    """Publish pending identity before dispatch, under the model lock."""
    atomic_json(
        _control_path("pending.json"),
        {
            "schema_version": 1,
            "task_id": task_id,
            "created_at": time.time(),
            "manifest_sha256": manifest_sha256(),
        },
    )


def clear_pending(task_id: str | None) -> None:
    """Clear only our marker, or a stale marker for an explicit repair."""
    path = _control_path("pending.json")
    try:
        marker = read_json(path)
        if marker.get("task_id") == task_id or (
            task_id is None and pending_task() is None
        ):
            path.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError):
        if task_id is None:
            path.unlink(missing_ok=True)


def complete_pending(task_id: str | None) -> None:
    """Completion housekeeping must preserve the original task outcome."""
    try:
        with model_lock():
            clear_pending(task_id)
    except OSError:
        logger.warning("Could not clear native speaker preparation marker")
