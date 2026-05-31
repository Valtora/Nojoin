import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from unittest.mock import MagicMock
from starlette.websockets import WebSocketDisconnect

from backend.api.v1.endpoints import system
from backend.api.deps import get_current_active_superuser, get_current_active_superuser_ws
from backend.main import create_app


# Mock Docker Client
class MockContainer:
    def __init__(self, name):
        self.name = name

    def logs(self, *args, **kwargs):
        if kwargs.get("stream"):
            return [b"line 1\n", b"line 2\n"]
        return b"mock logs content"


class MockContainers:
    def get(self, name):
        return MockContainer(name)


class MockDockerClient:
    def __init__(self):
        self.containers = MockContainers()


@pytest.fixture
def test_app(monkeypatch):
    app = create_app(app_lifespan=None)

    # Mock user dependencies
    async def mock_superuser():
        return MagicMock(is_superuser=True, role="admin")

    app.dependency_overrides[get_current_active_superuser] = mock_superuser
    app.dependency_overrides[get_current_active_superuser_ws] = mock_superuser

    # Monkeypatch docker client in system endpoint
    mock_docker = MockDockerClient()
    monkeypatch.setattr(system, "client", mock_docker)

    return app


def test_download_logs_allowed(test_app):
    client = TestClient(test_app)
    response = client.get("/api/v1/system/logs/download?container=nojoin-api")
    assert response.status_code == 200
    assert "mock logs content" in response.text


def test_download_logs_forbidden(test_app):
    client = TestClient(test_app)
    response = client.get("/api/v1/system/logs/download?container=unrelated-container")
    assert response.status_code == 403
    assert response.json()["detail"] == "Forbidden"


def test_websocket_logs_allowed(test_app):
    client = TestClient(test_app)
    # The client can connect to allowed container
    with client.websocket_connect("/api/v1/system/logs/live?container=nojoin-api") as websocket:
        # Check that it gets lines from container
        # Since stream returns [b"line 1\n", b"line 2\n"], it should be processed.
        msg1 = websocket.receive_text()
        assert "[nojoin-api]" in msg1
        assert "line 1" in msg1


def test_websocket_logs_forbidden(test_app):
    client = TestClient(test_app)
    # The client should be closed with code 1008 immediately if the container is not allowed
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/api/v1/system/logs/live?container=unrelated-container") as websocket:
            websocket.receive_text()
    
    assert excinfo.value.code == 1008
