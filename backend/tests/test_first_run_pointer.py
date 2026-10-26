from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace

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


async def _no_owner(session):
    return None


@pytest.mark.anyio
async def test_owner_check_is_quiet_on_an_empty_install(monkeypatch, caplog) -> None:
    # Before first-run setup there is nobody to promote, and that is the state
    # every install starts in, so it is not worth a warning.
    monkeypatch.setattr(backend_main, "async_session_maker", _fake_session_maker(None))
    monkeypatch.setattr(backend_main, "get_primary_owner_async", _no_owner)

    with caplog.at_level(logging.INFO, logger="backend.main"):
        await backend_main.ensure_owner_exists()

    assert caplog.records == []


class _NoUsersResult:
    def scalars(self):
        return self

    def first(self):
        return None


class _NoUsersSession:
    async def execute(self, statement):
        return _NoUsersResult()

    async def get(self, model, key):
        return None


@asynccontextmanager
async def _no_users_session_maker():
    yield _NoUsersSession()


@pytest.mark.anyio
async def test_demo_seed_is_quiet_on_an_empty_install(
    monkeypatch, caplog, tmp_path
) -> None:
    import backend.seed_demo as seed_demo

    monkeypatch.setattr(seed_demo, "async_session_maker", _no_users_session_maker)
    monkeypatch.setattr(
        seed_demo,
        "PathManager",
        lambda: SimpleNamespace(recordings_directory=str(tmp_path)),
    )

    with caplog.at_level(logging.INFO, logger="backend.seed_demo"):
        await seed_demo.seed_demo_data()

    assert [record.levelno for record in caplog.records] == [
        logging.INFO,
        logging.INFO,
    ]
    assert "No users yet" in caplog.text


@pytest.mark.anyio
async def test_demo_seed_for_a_missing_user_is_an_error(
    monkeypatch, caplog, tmp_path
) -> None:
    import backend.seed_demo as seed_demo

    monkeypatch.setattr(seed_demo, "async_session_maker", _no_users_session_maker)
    monkeypatch.setattr(
        seed_demo,
        "PathManager",
        lambda: SimpleNamespace(recordings_directory=str(tmp_path)),
    )

    with caplog.at_level(logging.INFO, logger="backend.seed_demo"):
        await seed_demo.seed_demo_data(user_id=42)

    assert "user 42 not found" in caplog.text
    assert caplog.records[-1].levelno == logging.ERROR
