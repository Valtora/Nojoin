"""In-memory Redis boundary for stream lifecycle tests."""

from __future__ import annotations

import asyncio
from typing import Any


class FakePubSub:
    messages: asyncio.Queue[dict[str, Any]]
    subscribed: str | None
    acknowledged: bool
    closed: bool
    unsubscribed: bool
    auto_ack: bool
    unsubscribe_error: bool

    def __init__(self) -> None:
        self.messages: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.subscribed: str | None = None
        self.acknowledged = False
        self.closed = False
        self.unsubscribed = False
        self.auto_ack = True
        self.unsubscribe_error = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed = channel
        if self.auto_ack:
            self.messages.put_nowait({"type": "subscribe", "channel": channel})

    async def get_message(self, *, timeout: float) -> dict[str, Any] | None:
        try:
            if timeout == 0:
                message = self.messages.get_nowait()
            else:
                message = await asyncio.wait_for(self.messages.get(), timeout)
        except (asyncio.QueueEmpty, TimeoutError):
            return None
        if message.get("type") == "subscribe":
            self.acknowledged = True
        return message

    def hint(self, sequence: int) -> None:
        self.messages.put_nowait(
            {"type": "message", "data": f'{{"sequence":{sequence}}}'}
        )

    async def unsubscribe(self, channel: str) -> None:
        assert channel == self.subscribed
        self.unsubscribed = True
        if self.unsubscribe_error:
            raise OSError("connection lost during unsubscribe")

    async def aclose(self) -> None:
        self.closed = True


class FakeStreamClient:
    subscription: FakePubSub
    closed: bool

    def __init__(self) -> None:
        self.subscription = FakePubSub()
        self.closed = False

    def pubsub(self, *, ignore_subscribe_messages: bool) -> FakePubSub:
        assert ignore_subscribe_messages is False
        return self.subscription

    async def aclose(self) -> None:
        self.closed = True
