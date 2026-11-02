"""First-run setup under a NOJOIN_TELEMETRY_ENABLED pin (#838).

The environment outranks the UI in both directions (docs/TELEMETRY.md), so a
pinned install's wizard has nothing to ask. The unlock response says what the
pin is, so the wizard can show it read-only as Settings > Privacy does, and
setup stores no choice under a pin: the operator was shown a value, not asked
for one. Nor does it store one when setup sends none, which a wizard that saw a
pin still does after the pin is removed and the API restarted (#854).
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.deps import get_db
from backend.api.v1.endpoints import setup, system
from backend.main import create_app
from backend.utils import telemetry
from backend.utils.config_manager import ConfigManager

BOOTSTRAP_PASSWORD = "an-operator-chosen-secret"
BASE_URL = "https://test"


class _Result:
    def __init__(self, initialized: bool):
        self._initialized = initialized

    def scalar_one_or_none(self):
        return object() if self._initialized else None


class _Session:
    """Just enough session for unlock and setup on an empty install."""

    def __init__(self) -> None:
        self._initialized = False

    async def execute(self, statement):
        return _Result(self._initialized)

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="fake"))

    def add(self, value) -> None:
        self._initialized = True

    async def commit(self) -> None:
        return None

    async def refresh(self, value) -> None:
        value.id = 1


@pytest.fixture
def telemetry_config(tmp_path, monkeypatch) -> ConfigManager:
    """Telemetry's config.json, isolated, with the rest of setup kept quiet."""
    manager = ConfigManager(config_path=str(tmp_path / "config.json"))
    monkeypatch.setattr(telemetry, "config_manager", manager)
    monkeypatch.delenv(telemetry.TELEMETRY_ENABLED_ENV_KEY, raising=False)
    monkeypatch.setenv(setup.FIRST_RUN_PASSWORD_ENV_KEY, BOOTSTRAP_PASSWORD)

    async def _none(*args, **kwargs) -> None:
        return None

    async def _queued(**kwargs) -> str:
        return "model-prep-task"

    monkeypatch.setattr(setup, "enforce_setup_rate_limit", _none)
    monkeypatch.setattr(system, "enforce_setup_rate_limit", _none)
    monkeypatch.setattr(system, "seed_demo_data", _none)
    monkeypatch.setattr(system, "enqueue_model_preparation", _queued)
    monkeypatch.setattr(system, "_save_first_run_whisper_size", lambda size: size)
    return manager


def _stored(manager: ConfigManager) -> dict:
    """The telemetry keys in config.json as written, defaults included."""
    data = json.loads(Path(manager.config_path).read_text(encoding="utf-8"))
    return {key: value for key, value in data.items() if key.startswith("telemetry_")}


def _assert_no_choice_stored(manager: ConfigManager, before: dict) -> None:
    """Nothing was written, so the admin notice decides, as on an upgrade."""
    assert _stored(manager) == before
    assert before[telemetry.ACKNOWLEDGED_CONFIG_KEY] is False
    assert before[telemetry.POLICY_CONFIG_KEY] is None
    assert telemetry.notice_pending() is True
    assert telemetry.should_send() is False


async def _unlock_and_set_up(
    setup_body: dict, between: Callable[[], None] | None = None
) -> tuple[dict, int]:
    """Unlock the wizard, run ``between`` if given, then finish setup."""
    app = create_app(app_lifespan=None)
    session = _Session()

    async def _db() -> AsyncGenerator[_Session, None]:
        yield session

    app.dependency_overrides[get_db] = _db
    headers = {"Authorization": f"Bootstrap {BOOTSTRAP_PASSWORD}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as c:
        unlock = await c.get("/api/v1/setup/initial-config", headers=headers)
        assert unlock.status_code == 200
        if between is not None:
            between()
        created = await c.post(
            "/api/v1/system/setup",
            headers=headers,
            json={
                "username": "alice.example",
                "password": "password123",
                "include_demo_recording": False,
                **setup_body,
            },
        )
    return unlock.json(), created.status_code


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("raw", "pinned", "sent"),
    [
        # The case in #838: the box showed ticked, and its tick was stored.
        ("false", False, {}),
        ("false", False, {"enable_telemetry": True}),
        # An untick under a true pin was stored too, and never honoured.
        ("true", True, {"enable_telemetry": False}),
        ("TRUE", True, {}),
    ],
)
async def test_a_pinned_install_is_told_the_pin_and_stores_no_choice(
    telemetry_config, monkeypatch, raw, pinned, sent
) -> None:
    monkeypatch.setenv(telemetry.TELEMETRY_ENABLED_ENV_KEY, raw)
    before = _stored(telemetry_config)

    config, status = await _unlock_and_set_up(sent)

    assert status == 200
    assert config["telemetry_pinned"] is pinned
    # Dropping the pin later leaves the diagnostics decision to the admin
    # notice, as on an upgraded install, rather than to consent nobody gave.
    monkeypatch.delenv(telemetry.TELEMETRY_ENABLED_ENV_KEY)
    _assert_no_choice_stored(telemetry_config, before)


@pytest.mark.anyio
@pytest.mark.parametrize("raw", [None, "", "maybe"])
@pytest.mark.parametrize("choice", [True, False])
async def test_without_a_pin_the_wizard_choice_is_stored(
    telemetry_config, monkeypatch, raw, choice
) -> None:
    """Empty or unrecognised is no pin, as Settings > Privacy reads it."""
    if raw is not None:
        monkeypatch.setenv(telemetry.TELEMETRY_ENABLED_ENV_KEY, raw)

    config, status = await _unlock_and_set_up({"enable_telemetry": choice})

    assert config["telemetry_pinned"] is None
    assert status == 200
    stored = _stored(telemetry_config)
    assert stored[telemetry.ENABLED_CONFIG_KEY] is choice
    assert stored[telemetry.ACKNOWLEDGED_CONFIG_KEY] is True
    assert stored[telemetry.POLICY_CONFIG_KEY] == telemetry.HEARTBEAT_POLICY_VERSION
    assert telemetry.notice_pending() is False
    assert telemetry.should_send() is choice


@pytest.mark.anyio
@pytest.mark.parametrize("sent", [{}, {"enable_telemetry": None}])
async def test_setup_that_sends_no_choice_stores_none(telemetry_config, sent) -> None:
    """No choice sent is not a tick, though the model's default once read it as one."""
    before = _stored(telemetry_config)

    config, status = await _unlock_and_set_up(sent)

    assert config["telemetry_pinned"] is None
    assert status == 200
    _assert_no_choice_stored(telemetry_config, before)


@pytest.mark.anyio
async def test_a_pin_removed_while_the_wizard_is_open_stores_no_choice(
    telemetry_config, monkeypatch
) -> None:
    """The wizard saw the pin and sends no choice, although none is set now."""
    monkeypatch.setenv(telemetry.TELEMETRY_ENABLED_ENV_KEY, "false")
    before = _stored(telemetry_config)

    def remove_the_pin() -> None:
        monkeypatch.delenv(telemetry.TELEMETRY_ENABLED_ENV_KEY)

    config, status = await _unlock_and_set_up({}, between=remove_the_pin)

    assert config["telemetry_pinned"] is False
    assert status == 200
    assert telemetry.is_env_managed() is False
    _assert_no_choice_stored(telemetry_config, before)
