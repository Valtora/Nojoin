"""First-run setup refuses the example FIRST_RUN_PASSWORD from .env.example.

Everyone who copies the template starts with the same bootstrap password, so
until the operator sets their own, setup must not open with it. The refusal has
a detail of its own so the wizard can say what to change. That detail depends
only on the password sent, so it says nothing about whether the system is
initialised or what FIRST_RUN_PASSWORD holds.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from backend import main as backend_main
from backend.api.deps import get_db
from backend.api.v1.endpoints import setup, system
from backend.main import create_app
from backend.utils import telemetry
from backend.utils.deployment_warnings import FIRST_RUN_PASSWORD_PLACEHOLDER

EXAMPLE_PASSWORD = "change_this_before_first_start"
OWN_PASSWORD = "an-operator-chosen-secret"
BASE_URL = "https://test"
EXAMPLE_DENIAL = {
    "detail": "Setup does not accept the example FIRST_RUN_PASSWORD from .env.example."
}
GENERIC_DENIAL = {"detail": "First-run setup access denied."}
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    async def _allow(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(setup, "enforce_setup_rate_limit", _allow)
    monkeypatch.setattr(system, "enforce_setup_rate_limit", _allow)


class _Result:
    def __init__(self, initialized: bool):
        self._initialized = initialized

    def scalar_one_or_none(self):
        return object() if self._initialized else None


class _Session:
    def __init__(self, initialized: bool):
        self._initialized = initialized
        self._added: list = []

    async def execute(self, statement):
        return _Result(self._initialized)

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="fake"))

    def add(self, value):
        self._added.append(value)

    async def commit(self):
        if self._added:
            self._initialized = True

    async def refresh(self, value):
        if getattr(value, "id", None) is None:
            value.id = 1


def _app(*, initialized: bool):
    app = create_app(app_lifespan=None)

    async def _db() -> AsyncGenerator[_Session, None]:
        yield _Session(initialized)

    app.dependency_overrides[get_db] = _db
    return app


def _bootstrap(password: str) -> dict[str, str]:
    return {"Authorization": f"{setup.FIRST_RUN_PASSWORD_AUTH_SCHEME} {password}"}


async def _unlock_and_create(app, password: str) -> list[tuple[int, dict]]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as c:
        unlock = await c.get(
            "/api/v1/setup/initial-config", headers=_bootstrap(password)
        )
        create = await c.post(
            "/api/v1/system/setup",
            headers=_bootstrap(password),
            json={"username": "alice.example", "password": "password123"},
        )
    return [(unlock.status_code, unlock.json()), (create.status_code, create.json())]


@pytest.fixture
def quiet_setup(monkeypatch):
    async def _none(*args, **kwargs):
        return None

    async def _queued(**kwargs):
        return "model-prep-task"

    monkeypatch.setattr(system, "seed_demo_data", _none)
    monkeypatch.setattr(system, "enqueue_model_preparation", _queued)
    monkeypatch.setattr(system, "_save_first_run_whisper_size", lambda size: size)
    monkeypatch.setattr(telemetry, "set_enabled", lambda enabled: None)


def test_the_refused_value_is_the_one_env_example_ships() -> None:
    lines = (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    shipped = [
        line.split("=", 1)[1]
        for line in lines
        if line.startswith("FIRST_RUN_PASSWORD=")
    ]

    assert shipped == [FIRST_RUN_PASSWORD_PLACEHOLDER] == [EXAMPLE_PASSWORD]


@pytest.mark.anyio
async def test_the_example_password_does_not_open_setup(
    monkeypatch, quiet_setup
) -> None:
    monkeypatch.setenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, EXAMPLE_PASSWORD)

    responses = await _unlock_and_create(_app(initialized=False), EXAMPLE_PASSWORD)

    assert responses == [(403, EXAMPLE_DENIAL), (403, EXAMPLE_DENIAL)]


@pytest.mark.anyio
async def test_the_example_denial_is_the_same_in_every_state(
    monkeypatch, quiet_setup
) -> None:
    """Initialised or not, and whatever FIRST_RUN_PASSWORD holds, sending the
    example value gets the same answer, so the answer discloses nothing."""
    responses: list[tuple[int, dict]] = []
    for initialized, configured in [
        (False, EXAMPLE_PASSWORD),
        (False, OWN_PASSWORD),
        (False, None),
        (True, EXAMPLE_PASSWORD),
        (True, OWN_PASSWORD),
    ]:
        if configured is None:
            monkeypatch.delenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, raising=False)
        else:
            monkeypatch.setenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, configured)
        responses += await _unlock_and_create(
            _app(initialized=initialized), EXAMPLE_PASSWORD
        )

    assert set(map(repr, responses)) == {repr((403, EXAMPLE_DENIAL))}


@pytest.mark.anyio
async def test_a_configured_example_value_refuses_every_password(
    monkeypatch, quiet_setup, caplog
) -> None:
    """With the example still in .env, any other password is simply wrong: the
    generic denial, and the reason in the server log."""
    monkeypatch.setenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, EXAMPLE_PASSWORD)

    with caplog.at_level(logging.WARNING, logger=setup.logger.name):
        responses = await _unlock_and_create(_app(initialized=False), OWN_PASSWORD)

    assert responses == [(403, GENERIC_DENIAL), (403, GENERIC_DENIAL)]
    assert "FIRST_RUN_PASSWORD is still the example value" in caplog.text
    assert EXAMPLE_PASSWORD not in caplog.text


@pytest.mark.anyio
async def test_an_operators_own_password_still_opens_setup(
    monkeypatch, quiet_setup
) -> None:
    monkeypatch.setenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, OWN_PASSWORD)

    responses = await _unlock_and_create(_app(initialized=False), OWN_PASSWORD)

    assert [status for status, _ in responses] == [200, 200]


@pytest.mark.anyio
async def test_an_install_already_set_up_with_the_example_value_is_unaffected(
    monkeypatch,
) -> None:
    """An existing install signs in with its session, never the bootstrap
    password, so the example value left in its .env changes nothing."""
    monkeypatch.setenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, EXAMPLE_PASSWORD)

    async def _owner(*args, **kwargs):
        return SimpleNamespace(
            id=1, role="owner", is_superuser=True, force_password_change=False
        )

    monkeypatch.setattr(setup, "get_authenticated_user_from_token", _owner)
    monkeypatch.setattr(
        setup, "enforce_password_change_policy", lambda *args, **kwargs: None
    )

    async with AsyncClient(
        transport=ASGITransport(app=_app(initialized=True)), base_url=BASE_URL
    ) as client:
        response = await client.get(
            "/api/v1/setup/initial-config",
            headers={"Authorization": "Bearer owner-session-token"},
        )

    assert response.status_code == 200


@pytest.mark.anyio
async def test_the_startup_log_says_the_example_value_is_refused(
    monkeypatch, caplog
) -> None:
    class _NoUser:
        async def execute(self, statement):
            return _Result(False)

    @asynccontextmanager
    async def _sessions():
        yield _NoUser()

    monkeypatch.setattr(backend_main, "async_session_maker", _sessions)
    monkeypatch.setattr(
        backend_main, "get_configured_web_origin", lambda: "https://nojoin.example.com"
    )
    monkeypatch.setenv("FIRST_RUN_PASSWORD", EXAMPLE_PASSWORD)

    with caplog.at_level(logging.INFO, logger="backend.main"):
        await backend_main.log_first_run_setup_pointer()

    assert "https://nojoin.example.com/setup" in caplog.text
    assert "still the example value from .env.example" in caplog.text
    assert EXAMPLE_PASSWORD not in caplog.text
