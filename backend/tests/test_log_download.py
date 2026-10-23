"""The logs download, for one service and for every service at once (#633).

Docker is replaced by a fake that serves fixed log chunks, so these pin what
the endpoint asks Docker for and what the file holds: one service's whole log,
or the newest lines of every service merged by time, streamed either way.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from docker.errors import APIError, NotFound
from fastapi.testclient import TestClient

from backend.api.deps import get_current_active_superuser
from backend.api.services.log_download import ALL_SERVICES_TAIL_LINES
from backend.api.v1.endpoints import system
from backend.main import create_app


class _Stream:
    """A log stream: chunks as Docker yields them, then an optional failure."""

    def __init__(self, chunks: list[bytes], fail: Exception | None = None):
        self._chunks = chunks
        self._fail = fail
        self.closed = False

    def __iter__(self):
        yield from self._chunks
        if self._fail is not None:
            raise self._fail

    def close(self) -> None:
        self.closed = True


class _Container:
    def __init__(self, stream: _Stream, calls: list[dict]):
        self._stream = stream
        self._calls = calls

    def logs(self, **kwargs):
        self._calls.append(kwargs)
        return self._stream


class _Containers:
    def __init__(self, streams: dict[str, _Stream], broken: set[str]):
        self.streams = streams
        self.broken = broken
        self.calls: dict[str, list[dict]] = {}

    def get(self, name: str):
        if name in self.broken:
            raise APIError("socket proxy refused the request")
        if name not in self.streams:
            raise NotFound(f"No such container: {name}")
        return _Container(self.streams[name], self.calls.setdefault(name, []))


def _docker(streams: dict[str, _Stream], broken: set[str] | None = None):
    return MagicMock(containers=_Containers(streams, broken or set()))


@pytest.fixture
def client_for(monkeypatch):
    def _client(docker_client) -> TestClient:
        app = create_app(app_lifespan=None)

        async def superuser():
            return MagicMock(is_superuser=True, role="owner")

        app.dependency_overrides[get_current_active_superuser] = superuser
        monkeypatch.setattr(system, "client", docker_client)
        return TestClient(app)

    return _client


def test_all_services_download_as_one_file_merged_by_time(client_for) -> None:
    api = _Stream(
        [
            b"2026-10-23T09:00:01.000000000Z GET /health\n",
            b"2026-10-23T09:00:03.000000000Z GET /recordings\n",
        ]
    )
    db = _Stream([b"2026-10-23T09:00:02.000000000Z checkpoint starting\n"])
    docker_client = _docker({"nojoin-api": api, "nojoin-db": db})

    response = client_for(docker_client).get(
        "/api/v1/system/logs/download?container=all"
    )

    assert response.status_code == 200
    assert (
        response.headers["content-disposition"]
        == "attachment; filename=nojoin-all_logs.txt"
    )
    lines = response.text.splitlines()
    missing = [
        n for n in system.LOG_ALL_CONTAINERS if n not in {"nojoin-api", "nojoin-db"}
    ]
    assert lines[: len(missing)] == [
        f"[{name}] Left out: no container with this name." for name in missing
    ]
    assert lines[len(missing) :] == [
        "[nojoin-api] 2026-10-23T09:00:01.000000000Z GET /health",
        "[nojoin-db] 2026-10-23T09:00:02.000000000Z checkpoint starting",
        "[nojoin-api] 2026-10-23T09:00:03.000000000Z GET /recordings",
    ]
    # Bounded: only the newest lines of each service, read without following.
    for name in ("nojoin-api", "nojoin-db"):
        assert docker_client.containers.calls[name] == [
            {
                "stream": True,
                "follow": False,
                "timestamps": True,
                "tail": ALL_SERVICES_TAIL_LINES,
            }
        ]
    assert api.closed and db.closed


def test_a_failed_read_is_noted_where_it_stopped(client_for) -> None:
    api = _Stream(
        [b"2026-10-23T09:00:01.000000000Z first\n"],
        fail=APIError("connection reset"),
    )
    io = _Stream(
        [
            b"2026-10-23T09:00:00.000000000Z early\n",
            b"2026-10-23T09:00:05.000000000Z late\n",
        ]
    )
    docker_client = _docker(
        {"nojoin-api": api, "nojoin-worker-io": io}, broken={"nojoin-db"}
    )

    response = client_for(docker_client).get(
        "/api/v1/system/logs/download?container=all"
    )

    assert response.status_code == 200
    lines = response.text.splitlines()
    assert any(
        line.startswith("[nojoin-db] Left out: its log could not be read:")
        for line in lines
    )
    merged = [line for line in lines if "Left out" not in line]
    assert merged[0] == "[nojoin-worker-io] 2026-10-23T09:00:00.000000000Z early"
    assert merged[1] == "[nojoin-api] 2026-10-23T09:00:01.000000000Z first"
    assert merged[2].startswith("[nojoin-api] Reading this log failed here:")
    assert merged[3] == "[nojoin-worker-io] 2026-10-23T09:00:05.000000000Z late"
    assert api.closed and io.closed


def test_one_service_downloads_its_whole_log_as_streamed(client_for) -> None:
    # Docker's chunks need not end on a line; the file holds each line once.
    api = _Stream(
        [
            b"2026-10-23T09:00:01.000000000Z one\n2026-10-23T09:00:02.0",
            b"00000000Z two\n",
        ]
    )
    docker_client = _docker({"nojoin-api": api})

    response = client_for(docker_client).get(
        "/api/v1/system/logs/download?container=nojoin-api"
    )

    assert response.status_code == 200
    assert (
        response.headers["content-disposition"]
        == "attachment; filename=nojoin-api_logs.txt"
    )
    assert response.text == (
        "2026-10-23T09:00:01.000000000Z one\n2026-10-23T09:00:02.000000000Z two\n"
    )
    assert docker_client.containers.calls["nojoin-api"] == [
        {"stream": True, "follow": False, "timestamps": True, "tail": "all"}
    ]
    assert api.closed


def test_one_missing_service_is_not_found(client_for) -> None:
    response = client_for(_docker({})).get(
        "/api/v1/system/logs/download?container=nojoin-worker-gpu"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Container nojoin-worker-gpu not found"


def test_a_container_outside_the_list_is_still_refused(client_for) -> None:
    response = client_for(_docker({})).get(
        "/api/v1/system/logs/download?container=postgres"
    )

    assert response.status_code == 403
