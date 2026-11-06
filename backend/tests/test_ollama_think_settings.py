"""``ollama_think`` and ``secondary_ollama_think`` are install-wide settings.

Thinking is off until an administrator turns it on under Settings > AI
providers. The choice goes to config.json and applies to every user, and a
request from anyone else is dropped as the other install-wide AI keys are.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from backend.api.v1.endpoints import settings as settings_ep
from backend.tests.test_install_wide_transcription_settings import _call_settings
from backend.utils.config_manager import ConfigManager
from backend.utils.llm_config import LLM_PURPOSE_DEFAULT, _merge_llm_config

KEYS = ("ollama_think", "secondary_ollama_think")


@pytest.fixture
def config(tmp_path: Path, monkeypatch) -> ConfigManager:
    """A real config.json in a temporary directory, used by the settings routes."""
    monkeypatch.setattr(ConfigManager, "_ensure_dirs_exist", lambda self, cfg: None)
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    manager = ConfigManager(config_path=str(path))
    monkeypatch.setattr(settings_ep, "config_manager", manager)
    return manager


def _saved(manager: ConfigManager) -> dict:
    return json.loads(Path(manager.config_path).read_text(encoding="utf-8"))


def test_thinking_is_off_for_an_install_that_never_chose(config):
    status, body, _ = asyncio.run(_call_settings("user", {}, "GET"))

    assert status == 200
    assert [body[key] for key in KEYS] == [False, False]


def test_an_admin_turns_thinking_on_for_the_install(config):
    status, body, row = asyncio.run(
        _call_settings(
            "admin",
            {},
            "POST",
            {"ollama_think": True, "secondary_ollama_think": True},
        )
    )

    assert status == 200
    saved = _saved(config)
    assert [saved[key] for key in KEYS] == [True, True]
    assert [body[key] for key in KEYS] == [True, True]
    assert not set(KEYS) & set(row)

    resolved = _merge_llm_config(
        base_config={
            **config.get_all(),
            "llm_provider": "ollama",
            "secondary_llm_provider": "ollama",
        },
        system_keys={},
        owner_settings=None,
        user_settings=None,
        purpose=LLM_PURPOSE_DEFAULT,
    )
    assert resolved.think is True
    assert resolved.secondary_think is True


def test_it_can_be_turned_off_again(config):
    asyncio.run(_call_settings("admin", {}, "POST", {"ollama_think": True}))

    status, body, _ = asyncio.run(
        _call_settings("admin", {}, "POST", {"ollama_think": False})
    )

    assert status == 200
    assert _saved(config)["ollama_think"] is False
    assert body["ollama_think"] is False


def test_a_non_admin_cannot_turn_thinking_on(config):
    status, body, row = asyncio.run(
        _call_settings("user", {}, "POST", {"ollama_think": True})
    )

    # Dropped without an error, as the install-wide LLM keys are.
    assert status == 200
    assert "ollama_think" not in _saved(config)
    assert body["ollama_think"] is False
    assert "ollama_think" not in row


def test_a_value_that_is_not_a_boolean_is_refused(config):
    status, _, _ = asyncio.run(
        _call_settings("admin", {}, "POST", {"ollama_think": "maybe"})
    )

    assert status == 422
    assert "ollama_think" not in _saved(config)
