"""Speaker engine selection is install-wide and never stored as a user override."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from backend.api.v1.endpoints import settings as settings_ep
from backend.tests.test_install_wide_transcription_settings import (
    _call_settings,
    _config_file,
)
from backend.tests.test_install_wide_transcription_settings import (
    install_config as install_config,
)
from backend.utils.config_manager import ConfigManager

SPEAKER_SETTINGS = {"speaker_engine": "classic", "live_speaker_labels": "auto"}


@pytest.mark.parametrize("role", ["admin", "owner"])
def test_admin_selection_persists_only_to_install_config(
    install_config: ConfigManager, role: str
) -> None:
    status, body, row = asyncio.run(_call_settings(role, {}, "POST", SPEAKER_SETTINGS))
    assert status == 200
    for key, expected in SPEAKER_SETTINGS.items():
        assert _config_file(install_config)[key] == expected
        assert body[key] == expected
        assert key not in row


def test_non_admin_cannot_change_install_selection_or_keep_stale_overrides(
    install_config: ConfigManager,
) -> None:
    before = _config_file(install_config)
    status, body, row = asyncio.run(
        _call_settings("user", SPEAKER_SETTINGS, "POST", SPEAKER_SETTINGS)
    )
    assert status == 200
    assert _config_file(install_config) == before
    assert body["speaker_engine"] == "auto"
    assert body["live_speaker_labels"] == "off"
    assert not set(SPEAKER_SETTINGS).intersection(row)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("speaker_engine", "clustering"),
        ("speaker_engine", "AUTO"),
        ("speaker_engine", ""),
        ("live_speaker_labels", "on"),
        ("live_speaker_labels", ""),
    ],
)
def test_invalid_selection_is_rejected_without_saving(
    install_config: ConfigManager, key: str, value: str
) -> None:
    before = _config_file(install_config)
    status, _, row = asyncio.run(_call_settings("admin", {}, "POST", {key: value}))
    assert status == 422
    assert _config_file(install_config) == before
    assert row == {}


def test_null_selection_leaves_the_install_choice_in_place(
    install_config: ConfigManager,
) -> None:
    asyncio.run(_call_settings("admin", {}, "POST", SPEAKER_SETTINGS))
    status, body, row = asyncio.run(
        _call_settings("owner", {}, "POST", {key: None for key in SPEAKER_SETTINGS})
    )
    assert status == 200
    for key, expected in SPEAKER_SETTINGS.items():
        assert _config_file(install_config)[key] == expected
        assert body[key] == expected
        assert key not in row


def test_owner_row_never_supplies_install_speaker_defaults() -> None:
    merged: dict[str, Any] = {}
    settings_ep._apply_install_wide_ai_owner_fallback(merged, SPEAKER_SETTINGS)
    assert not set(SPEAKER_SETTINGS).intersection(merged)


def test_existing_config_without_selection_gets_auto_but_labels_stay_off(
    install_config: ConfigManager,
) -> None:
    status, body, _ = asyncio.run(_call_settings("user", SPEAKER_SETTINGS, "GET"))
    assert status == 200
    assert body["speaker_engine"] == "auto"
    assert body["live_speaker_labels"] == "off"


def test_config_file_keeps_an_explicit_classic_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ConfigManager, "_ensure_dirs_exist", lambda self, cfg: None)
    config_path = tmp_path / "config.json"
    config_path.write_text('{"speaker_engine":"classic"}', encoding="utf-8")
    manager = ConfigManager(config_path=str(config_path))
    assert manager.get("speaker_engine") == "classic"
