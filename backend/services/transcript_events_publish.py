"""Best-effort, post-commit Redis hints; transcript reads remain authoritative."""

from __future__ import annotations

import asyncio
import json
import logging

import redis
import redis.asyncio as aioredis
from redis.asyncio.retry import Retry as AsyncRetry
from redis.backoff import NoBackoff
from redis.retry import Retry
from sqlalchemy.util.concurrency import await_only, in_greenlet

from backend.core.redis import get_redis_url
from backend.services.transcript_events_deadline import apply_publication_deadline

logger = logging.getLogger(__name__)

PUBLISH_TIMEOUT_SECONDS = 0.5


def transcript_channel(recording_id: int) -> str:
    return f"nojoin:transcript:{recording_id}:changed"


_PUBLISH_SCRIPT = """
for i = 1, #ARGV, 2 do
    redis.call('PUBLISH', ARGV[i], ARGV[i + 1])
end
return #ARGV / 2
"""


def _arguments(changes: dict[int, int]) -> list[str]:
    arguments: list[str] = []
    for recording_id, sequence in changes.items():
        arguments.extend(
            (transcript_channel(recording_id), json.dumps({"sequence": sequence}))
        )
    return arguments


async def _publish_async(changes: dict[int, int]) -> None:
    async with asyncio.timeout(PUBLISH_TIMEOUT_SECONDS):
        async with aioredis.from_url(
            get_redis_url(),
            socket_connect_timeout=PUBLISH_TIMEOUT_SECONDS / 2,
            socket_timeout=PUBLISH_TIMEOUT_SECONDS / 2,
            retry=AsyncRetry(NoBackoff(), 0),
        ) as client:
            await client.eval(_PUBLISH_SCRIPT, 0, *_arguments(changes))


def _publish_sync(changes: dict[int, int]) -> None:
    # One server-side batch has one response, so linked recordings cannot each
    # consume another socket timeout. There are no connection or command retries.
    with redis.from_url(
        get_redis_url(),
        socket_connect_timeout=PUBLISH_TIMEOUT_SECONDS / 2,
        socket_timeout=PUBLISH_TIMEOUT_SECONDS / 2,
        retry=Retry(NoBackoff(), 0),
    ) as client:
        apply_publication_deadline(client.connection_pool, PUBLISH_TIMEOUT_SECONDS)
        client.eval(_PUBLISH_SCRIPT, 0, *_arguments(changes))


def publish_transcript_changes(changes: dict[int, int]) -> None:
    """Run after root commit, without blocking the asynchronous API event loop."""
    try:
        if in_greenlet():
            await_only(_publish_async(changes))
        else:
            _publish_sync(changes)
    except Exception:
        logger.warning(
            "Transcript wake-up failed; polling remains available", exc_info=True
        )
