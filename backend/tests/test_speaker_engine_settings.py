"""Speaker engine selection is install-wide and never stored as a user override."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

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


@pytest.mark.parametrize("role", ["user", "admin", "owner"])
@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (
            {"speaker_engine": "AUTO", "live_speaker_labels": "on"},
            {"speaker_engine": "auto", "live_speaker_labels": "off"},
        ),
        (
            {"speaker_engine": " CLASSIC ", "live_speaker_labels": " AUTO "},
            {"speaker_engine": "auto", "live_speaker_labels": "auto"},
        ),
        (
            {"speaker_engine": ["classic"], "live_speaker_labels": {"bad": True}},
            {"speaker_engine": "auto", "live_speaker_labels": "off"},
        ),
        (
            {"speaker_engine": None, "live_speaker_labels": None},
            {"speaker_engine": "auto", "live_speaker_labels": "off"},
        ),
    ],
)
def test_stored_speaker_values_report_runtime_choices_and_allow_full_object_saves(
    install_config: ConfigManager,
    role: str,
    stored: dict[str, Any],
    expected: dict[str, str],
) -> None:
    path = Path(install_config.config_path)
    path.write_text(json.dumps({**_config_file(install_config), **stored}))
    install_config.reload(force=True)
    before = path.read_bytes()

    status, body, _ = asyncio.run(_call_settings(role, SPEAKER_SETTINGS, "GET"))
    assert status == 200
    assert {key: body[key] for key in expected} == expected
    assert path.read_bytes() == before

    body["prefer_short_titles"] = False
    status, saved_body, row = asyncio.run(
        _call_settings(role, SPEAKER_SETTINGS, "POST", body)
    )
    assert status == 200
    assert row["prefer_short_titles"] is False
    assert {key: saved_body[key] for key in expected} == expected
    assert not set(SPEAKER_SETTINGS).intersection(row)
    if role == "user":
        assert path.read_bytes() == before


@pytest.mark.parametrize("path", ["/settings", "/settings/"])
def test_stale_non_admin_payload_ignores_install_keys_before_validation(
    install_config: ConfigManager, path: str
) -> None:
    before = Path(install_config.config_path).read_bytes()
    status, body, row = asyncio.run(
        _call_settings(
            "user",
            SPEAKER_SETTINGS,
            "POST",
            {
                "speaker_engine": "AUTO",
                "live_speaker_labels": "on",
                "llm_provider": ["invalid"],
                "ai_analysis_daily_limit": -1,
                "install_notes_template_id": "not-an-id",
                "prefer_short_titles": False,
            },
            options={"path": path},
        )
    )
    assert status == 200
    assert row["prefer_short_titles"] is False
    assert not set(settings_ep.INSTALL_WIDE_ONLY_USER_SETTING_KEYS).intersection(row)
    assert Path(install_config.config_path).read_bytes() == before
    assert body["speaker_engine"] == "auto"
    assert body["live_speaker_labels"] == "off"


def test_null_install_choices_report_the_runtime_environment_fallback(
    install_config: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(install_config.config, "speaker_engine", None)
    monkeypatch.setitem(install_config.config, "live_speaker_labels", None)
    monkeypatch.setenv("SPEAKER_ENGINE", "classic")
    monkeypatch.setenv("LIVE_SPEAKER_LABELS", " AUTO ")
    before = Path(install_config.config_path).read_bytes()
    status, body, _ = asyncio.run(_call_settings("user", SPEAKER_SETTINGS, "GET"))
    assert status == 200
    assert body["speaker_engine"] == "classic"
    assert body["live_speaker_labels"] == "auto"
    assert Path(install_config.config_path).read_bytes() == before


@pytest.mark.parametrize(
    ("role", "is_superuser"), [("admin", False), ("owner", False), ("user", True)]
)
@pytest.mark.parametrize("path", ["/settings", "/settings/"])
def test_administrator_requests_keep_strict_speaker_validation(
    install_config: ConfigManager, role: str, is_superuser: bool, path: str
) -> None:
    before = Path(install_config.config_path).read_bytes()
    status, body, row = asyncio.run(
        _call_settings(
            role,
            {},
            "POST",
            {"speaker_engine": "AUTO", "live_speaker_labels": "on"},
            options={"is_superuser": is_superuser, "path": path},
        )
    )
    assert status == 422
    assert {tuple(error["loc"]) for error in body["detail"]} == {
        ("body", "speaker_engine"),
        ("body", "live_speaker_labels"),
    }
    assert Path(install_config.config_path).read_bytes() == before
    assert row == {}


def test_superuser_with_user_role_can_save_install_choices(
    install_config: ConfigManager,
) -> None:
    status, body, row = asyncio.run(
        _call_settings(
            "user", {}, "POST", SPEAKER_SETTINGS, options={"is_superuser": True}
        )
    )
    assert status == 200
    assert {key: _config_file(install_config)[key] for key in SPEAKER_SETTINGS} == (
        SPEAKER_SETTINGS
    )
    assert {key: body[key] for key in SPEAKER_SETTINGS} == SPEAKER_SETTINGS
    assert not set(SPEAKER_SETTINGS).intersection(row)


@pytest.mark.parametrize(
    ("payload", "location"),
    [
        ({"theme": "invalid", "speaker_engine": "AUTO"}, ["body", "theme"]),
        ([{"theme": "dark"}], ["body"]),
        ("invalid", ["body"]),
        (None, ["body"]),
    ],
)
def test_non_admin_requests_still_validate_user_fields_and_body_shape(
    install_config: ConfigManager, payload: Any, location: list[str]
) -> None:
    before = Path(install_config.config_path).read_bytes()
    status, body, row = asyncio.run(_call_settings("user", {}, "POST", payload))
    assert status == 422
    assert body["detail"][0]["loc"] == location
    assert Path(install_config.config_path).read_bytes() == before
    assert row == {}


def test_settings_post_schema_keeps_the_typed_required_body() -> None:
    app = FastAPI()
    app.include_router(settings_ep.router, prefix="/settings")
    schema = app.openapi()
    for path in ("/settings", "/settings/"):
        request_body = schema["paths"][path]["post"]["requestBody"]
        assert request_body["required"] is True
        body_schema = request_body["content"]["application/json"]["schema"]
        assert body_schema["type"] == "object"
        properties = body_schema["properties"]
        for key in ("speaker_engine", "live_speaker_labels", "theme"):
            assert properties[key]["anyOf"] == [
                {"type": "string"},
                {"type": "null"},
            ]
        assert properties["ai_analysis_daily_limit"]["anyOf"] == [
            {"type": "integer", "minimum": 0},
            {"type": "null"},
        ]
