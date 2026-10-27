"""A bounded Redis subscription with no database or model lifetime."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import anyio
import redis.asyncio as aioredis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from backend.core.redis import get_redis_url
from backend.services.transcript_events_publish import transcript_channel

HEARTBEAT_SECONDS = 15.0
SUBSCRIBE_TIMEOUT_SECONDS = 2.0
CLOSE_TIMEOUT_SECONDS = 0.5
COALESCE_LIMIT = 64
logger = logging.getLogger(__name__)


def sequence_frame(sequence: int) -> str:
    return f'event: transcript.changed\ndata: {{"sequence":{sequence}}}\n\n'


def _message_sequence(message: dict[str, Any] | None) -> int | None:
    if message is None or message.get("type") != "message":
        return None
    try:
        payload = json.loads(message["data"])
    except (KeyError, TypeError, ValueError):
        return None
    value = payload.get("sequence") if isinstance(payload, dict) else None
    return value if type(value) is int and value >= 0 else None


class TranscriptEventStream:
    channel: str
    client: aioredis.Redis
    pubsub: aioredis.client.PubSub
    closed: bool

    def __init__(self, recording_id: int) -> None:
        self.channel = transcript_channel(recording_id)
        self.client = aioredis.from_url(
            get_redis_url(),
            decode_responses=True,
            socket_connect_timeout=SUBSCRIBE_TIMEOUT_SECONDS,
            retry=Retry(NoBackoff(), 0),
        )
        self.pubsub = self.client.pubsub(ignore_subscribe_messages=False)
        self.closed = False

    async def open(self) -> None:
        """Wait for Redis acknowledgement before the initial committed read."""
        try:
            async with asyncio.timeout(SUBSCRIBE_TIMEOUT_SECONDS):
                await self.pubsub.subscribe(self.channel)
                while True:
                    message = await self.pubsub.get_message(
                        timeout=SUBSCRIBE_TIMEOUT_SECONDS
                    )
                    if message is not None and message.get("type") == "subscribe":
                        return
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        # Starlette cancels the stream's scope on disconnect. Shield teardown
        # from that cancellation, but never let Redis retain a task forever.
        cancelled: asyncio.CancelledError | None = None
        for operation in (
            lambda: self.pubsub.unsubscribe(self.channel),
            self.pubsub.aclose,
            self.client.aclose,
        ):
            try:
                with anyio.move_on_after(CLOSE_TIMEOUT_SECONDS, shield=True):
                    await operation()
            except asyncio.CancelledError as exc:
                # Uvicorn cancels on its drain deadline; asyncio.run can cancel
                # again on exit. Still attempt both closes before propagating it.
                cancelled = exc
            except Exception:
                logger.warning("Transcript stream cleanup failed", exc_info=True)
        if cancelled is not None:
            raise cancelled

    async def _coalesce(self, sequence: int) -> int:
        for _ in range(COALESCE_LIMIT):
            message = await self.pubsub.get_message(timeout=0)
            if message is None:
                break
            candidate = _message_sequence(message)
            if candidate is not None:
                sequence = max(sequence, candidate)
        return sequence

    async def frames(self, sequence: int) -> AsyncIterator[str]:
        loop = asyncio.get_running_loop()
        heartbeat_at = loop.time() + HEARTBEAT_SECONDS
        try:
            yield sequence_frame(sequence)
            while True:
                message = await self.pubsub.get_message(
                    timeout=max(0, heartbeat_at - loop.time())
                )
                candidate = _message_sequence(message)
                if candidate is not None:
                    candidate = await self._coalesce(candidate)
                    if candidate > sequence:
                        sequence = candidate
                        yield sequence_frame(sequence)
                if loop.time() >= heartbeat_at:
                    yield ": heartbeat\n\n"
                    # EventSource hides comments. Repeat the current hint so
                    # browsers can observe liveness without a second schema.
                    yield sequence_frame(sequence)
                    heartbeat_at = loop.time() + HEARTBEAT_SECONDS
        finally:
            await self.close()
