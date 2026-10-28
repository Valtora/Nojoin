"""Settling settings persist installation-wide and freeze per capture episode."""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from backend.models.transcript import Transcript
from backend.tests.test_install_wide_transcription_settings import (
    _call_settings,
    _config_file,
)
from backend.tests.test_install_wide_transcription_settings import (
    install_config as install_config,
)
from backend.utils import transcript_settling
from backend.utils.config_manager import ConfigManager
from backend.utils.transcript_settling import (
    new_capture_settling_state,
    resolve_capture_settling_policy,
    update_capture_settling_state,
)

SETTINGS = {
    "live_text_settling": "auto",
    "live_text_settling_k": 5,
    "live_text_settling_window_s": 12.5,
}


def test_admin_policy_is_install_wide_and_non_admin_stale_values_are_ignored(
    install_config: ConfigManager,
) -> None:
    status, body, row = asyncio.run(_call_settings("admin", {}, "POST", SETTINGS))
    assert status == 200
    assert {key: _config_file(install_config)[key] for key in SETTINGS} == SETTINGS
    assert {key: body[key] for key in SETTINGS} == SETTINGS
    assert not set(SETTINGS).intersection(row)
    status, body, row = asyncio.run(
        _call_settings("user", SETTINGS, "POST", {key: "invalid" for key in SETTINGS})
    )
    assert status == 200
    assert {key: body[key] for key in SETTINGS} == SETTINGS
    assert not set(SETTINGS).intersection(row)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("live_text_settling", "on"),
        ("live_text_settling_k", 0),
        ("live_text_settling_k", 11),
        ("live_text_settling_k", True),
        ("live_text_settling_k", 2.5),
        ("live_text_settling_window_s", 1.9),
        ("live_text_settling_window_s", 31),
        ("live_text_settling_window_s", True),
    ],
)
def test_invalid_policy_cannot_change_saved_settings(
    install_config: ConfigManager, key: str, value: Any
) -> None:
    before = _config_file(install_config)
    status, _, row = asyncio.run(_call_settings("admin", {}, "POST", {key: value}))
    assert status == 422
    assert _config_file(install_config) == before
    assert row == {}
    with pytest.raises(ValueError):
        install_config.validate_config_value(key, value)


@pytest.mark.parametrize("existing", [False, True], ids=["fresh", "existing-missing"])
@pytest.mark.parametrize("path", ["/settings", "/settings/"])
def test_default_on_for_fresh_and_existing_users_without_a_stored_value(
    install_config: ConfigManager,
    monkeypatch: pytest.MonkeyPatch,
    existing: bool,
    path: str,
) -> None:
    if not existing:
        Path(install_config.config_path).unlink()
    install_config.reload(force=True)
    monkeypatch.setattr(transcript_settling, "config_manager", install_config)
    user_settings = {"theme": "light"} if existing else {}
    status, body, row = asyncio.run(
        _call_settings("user", user_settings, "GET", options={"path": path})
    )
    assert status == 200
    assert body["live_text_settling"] == "auto"
    assert body["live_text_settling_k"] == 3
    assert body["live_text_settling_window_s"] == 10
    assert row == user_settings
    if existing:
        assert "live_text_settling" not in _config_file(install_config)
    else:
        assert _config_file(install_config)["live_text_settling"] == "auto"

    transcript = Transcript(
        recording_id=1, settling_state=new_capture_settling_state(capture_epoch=0)
    )
    resolve_capture_settling_policy(transcript, device="cuda")
    assert transcript.settling_state["policy"] == {
        "k": 3,
        "window_s": 10,
        "enabled": True,
    }


def test_null_and_unrelated_updates_preserve_explicit_off_after_reload(
    install_config: ConfigManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    explicit = {**SETTINGS, "live_text_settling": "off"}
    status, _, _ = asyncio.run(_call_settings("admin", {}, "POST", explicit))
    assert status == 200
    status, body, _ = asyncio.run(
        _call_settings(
            "admin",
            {},
            "POST",
            {**dict.fromkeys(SETTINGS), "canary_model": "canary-new"},
        )
    )
    assert status == 200
    assert {key: body[key] for key in SETTINGS} == explicit
    install_config.reload(force=True)
    status, body, _ = asyncio.run(_call_settings("user", {}, "GET"))
    assert status == 200
    assert {key: body[key] for key in SETTINGS} == explicit
    assert _config_file(install_config)["live_text_settling"] == "off"
    monkeypatch.setattr(transcript_settling, "config_manager", install_config)
    assert new_capture_settling_state(capture_epoch=0) is None


def test_capture_counts_keep_policy_until_next_episode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.utils.config_manager import config_manager

    settings = dict(SETTINGS)
    monkeypatch.setattr(
        config_manager, "get", lambda key, default=None: settings.get(key, default)
    )
    transcript = Transcript(
        recording_id=1, settling_state=new_capture_settling_state(capture_epoch=0)
    )
    resolve_capture_settling_policy(transcript, device="cuda")
    transcript.settling_state = {
        **transcript.settling_state,
        "speaker_enabled": True,
        "gate_chunks": [
            {
                "sequence": 0,
                "start_ms": 0,
                "end_ms": 2000,
                "missed": True,
                "consumed": False,
            }
        ],
    }
    initial = dict(transcript.settling_state)
    settings.update(live_text_settling_k=2, live_text_settling_window_s=6)
    resolve_capture_settling_policy(transcript, device="cuda")
    assert transcript.settling_state == initial
    update_capture_settling_state(
        transcript, capture_epoch=0, sealed=False, phase="paused"
    )
    update_capture_settling_state(
        transcript, capture_epoch=1, sealed=False, phase="live"
    )
    assert transcript.settling_state["generation"] != initial["generation"]
    assert transcript.settling_state["policy"] == {
        "k": 2,
        "window_s": 6,
        "enabled": None,
    }
    assert "speaker_enabled" not in transcript.settling_state
    assert "gate_chunks" not in transcript.settling_state


@pytest.mark.parametrize("resumed", [False, True])
def test_canonical_write_rollback_disables_new_and_resumed_settling_episodes(
    monkeypatch: pytest.MonkeyPatch, resumed: bool
) -> None:
    from backend.utils.config_manager import config_manager

    settings = {**SETTINGS, "enable_canonical_transcript_writes": True}
    monkeypatch.setattr(
        config_manager, "get", lambda key, default=None: settings.get(key, default)
    )
    transcript = Transcript(
        recording_id=1, settling_state=new_capture_settling_state(capture_epoch=0)
    )
    settings["enable_canonical_transcript_writes"] = False
    if resumed:
        update_capture_settling_state(
            transcript, capture_epoch=0, sealed=False, phase="paused"
        )
        update_capture_settling_state(
            transcript, capture_epoch=1, sealed=False, phase="live"
        )
        resolve_capture_settling_policy(transcript, device="cuda")
        assert transcript.settling_state["policy"]["enabled"] is False
    else:
        assert new_capture_settling_state(capture_epoch=0) is None


def test_actual_cpu_fallback_disables_repeated_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.utils.config_manager import config_manager

    monkeypatch.setattr(
        config_manager, "get", lambda key, default=None: SETTINGS.get(key, default)
    )
    transcript = Transcript(
        recording_id=1, settling_state=new_capture_settling_state(capture_epoch=0)
    )
    resolve_capture_settling_policy(transcript, device="cuda")
    resolve_capture_settling_policy(transcript, device="cpu", disable_on_cpu=True)
    resolve_capture_settling_policy(transcript, device="cuda")
    assert transcript.settling_state["policy"]["enabled"] is False


@pytest.mark.parametrize(("mode", "enabled"), [("auto", False), ("always", True)])
def test_a_cpu_worker_settles_only_when_the_mode_allows_it(
    monkeypatch: pytest.MonkeyPatch, mode: str, enabled: bool
) -> None:
    from backend.utils.config_manager import config_manager

    settings = {**SETTINGS, "live_text_settling": mode}
    monkeypatch.setattr(
        config_manager, "get", lambda key, default=None: settings.get(key, default)
    )
    transcript = Transcript(
        recording_id=1, settling_state=new_capture_settling_state(capture_epoch=0)
    )
    resolve_capture_settling_policy(transcript, device="cpu")
    assert transcript.settling_state["policy"]["enabled"] is enabled
