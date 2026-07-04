from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import pytest

from backend import main as backend_main


class _FakeResult:
    def __init__(self, user):
        self._user = user

    def scalar_one_or_none(self):
        return self._user


class _FakeSession:
    def __init__(self, user):
        self._user = user

    async def execute(self, statement):
        return _FakeResult(self._user)


def _fake_session_maker(user):
    @asynccontextmanager
    async def _maker():
        yield _FakeSession(user)

    return _maker


@pytest.mark.anyio
async def test_startup_pointer_logged_while_uninitialised(monkeypatch, caplog) -> None:
    monkeypatch.setattr(backend_main, "async_session_maker", _fake_session_maker(None))
    monkeypatch.setattr(
        backend_main,
        "get_configured_web_origin",
        lambda: "https://nojoin.example.com",
    )
    monkeypatch.setenv("FIRST_RUN_PASSWORD", "bootstrap-secret")

    with caplog.at_level(logging.INFO, logger="backend.main"):
        await backend_main.log_first_run_setup_pointer()

    assert "https://nojoin.example.com/setup" in caplog.text
    assert "FIRST_RUN_PASSWORD is not set" not in caplog.text


@pytest.mark.anyio
async def test_startup_pointer_warns_when_password_unset(monkeypatch, caplog) -> None:
    monkeypatch.setattr(backend_main, "async_session_maker", _fake_session_maker(None))
    monkeypatch.setattr(
        backend_main,
        "get_configured_web_origin",
        lambda: "https://nojoin.example.com",
    )
    monkeypatch.delenv("FIRST_RUN_PASSWORD", raising=False)

    with caplog.at_level(logging.INFO, logger="backend.main"):
        await backend_main.log_first_run_setup_pointer()

    assert "https://nojoin.example.com/setup" in caplog.text
    assert "FIRST_RUN_PASSWORD is not set" in caplog.text


@pytest.mark.anyio
async def test_startup_pointer_silent_once_initialised(monkeypatch, caplog) -> None:
    monkeypatch.setattr(
        backend_main, "async_session_maker", _fake_session_maker(object())
    )

    with caplog.at_level(logging.INFO, logger="backend.main"):
        await backend_main.log_first_run_setup_pointer()

    assert "/setup" not in caplog.text
