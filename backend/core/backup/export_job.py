"""The export being built, recorded in Redis so every API process sees the same one.

The record is what keeps exports to one at a time: a request for the same options joins
the running build, and a request for different options waits for it. The worker deletes
the record when it finishes. A worker that dies mid-build cannot, so a build that stops
reporting progress for EXPORT_STALL_SECONDS is treated as lost and the record as free.

The status a browser polls is assembled here from three sources that each know part of
the answer: the cache manifest says which build produced the archive on disk, Celery
says how a build ended or how far it has got, and the record says what is still queued.
Celery alone cannot tell a queued task from one it has never heard of, or from one whose
result expired a day after it finished, since it reports PENDING for all three.
"""

import json
import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from backend.core.backup.cache import CachedExport
from backend.core.backup.format import (
    EXPORT_JOB_KEY,
    EXPORT_JOB_TTL_SECONDS,
    EXPORT_STALL_SECONDS,
)

logger = logging.getLogger(__name__)

_TERMINAL_STATES = frozenset({"SUCCESS", "FAILURE", "REVOKED"})

# Deletes the record only if it still names this task, so a finishing build never
# clears the record of a newer one.
_RELEASE_IF_OWNED = """
local raw = redis.call('get', KEYS[1])
if not raw then return 0 end
local ok, job = pcall(cjson.decode, raw)
if ok and type(job) == 'table' and job['task_id'] == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


@dataclass
class ExportJob:
    task_id: str
    include_audio: bool
    archive_quality: str
    requested_at: float

    def same_options(self, include_audio: bool, archive_quality: str) -> bool:
        return (
            self.include_audio == include_audio
            and self.archive_quality == archive_quality
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    def summary(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "include_audio": self.include_audio,
            "archive_quality": self.archive_quality,
        }

    @classmethod
    def from_json(cls, raw: Any) -> Optional["ExportJob"]:
        if not raw:
            return None
        try:
            return cls(**json.loads(raw))
        except (TypeError, ValueError):
            logger.warning("Ignoring an unreadable export job record.")
            return None


async def read_job(client: Any) -> Optional[ExportJob]:
    return ExportJob.from_json(await client.get(EXPORT_JOB_KEY))


async def claim_job(client: Any, job: ExportJob) -> bool:
    """Record ``job`` as the running export unless another already is."""
    return bool(
        await client.set(
            EXPORT_JOB_KEY, job.to_json(), nx=True, ex=EXPORT_JOB_TTL_SECONDS
        )
    )


async def release_job(client: Any, task_id: str) -> None:
    await client.eval(_RELEASE_IF_OWNED, 1, EXPORT_JOB_KEY, task_id)


def release_job_sync(task_id: str) -> None:
    """Worker side: free the record once this build has finished, whatever the outcome."""
    try:
        import redis

        from backend.core.redis import REDIS_URL

        redis.from_url(REDIS_URL).eval(_RELEASE_IF_OWNED, 1, EXPORT_JOB_KEY, task_id)
    except Exception as e:  # noqa: BLE001 -- boundary: a stuck record is freed by the stall rule, so a failed release is not fatal
        logger.warning("Failed to release export job record: %s", e)


def _heartbeat(info: Dict[str, Any], job: Optional[ExportJob]) -> Optional[float]:
    updated_at = info.get("updated_at")
    if isinstance(updated_at, (int, float)):
        return float(updated_at)
    if job is not None:
        return job.requested_at
    return None


def is_stalled(info: Dict[str, Any], job: Optional[ExportJob], now: float) -> bool:
    heartbeat = _heartbeat(info, job)
    return heartbeat is not None and now - heartbeat > EXPORT_STALL_SECONDS


def _info(result: Any) -> Dict[str, Any]:
    info = result.info
    return info if isinstance(info, dict) else {}


def job_is_active(job: ExportJob, result: Any, now: float) -> bool:
    """Whether the recorded build is still queued or running."""
    if result.state in _TERMINAL_STATES:
        return False
    return not is_stalled(_info(result), job, now)


def _success(cached: CachedExport, reused: bool) -> Dict[str, Any]:
    return {
        "state": "SUCCESS",
        "status": "Backup ready",
        "export": cached.summary(),
        "reused": reused,
    }


def describe_export(
    task_id: str,
    *,
    result: Any,
    job: Optional[ExportJob],
    cached: Optional[CachedExport],
    now: float,
) -> Dict[str, Any]:
    """What a browser polling ``task_id`` is told.

    States are PENDING and PROCESSING while a build is under way, then SUCCESS, FAILURE,
    or EXPIRED for a build whose archive has since been replaced or deleted, or that the
    server no longer remembers.
    """
    if cached is not None and cached.export_id == task_id:
        return _success(cached, reused=False)

    state = result.state
    if state == "SUCCESS":
        payload = result.result if isinstance(result.result, dict) else {}
        if cached is not None and payload.get("export_id") == cached.export_id:
            return _success(cached, reused=bool(payload.get("reused")))
        return {
            "state": "EXPIRED",
            "status": (
                "That backup has been replaced by a newer one or deleted. Download the "
                "latest from Settings > Backup and restore."
            ),
        }

    if state in ("FAILURE", "REVOKED"):
        return {
            "state": "FAILURE",
            "status": "The backup failed. Check the worker logs for details.",
        }

    # A task that is not the recorded build and has never reported a heartbeat is not
    # one the server is waiting for: an id it never queued, a result that expired, or
    # an export left running by a version without heartbeats when it was upgraded.
    owned = job is not None and job.task_id == task_id
    info = _info(result)
    if not owned and not isinstance(info.get("updated_at"), (int, float)):
        return {
            "state": "EXPIRED",
            "status": (
                "The server is no longer tracking that backup. Check Settings > Backup "
                "and restore for the latest one."
            ),
        }

    if is_stalled(info, job if owned else None, now):
        return {
            "state": "FAILURE",
            "status": (
                "The backup stopped making progress and was abandoned. Check that the "
                "CPU worker is running, then try again."
            ),
        }

    if state == "PENDING":
        return {"state": "PENDING", "status": "Waiting for a worker..."}

    return {
        "state": "PROCESSING",
        "status": info.get("status", ""),
        # Stage and counts let the client draw a real progress bar rather than an
        # indeterminate spinner that cannot distinguish work from a stall.
        "stage": info.get("stage"),
        "current": info.get("current"),
        "total": info.get("total"),
    }
