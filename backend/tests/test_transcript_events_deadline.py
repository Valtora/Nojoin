"""One deadline is consumed across Redis connect, authentication and publish."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import redis
from redis.exceptions import TimeoutError

from backend.services import transcript_events_deadline as deadline


def test_protocol_commands_share_one_remaining_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    elapsed = [0.0]
    observed: list[float] = []
    monkeypatch.setattr(deadline.time, "monotonic", lambda: elapsed[0])

    class Connection:
        socket_timeout: float
        _sock: None = None

        def connect(self) -> None:
            observed.append(self.socket_timeout)
            elapsed[0] += 0.2

        def send_packed_command(self, *args: Any, **kwargs: Any) -> None:
            observed.append(self.socket_timeout)
            elapsed[0] += 0.1

        def read_response(self, *args: Any, **kwargs: Any) -> str:
            observed.append(self.socket_timeout)
            elapsed[0] += 0.15
            return "OK"

    pool = SimpleNamespace(connection_class=Connection)
    deadline.apply_publication_deadline(pool, 0.5)
    client = pool.connection_class()
    client.connect()
    client.send_packed_command("AUTH")
    assert client.read_response() == "OK"
    client.send_packed_command("EVAL")
    with pytest.raises(TimeoutError, match="deadline exceeded"):
        client.read_response()
    assert observed == pytest.approx([0.5, 0.3, 0.2, 0.05])


@pytest.mark.parametrize(
    ("url", "transport"),
    [
        ("redis://localhost/0", redis.Connection),
        ("rediss://localhost/0", redis.SSLConnection),
        ("unix:///tmp/redis.sock", redis.UnixDomainSocketConnection),
    ],
)
def test_deadline_preserves_url_transport(url: str, transport: type[Any]) -> None:
    pool = redis.ConnectionPool.from_url(url)
    deadline.apply_publication_deadline(pool, 0.5)
    connection = pool.make_connection()
    assert isinstance(connection, transport)
