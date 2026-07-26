"""Short-lived job records for the notes-structure generator.

The generator is interactive but the repo rule is firm: no LLM call in an API
request path. So the endpoint dispatches a Celery task and the browser polls a
job record. Redis rather than a table because the record is worthless minutes
after it is read -- it exists only to carry one proposal from the worker back to
the tab that asked for it, and the codex model catalogue already establishes this
pattern in the codebase.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Optional

from backend.core.redis import get_redis_url

_KEY_PREFIX = "nojoin:notes_structure_job:"
# Long enough to outlast a slow local model, short enough that abandoned jobs
# evaporate on their own.
_TTL_SECONDS = 1800

STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"
STATUS_ERROR = "error"


def new_job_id() -> str:
    return uuid.uuid4().hex


def _key(job_id: str) -> str:
    return f"{_KEY_PREFIX}{job_id}"


def publish_job(job_id: str, payload: dict[str, Any]) -> None:
    """Worker side (sync): record a job's state."""
    import redis as sync_redis

    client = sync_redis.from_url(get_redis_url(), decode_responses=True)
    try:
        client.set(_key(job_id), json.dumps(payload), ex=_TTL_SECONDS)
    finally:
        client.close()


async def read_job(job_id: str) -> Optional[dict[str, Any]]:
    """API side (async): a job's state, or None once it has expired."""
    import redis.asyncio as redis

    client = redis.from_url(get_redis_url(), decode_responses=True)
    try:
        raw = await client.get(_key(job_id))
    finally:
        await client.aclose()
    return json.loads(raw) if raw else None


async def publish_job_async(job_id: str, payload: dict[str, Any]) -> None:
    """API side (async): record a job as pending before dispatching it."""
    import redis.asyncio as redis

    client = redis.from_url(get_redis_url(), decode_responses=True)
    try:
        await client.set(_key(job_id), json.dumps(payload), ex=_TTL_SECONDS)
    finally:
        await client.aclose()
