"""The log endpoints open their Docker client on first use, not at import (#800).

Opening a client asks the daemon for its API version. Done at import, every
process that built the app reached for the Docker socket, and a socket proxy
that was not answering when the API started left log downloads at 503 until
the API restarted.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from unittest.mock import MagicMock

import pytest
from docker.errors import DockerException
from fastapi.testclient import TestClient

from backend.api.deps import (
    get_current_active_superuser,
    get_current_active_superuser_ws,
)
from backend.api.v1.endpoints import system
from backend.main import create_app

DOWNLOAD = "/api/v1/system/logs/download?container=nojoin-api"


@pytest.fixture
def api():
    system.get_docker_client.cache_clear()
    app = create_app(app_lifespan=None)

    async def superuser():
        return MagicMock(is_superuser=True, role="owner")

    app.dependency_overrides[get_current_active_superuser] = superuser
    app.dependency_overrides[get_current_active_superuser_ws] = superuser
    yield TestClient(app)
    system.get_docker_client.cache_clear()


def test_building_the_app_opens_no_docker_client() -> None:
    # A fresh interpreter, so the import really runs. Every Docker client goes
    # through APIClient, which is refused here.
    script = textwrap.dedent(
        """
        from docker.api.client import APIClient

        def refuse(self, *args, **kwargs):
            raise AssertionError("a Docker client was opened")

        APIClient.__init__ = refuse

        from backend.main import create_app

        create_app(app_lifespan=None)
        print("NO_DOCKER_CLIENT")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert "NO_DOCKER_CLIENT" in result.stdout, result.stderr


def test_a_failed_connection_is_retried_on_the_next_request(api, monkeypatch) -> None:
    docker_client = MagicMock()
    docker_client.containers.get.return_value.logs.return_value = [
        b"2026-10-29T09:00:00.000000000Z ready\n"
    ]
    attempts: list[int] = []

    def from_env():
        attempts.append(1)
        if len(attempts) == 1:
            raise DockerException("Error while fetching server API version")
        return docker_client

    monkeypatch.setattr(system.docker, "from_env", from_env)

    first = api.get(DOWNLOAD)
    assert first.status_code == 503
    assert first.json()["detail"] == "Docker client unavailable"

    second = api.get(DOWNLOAD)
    assert second.status_code == 200
    assert second.text == "2026-10-29T09:00:00.000000000Z ready\n"

    # Once open, the client is kept.
    assert api.get(DOWNLOAD).status_code == 200
    assert len(attempts) == 2


def test_live_logs_say_why_docker_is_unavailable(api, monkeypatch) -> None:
    def from_env():
        raise DockerException("socket proxy not answering")

    monkeypatch.setattr(system.docker, "from_env", from_env)

    with api.websocket_connect(
        "/api/v1/system/logs/live?container=nojoin-api"
    ) as websocket:
        assert (
            websocket.receive_text()
            == "Error: Docker client unavailable. socket proxy not answering"
        )
