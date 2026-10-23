"""Container log downloads, streamed so that no log sits whole in memory.

One service's download is its whole log. The download of every service holds
the newest ``ALL_SERVICES_TAIL_LINES`` lines of each, merged by time and
prefixed with the service's name as the live view shows them, so its size
stays bounded however long the services have run.
"""

from __future__ import annotations

import heapq
import logging
from collections.abc import Iterable, Iterator, Sequence
from typing import Any

from docker.errors import NotFound

logger = logging.getLogger(__name__)

ALL_SERVICES_TAIL_LINES = 10_000


def open_log_stream(docker_client: Any, name: str, tail: int | str = "all") -> Any:
    """Start reading a container's log. Raises NotFound or a Docker error."""
    container = docker_client.containers.get(name)
    return container.logs(stream=True, follow=False, timestamps=True, tail=tail)


def _close(stream: Any) -> None:
    close = getattr(stream, "close", None)
    if callable(close):
        close()


def _whole_lines(chunks: Iterable[bytes]) -> Iterator[bytes]:
    """Each line once, ending in a newline, however Docker split the chunks."""
    pending = b""
    for chunk in chunks:
        pending += chunk
        *complete, pending = pending.split(b"\n")
        for line in complete:
            yield line + b"\n"
    if pending:
        yield pending + b"\n"


def _keyed_lines(
    name: str, stream: Iterable[bytes], prefix: bytes
) -> Iterator[tuple[bytes, bytes]]:
    """Pair each line with its timestamp, the sort key of the merge.

    Docker writes a fixed-width RFC 3339 UTC timestamp before every line when
    asked for timestamps, so comparing the bytes orders the lines by time. A
    read that fails part-way ends this service's lines with a note at the point
    it stopped: the response has started, so it can no longer be an error.
    """
    key = b""
    try:
        for line in _whole_lines(stream):
            key = line.split(b" ", 1)[0]
            yield key, prefix + line
    except Exception as exc:  # noqa: BLE001 -- boundary: the response has already started, so a failed read is noted in the file instead
        logger.error("Reading the logs of %s failed: %s", name, exc)
        yield key, prefix + f"Reading this log failed here: {exc}\n".encode()


def one_service_log(name: str, stream: Any) -> Iterator[bytes]:
    """One container's whole log, from a stream ``open_log_stream`` opened."""
    try:
        for _key, line in _keyed_lines(name, stream, b""):
            yield line
    finally:
        _close(stream)


def all_services_log(docker_client: Any, names: Sequence[str]) -> Iterator[bytes]:
    """The newest lines of each service, merged by time.

    A service with no container, or whose log cannot be opened, is named in a
    note at the top of the file and left out, so one missing worker never
    costs the others' logs.
    """
    streams: list[Any] = []
    try:
        for name in names:
            try:
                stream = open_log_stream(docker_client, name, ALL_SERVICES_TAIL_LINES)
            except NotFound:
                yield f"[{name}] Left out: no container with this name.\n".encode()
                continue
            except Exception as exc:  # noqa: BLE001 -- boundary: one unreadable service is noted in the file instead of failing the download
                logger.error("Opening the logs of %s failed: %s", name, exc)
                yield f"[{name}] Left out: its log could not be read: {exc}\n".encode()
                continue
            streams.append((name, stream))

        merged = heapq.merge(
            *(
                _keyed_lines(name, stream, f"[{name}] ".encode())
                for name, stream in streams
            ),
            key=lambda item: item[0],
        )
        for _key, line in merged:
            yield line
    finally:
        for _name, stream in streams:
            _close(stream)
