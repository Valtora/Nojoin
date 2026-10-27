"""Carry one worker publication deadline through Redis protocol commands."""

from __future__ import annotations

import time
from socket import socket
from typing import Any

from redis.exceptions import TimeoutError


class _DeadlineConnection:
    publication_deadline: float
    socket_timeout: float | None
    socket_connect_timeout: float | None
    _sock: socket | None

    def _refresh_timeout(self) -> None:
        remaining = self.publication_deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Transcript publication deadline exceeded")
        self.socket_timeout = remaining
        self.socket_connect_timeout = remaining
        if self._sock is not None:
            self._sock.settimeout(remaining)

    def connect(self) -> None:
        self._refresh_timeout()
        super().connect()

    def send_packed_command(self, *args: Any, **kwargs: Any) -> None:
        self._refresh_timeout()
        super().send_packed_command(*args, **kwargs)

    def read_response(self, *args: Any, **kwargs: Any) -> Any:
        self._refresh_timeout()
        return super().read_response(*args, **kwargs)


def apply_publication_deadline(pool: Any, seconds: float) -> None:
    """Preserve the URL's TCP/TLS/Unix transport while bounding its commands.

    OS DNS resolution and a trusted server slowly dripping one response are
    outside socket timeout guarantees. No background thread is left behind.
    """
    pool.connection_class = type(
        "TranscriptPublicationConnection",
        (_DeadlineConnection, pool.connection_class),
        {"publication_deadline": time.monotonic() + seconds},
    )
