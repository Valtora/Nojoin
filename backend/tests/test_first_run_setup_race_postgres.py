"""Two requests finishing first-run setup at once create one owner, on PostgreSQL.

Setup checks that no user exists and then creates the owner. Two browsers that
submit the account step at the same moment would each pass the check before
either committed, and each create an owner. ``lock_first_run_setup`` holds the
second until the first commits, so its check sees the first owner and it is
refused with the usual denial. Skips unless NOJOIN_TEST_POSTGRES_URL names a
server.
"""

from __future__ import annotations

import asyncio
import importlib
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import Session, select
from starlette.requests import Request

from backend.api.v1.endpoints import setup, system
from backend.core.db import driver_urls
from backend.models.user import User
from backend.tests.postgres_setup import fresh_schema
from backend.utils import telemetry

SCHEMA = "first_run_setup_race_test"
BOOTSTRAP_PASSWORD = "bootstrap-secret"


@pytest.fixture
def engine(postgres_test_url: str):
    importlib.import_module("backend.models.registry")
    engine = create_engine(
        driver_urls(postgres_test_url)[1],
        connect_args={"options": f"-csearch_path={SCHEMA},public"},
    )
    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    fresh_schema(engine, SCHEMA)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def quiet_setup(monkeypatch):
    """Stub what setup does after the owner exists; only the owner matters here."""

    async def _allow(*args, **kwargs) -> None:
        return None

    async def _queued(**kwargs) -> str:
        return "model-prep-task"

    monkeypatch.setenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, BOOTSTRAP_PASSWORD)
    monkeypatch.setattr(system, "enforce_setup_rate_limit", _allow)
    monkeypatch.setattr(system, "seed_demo_data", _allow)
    monkeypatch.setattr(system, "enqueue_model_preparation", _queued)
    monkeypatch.setattr(system, "_save_first_run_whisper_size", lambda size: size)
    monkeypatch.setattr(telemetry, "set_enabled", lambda enabled: None)


@asynccontextmanager
async def _two_sessions(postgres_test_url: str):
    engine = create_async_engine(
        driver_urls(postgres_test_url)[0],
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    try:
        async with (
            AsyncSession(engine, expire_on_commit=False) as first,
            AsyncSession(engine, expire_on_commit=False) as second,
        ):
            yield first, second
    finally:
        await engine.dispose()


def _bootstrap_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/system/setup",
            "headers": [
                (
                    b"authorization",
                    f"{setup.FIRST_RUN_PASSWORD_AUTH_SCHEME} {BOOTSTRAP_PASSWORD}".encode(),
                )
            ],
            "client": ("203.0.113.5", 12345),
        }
    )


def _owners(engine) -> list[str]:
    with Session(engine) as session:
        return sorted(session.exec(select(User.username)).all())


@pytest.mark.anyio
async def test_two_setups_at_once_create_one_owner(
    engine, quiet_setup, postgres_test_url, monkeypatch
) -> None:
    # Each request waits, after its own check, for the other to check too,
    # for up to two seconds. Without the lock both checks see no user and both
    # requests go on to create one. With it, the second check cannot run until
    # the first request has committed, so the first gives up waiting.
    real_check = system.is_system_initialized
    checks: list[bool] = []
    both_checked = asyncio.Event()

    async def check_together(db) -> bool:
        initialised = await real_check(db)
        checks.append(initialised)
        if len(checks) == 2:
            both_checked.set()
        try:
            await asyncio.wait_for(both_checked.wait(), timeout=2)
        except TimeoutError:
            pass
        return initialised

    monkeypatch.setattr(system, "is_system_initialized", check_together)

    async with _two_sessions(postgres_test_url) as (first, second):
        results = await asyncio.gather(
            system.setup_system(
                request=_bootstrap_request(),
                db=first,
                setup_in=system.SetupRequest(
                    username="alice.example", password="password123"
                ),
            ),
            system.setup_system(
                request=_bootstrap_request(),
                db=second,
                setup_in=system.SetupRequest(
                    username="bob.example", password="password123"
                ),
            ),
            return_exceptions=True,
        )

    outcomes = []
    for result in results:
        if isinstance(result, HTTPException):
            assert result.status_code == 403
            assert result.detail == setup.FIRST_RUN_SETUP_ACCESS_DENIED_DETAIL
            outcomes.append("refused")
        elif isinstance(result, BaseException):
            raise result
        else:
            assert result["initialized"] is True
            outcomes.append("created")

    assert sorted(outcomes) == ["created", "refused"]
    assert checks == [False, True]
    assert len(_owners(engine)) == 1
