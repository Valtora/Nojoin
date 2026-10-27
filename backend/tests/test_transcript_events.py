"""SSE hints tolerate bursts, malformed traffic and cancellation without a DB."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import anyio
import pytest

from backend.services import transcript_events as events
from backend.tests.transcript_events_fakes import FakeStreamClient

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def stream_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeStreamClient]:
    client = FakeStreamClient()
    monkeypatch.setattr(events.aioredis, "from_url", lambda *args, **kwargs: client)
    yield client


async def test_subscription_acknowledgement_is_required(
    stream_client: FakeStreamClient,
) -> None:
    stream_client.subscription.auto_ack = False
    stream = events.TranscriptEventStream(12)
    opening = asyncio.create_task(stream.open())
    await asyncio.sleep(0)
    assert not opening.done()
    stream_client.subscription.messages.put_nowait({"type": "subscribe"})
    await opening
    assert stream_client.subscription.acknowledged
    await stream.close()


async def test_bursts_coalesce_and_duplicates_or_old_sequences_do_not_emit(
    stream_client: FakeStreamClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(events, "HEARTBEAT_SECONDS", 0.01)
    stream = events.TranscriptEventStream(12)
    await stream.open()
    frames = stream.frames(4)
    assert await anext(frames) == events.sequence_frame(4)
    for value in [5, 5, 8, 6, 8]:
        stream_client.subscription.hint(value)
    assert await anext(frames) == events.sequence_frame(8)
    stream_client.subscription.hint(7)
    stream_client.subscription.hint(8)
    assert await anext(frames) == ": heartbeat\n\n"
    assert await anext(frames) == events.sequence_frame(8)
    await frames.aclose()
    assert stream_client.subscription.unsubscribed
    assert stream_client.subscription.closed
    assert stream_client.closed


async def test_malformed_messages_do_not_break_heartbeat(
    stream_client: FakeStreamClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(events, "HEARTBEAT_SECONDS", 0.01)
    stream = events.TranscriptEventStream(12)
    await stream.open()
    frames = stream.frames(3)
    await anext(frames)
    for data in [
        "broken",
        "[]",
        '{"sequence":true}',
        '{"sequence":-1}',
        '{"sequence":"9"}',
    ]:
        stream_client.subscription.messages.put_nowait(
            {"type": "message", "data": data}
        )
    assert await anext(frames) == ": heartbeat\n\n"
    assert await anext(frames) == events.sequence_frame(3)
    await frames.aclose()


async def test_no_hint_still_has_observable_heartbeat(
    stream_client: FakeStreamClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(events, "HEARTBEAT_SECONDS", 0.01)
    stream = events.TranscriptEventStream(12)
    await stream.open()
    frames = stream.frames(14)
    assert await anext(frames) == events.sequence_frame(14)
    assert await anext(frames) == ": heartbeat\n\n"
    assert await anext(frames) == events.sequence_frame(14)
    await frames.aclose()


async def test_cancelled_reader_closes_redis_even_if_unsubscribe_fails(
    stream_client: FakeStreamClient,
) -> None:
    stream_client.subscription.unsubscribe_error = True
    stream = events.TranscriptEventStream(12)
    await stream.open()
    frames = stream.frames(1)
    await anext(frames)
    read = asyncio.create_task(anext(frames))
    await asyncio.sleep(0)
    read.cancel()
    with pytest.raises(asyncio.CancelledError):
        await read
    assert stream_client.subscription.closed
    assert stream_client.closed


async def test_anyio_disconnect_cancellation_does_not_cancel_cleanup(
    stream_client: FakeStreamClient,
) -> None:
    stream = events.TranscriptEventStream(12)
    await stream.open()
    frames = stream.frames(1)
    await anext(frames)
    with anyio.CancelScope() as scope:
        scope.cancel()
        await frames.aclose()
    assert stream_client.subscription.unsubscribed
    assert stream_client.subscription.closed
    assert stream_client.closed


async def test_subscribe_timeout_cleans_up_before_first_frame(
    stream_client: FakeStreamClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(events, "SUBSCRIBE_TIMEOUT_SECONDS", 0.01)
    stream_client.subscription.auto_ack = False
    with pytest.raises(TimeoutError):
        await events.TranscriptEventStream(12).open()
    assert stream_client.subscription.unsubscribed
    assert stream_client.subscription.closed
    assert stream_client.closed


async def test_large_bursts_have_bounded_coalescing(
    stream_client: FakeStreamClient,
) -> None:
    stream = events.TranscriptEventStream(12)
    await stream.open()
    frames = stream.frames(1)
    await anext(frames)
    for value in range(2, events.COALESCE_LIMIT * 2 + 3):
        stream_client.subscription.hint(value)
    assert await anext(frames) == events.sequence_frame(events.COALESCE_LIMIT + 2)
    assert await anext(frames) == events.sequence_frame(events.COALESCE_LIMIT * 2 + 2)
    await frames.aclose()
