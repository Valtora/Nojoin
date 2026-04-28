from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

COMPANION_EXPLICIT_DISCONNECT_EVENT = "companion-explicit-disconnect"


class CompanionFrontendEventBroker:
    def __init__(self) -> None:
        self._subscriptions: dict[int, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(self, user_id: int) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=8)
        async with self._lock:
            self._subscriptions[user_id].add(queue)
        return queue

    async def unsubscribe(
        self,
        user_id: int,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        async with self._lock:
            subscribers = self._subscriptions.get(user_id)
            if not subscribers:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscriptions.pop(user_id, None)

    async def publish(self, user_id: int, event: dict[str, Any]) -> None:
        async with self._lock:
            subscribers = list(self._subscriptions.get(user_id, ()))

        for queue in subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                continue

    async def publish_explicit_disconnect(self, user_id: int) -> None:
        await self.publish(
            user_id,
            {
                "type": COMPANION_EXPLICIT_DISCONNECT_EVENT,
                "reason": "manual_disconnect",
                "source": "companion_app",
                "occurred_at": datetime.now(timezone.utc).isoformat(),
            },
        )


companion_frontend_events = CompanionFrontendEventBroker()