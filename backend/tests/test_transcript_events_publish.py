"""The publication transport is bounded, nonblocking and failure-isolated."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Self

import pytest
from redis.exceptions import ConnectionError
from sqlalchemy.util.concurrency import greenlet_spawn

from backend.services import transcript_events_publish as publisher
from backend.tests.redis_guard import RedisContactedInTests


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class SyncPublisher:
    arguments: tuple[Any, ...]
    closed: bool
    fail: bool
    connection_pool: SimpleNamespace

    def __init__(self, *, fail: bool = False) -> None:
        self.arguments = ()
        self.closed = False
        self.fail = fail
        self.connection_pool = SimpleNamespace(connection_class=object)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: Any) -> None:
        self.closed = True

    def eval(self, *args: Any) -> None:
        self.arguments = args
        if self.fail:
            raise ConnectionError("Redis unavailable")


class AsyncPublisher:
    arguments: tuple[Any, ...]
    closed: bool
    delay: float

    def __init__(self, *, delay: float = 0.01) -> None:
        self.arguments = ()
        self.closed = False
        self.delay = delay

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.closed = True

    async def eval(self, *args: Any) -> None:
        self.arguments = args
        await asyncio.sleep(self.delay)


def test_sync_batch_uses_one_command_and_disables_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = SyncPublisher()
    options: dict[str, Any] = {}

    def factory(*args: Any, **kwargs: Any) -> SyncPublisher:
        options.update(kwargs)
        return client

    monkeypatch.setattr(publisher.redis, "from_url", factory)
    publisher.publish_transcript_changes({index: index + 9 for index in range(100)})
    script, keys, *arguments = client.arguments
    assert "redis.call('PUBLISH'" in script
    assert keys == 0
    assert len(arguments) == 200
    assert arguments[:2] == [publisher.transcript_channel(0), '{"sequence": 9}']
    assert (
        options["socket_timeout"] + options["socket_connect_timeout"]
        <= publisher.PUBLISH_TIMEOUT_SECONDS
    )
    assert options["retry"].get_retries() == 0
    assert client.closed


def test_publish_failure_logs_and_returns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    client = SyncPublisher(fail=True)
    monkeypatch.setattr(publisher.redis, "from_url", lambda *args, **kwargs: client)
    publisher.publish_transcript_changes({1: 3})
    assert client.closed
    assert "polling remains available" in caplog.text


@pytest.mark.anyio
async def test_async_publication_yields_to_other_api_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncPublisher()
    monkeypatch.setattr(publisher.aioredis, "from_url", lambda *args, **kwargs: client)
    commit = asyncio.create_task(
        greenlet_spawn(publisher.publish_transcript_changes, {1: 7})
    )
    await asyncio.sleep(0)
    assert not commit.done()
    await commit
    assert client.arguments[2:] == (publisher.transcript_channel(1), '{"sequence": 7}')
    assert client.closed


@pytest.mark.anyio
async def test_async_timeout_bounds_entire_batch_and_closes_client(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    client = AsyncPublisher(delay=100)
    monkeypatch.setattr(publisher.aioredis, "from_url", lambda *args, **kwargs: client)
    monkeypatch.setattr(publisher, "PUBLISH_TIMEOUT_SECONDS", 0.01)
    await asyncio.wait_for(
        greenlet_spawn(
            publisher.publish_transcript_changes,
            {index: index + 1 for index in range(100)},
        ),
        timeout=1,
    )
    assert client.closed
    assert "polling remains available" in caplog.text


@pytest.mark.anyio
async def test_redis_guard_also_blocks_async_connections(
    redis_contact_guard: list[str],
) -> None:
    connection = publisher.aioredis.connection.Connection(host="localhost", port=6379)
    with pytest.raises(RedisContactedInTests):
        await connection.connect()
    assert len(redis_contact_guard) == 1
    redis_contact_guard.clear()
