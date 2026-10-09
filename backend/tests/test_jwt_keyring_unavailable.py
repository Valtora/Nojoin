"""An unloadable JWT keyring is reported at startup without stopping the api,
and sign-in and token verification fail closed with an actionable error."""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from backend import main
from backend.api.deps import get_db
from backend.api.v1.endpoints import login
from backend.core import security

TRUSTED_ORIGIN = "https://nojoin.example.com"


class _StartupContinued(Exception):
    """Raised by the first stubbed startup step that would reach Postgres."""


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def keyring_file(monkeypatch, tmp_path):
    """A keyring inside ``tmp_path`` whose active key is empty."""
    monkeypatch.delenv("SECRET_KEY", raising=False)

    class _StubPathManager:
        user_data_directory = tmp_path

    monkeypatch.setattr(security, "path_manager", _StubPathManager())
    path = tmp_path / ".secret_keys.json"
    path.write_text(
        json.dumps({"active": "legacy", "keys": {"legacy": ""}}), encoding="utf-8"
    )
    return path


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setenv("WEB_APP_URL", TRUSTED_ORIGIN)

    async def no_rate_limit(*args, **kwargs):
        return None

    async def alice(*args, **kwargs):
        return SimpleNamespace(
            username="alice",
            token_version=0,
            force_password_change=False,
            is_superuser=False,
        )

    async def no_database():
        # Any use of this object fails, so a request that gets past token
        # verification errors instead of reaching a database.
        yield object()

    monkeypatch.setattr(login, "enforce_rate_limit", no_rate_limit)
    monkeypatch.setattr(login, "_authenticate_user_credentials", alice)
    app = main.create_app(app_lifespan=None)
    app.dependency_overrides[get_db] = no_database
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=TRUSTED_ORIGIN
    ) as async_client:
        yield async_client


@pytest.mark.anyio
async def test_api_startup_logs_an_empty_signing_key_and_carries_on(
    keyring_file, monkeypatch, caplog
):
    def stop_before_the_database():
        raise _StartupContinued

    monkeypatch.setattr(main, "run_migrations", stop_before_the_database)

    with caplog.at_level(logging.ERROR, logger=main.logger.name):
        with pytest.raises(_StartupContinued):
            async with main.lifespan(main.app):
                pass

    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any(
        f"The JWT signing key in {keyring_file} is empty" in m
        and f"Delete {keyring_file} and restart" in m
        for m in messages
    ), messages


@pytest.mark.anyio
async def test_api_startup_logs_a_non_string_signing_key(
    keyring_file, monkeypatch, caplog
):
    keyring_file.write_text(
        json.dumps({"active": "legacy", "keys": {"legacy": 123}}), encoding="utf-8"
    )

    def stop_before_the_database():
        raise _StartupContinued

    monkeypatch.setattr(main, "run_migrations", stop_before_the_database)

    with caplog.at_level(logging.ERROR, logger=main.logger.name):
        with pytest.raises(_StartupContinued):
            async with main.lifespan(main.app):
                pass

    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any(
        f"The JWT key file {keyring_file} is malformed" in m for m in messages
    ), messages


@pytest.mark.anyio
async def test_api_startup_carries_on_when_the_keyring_check_fails_unexpectedly(
    monkeypatch, caplog
):
    def unexpected_failure():
        raise RuntimeError("Could not determine home directory.")

    def stop_before_the_database():
        raise _StartupContinued

    monkeypatch.setattr(main, "get_signing_keyring", unexpected_failure)
    monkeypatch.setattr(main, "run_migrations", stop_before_the_database)

    with caplog.at_level(logging.ERROR, logger=main.logger.name):
        with pytest.raises(_StartupContinued):
            async with main.lifespan(main.app):
                pass

    assert any(
        "keyring check failed to run" in r.getMessage() and r.exc_info
        for r in caplog.records
    ), [r.getMessage() for r in caplog.records]


@pytest.mark.anyio
async def test_sign_in_with_an_empty_signing_key_fails_with_an_actionable_error(
    keyring_file, client, caplog
):
    with caplog.at_level(logging.ERROR, logger=main.logger.name):
        response = await client.post(
            "/api/v1/login/session",
            data={"username": "alice", "password": "irrelevant"},
            headers={"Origin": TRUSTED_ORIGIN},
        )

    assert response.status_code == 500
    assert response.json() == {"detail": main.SIGNING_KEY_UNAVAILABLE_DETAIL}
    assert "access_token" not in response.cookies
    # The client sees no server path; the log names the file to delete.
    assert str(keyring_file) not in response.text
    assert any(str(keyring_file) in r.getMessage() for r in caplog.records)


@pytest.mark.anyio
async def test_tokens_are_401_while_the_signing_key_is_empty(keyring_file, client):
    keyring_file.unlink()
    token = security.create_access_token(
        "alice",
        token_type=security.SESSION_TOKEN_TYPE,
        scopes=[security.WEB_SESSION_SCOPE],
        expires_delta=timedelta(minutes=5),
        token_version=0,
    )
    # A write cut short leaves the keyring blank.
    keyring_file.write_text("", encoding="utf-8")

    response = await client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 401
