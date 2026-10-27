"""Shipped launch commands bound drain while a real SSE client stays connected."""

from __future__ import annotations

import ast
import asyncio
import json
import shlex
import socket
from pathlib import Path

import pytest
import uvicorn

from backend.services import transcript_events as events
from backend.tests.test_transcript_events_publication import (
    events_engine as events_engine,
)
from backend.tests.test_transcript_events_route import (
    RouteContext,
)
from backend.tests.test_transcript_events_route import (
    route_context as route_context,
)
from backend.tests.test_transcript_events_route import (
    stream_client as stream_client,
)
from backend.tests.transcript_events_fakes import FakeStreamClient

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def command_timeout(path: str) -> int | None:
    source = (ROOT / path).read_text()
    if path.endswith(".py"):
        for node in ast.walk(ast.parse(source)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "uvicorn"
                and node.func.attr == "run"
            ):
                for keyword in node.keywords:
                    if keyword.arg == "timeout_graceful_shutdown":
                        return ast.literal_eval(keyword.value)
        return None
    for line in source.splitlines():
        if line.startswith("CMD "):
            command = json.loads(line.removeprefix("CMD "))
            break
        if "command: uvicorn " in line:
            command = shlex.split(line.split("command: ", 1)[1])
            break
    else:
        raise AssertionError(f"No shipped Uvicorn command in {path}")
    option = "--timeout-graceful-shutdown"
    return int(command[command.index(option) + 1]) if option in command else None


@pytest.mark.parametrize(
    "path",
    [
        "docker/Dockerfile.api",
        "demo/server/Dockerfile",
        "docs/DEVELOPMENT.md",
        "backend/main.py",
    ],
)
def test_every_shipped_uvicorn_command_bounds_drain(path: str) -> None:
    timeout = command_timeout(path)
    # Shipped Linux containers use Docker's default ten-second stop grace.
    # Leave three seconds for bounded Redis teardown and lifespan shutdown.
    assert timeout is not None, f"{path} leaves SSE drain unbounded"
    assert 0 < timeout <= 7 < 10


@pytest.mark.anyio
async def test_uvicorn_shutdown_releases_open_endpoint_stream(
    route_context: RouteContext,
    stream_client: FakeStreamClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.tests.test_transcript_events_lifecycle import application

    app, _ = application(route_context, monkeypatch)
    timeout = command_timeout("docker/Dockerfile.api")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            lifespan="off",
            log_config=None,
            timeout_graceful_shutdown=timeout,
        )
    )
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    writer: asyncio.StreamWriter | None = None
    stopped = False
    released = False
    try:
        async with asyncio.timeout(3):
            while not server.started:
                await asyncio.sleep(0.01)
            reader, writer = await asyncio.open_connection(*listener.getsockname())
            request = (
                f"GET /api/v1/transcripts/{route_context.public_id}/events HTTP/1.1\r\n"
                "Host: localhost\r\nAuthorization: Bearer probe\r\n\r\n"
            )
            writer.write(request.encode())
            await writer.drain()
            await reader.readuntil(b"sequence")
        server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(serving), (timeout or 0) + 2)
            async with asyncio.timeout(2):
                while not stream_client.closed:
                    await asyncio.sleep(0)
            stopped = serving.done()
            released = (
                stream_client.subscription.unsubscribed
                and stream_client.subscription.closed
                and stream_client.closed
            )
        except TimeoutError:
            pass
    finally:
        server.should_exit = True
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        try:
            await asyncio.wait_for(serving, 3)
        finally:
            listener.close()
    assert stopped, "Open transcript tab prevented graceful server shutdown"
    assert released, "Shutdown did not release Redis before closing the browser socket"


@pytest.mark.anyio
async def test_runner_recancellation_still_closes_redis(
    stream_client: FakeStreamClient,
) -> None:
    stream = events.TranscriptEventStream(12)
    await stream.open()
    frames = stream.frames(1)
    await anext(frames)
    unsubscribing = asyncio.Event()

    async def yielding_unsubscribe(channel: str) -> None:
        assert channel == stream.channel
        unsubscribing.set()
        await asyncio.Event().wait()

    stream_client.subscription.unsubscribe = yielding_unsubscribe
    read = asyncio.create_task(anext(frames))
    await asyncio.sleep(0)
    read.cancel("Uvicorn graceful timeout")
    async with asyncio.timeout(1):
        await unsubscribing.wait()
        read.cancel("asyncio.run exit")
        with pytest.raises(asyncio.CancelledError):
            await read
    assert stream_client.subscription.closed
    assert stream_client.closed
