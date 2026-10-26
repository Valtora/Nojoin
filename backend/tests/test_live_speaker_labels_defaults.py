"""Hardware-gated live-label defaults on the real config loading path."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import torch

from backend.api.v1.endpoints import settings as settings_ep
from backend.processing.live_diarization import setting, step
from backend.tests.test_install_wide_transcription_settings import _call_settings
from backend.utils.config_manager import ConfigManager


@pytest.fixture(autouse=True)
def isolate_config_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise file loading without touching the install's recordings folder."""

    def skip_directories(self: ConfigManager, config: dict[str, Any]) -> None:
        pass

    monkeypatch.setattr(ConfigManager, "_ensure_dirs_exist", skip_directories)
    monkeypatch.delenv("LIVE_SPEAKER_LABELS", raising=False)


@pytest.mark.parametrize(
    ("stored", "expected"),
    [({}, "auto"), ({"live_speaker_labels": "off"}, "off")],
    ids=["missing-key", "explicit-off"],
)
def test_existing_config_keeps_label_policy_across_reload_and_save(
    tmp_path: Path, stored: dict[str, str], expected: str
) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(stored), encoding="utf-8")

    manager = ConfigManager(config_path=str(path))
    assert manager.get("live_speaker_labels") == expected
    assert json.loads(path.read_text(encoding="utf-8")) == stored

    manager.reload(force=True)
    assert manager.get("live_speaker_labels") == expected
    manager.save_values({"whisper_model_size": "small"})
    assert manager.get("live_speaker_labels") == expected
    assert json.loads(path.read_text(encoding="utf-8")) == {
        **stored,
        "whisper_model_size": "small",
    }

    # Whole-config writes also preserve the effective policy, including off.
    manager.set("enable_live_transcription", True)
    restarted = ConfigManager(config_path=str(path))
    assert restarted.get("live_speaker_labels") == expected
    assert (
        json.loads(path.read_text(encoding="utf-8"))["live_speaker_labels"] == expected
    )


def test_fresh_config_saves_auto_as_the_shipped_default(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    manager = ConfigManager(config_path=str(path))

    assert manager.get("live_speaker_labels") == "auto"
    assert json.loads(path.read_text(encoding="utf-8"))["live_speaker_labels"] == "auto"
    assert ConfigManager(config_path=str(path)).get("live_speaker_labels") == "auto"


@pytest.mark.parametrize("stored", [None, "", "on", ["auto"], {"bad": True}])
def test_invalid_or_null_stored_labels_remain_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stored: object
) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"live_speaker_labels": stored}), encoding="utf-8")
    manager = ConfigManager(config_path=str(path))
    monkeypatch.setattr(setting, "config_manager", manager)
    monkeypatch.setattr(settings_ep, "config_manager", manager)

    assert not setting.live_speaker_labels_requested()
    response: dict[str, Any] = {}
    settings_ep._normalise_speaker_settings(response)
    assert response["live_speaker_labels"] == "off"


@pytest.mark.parametrize("speaker_engine", ["auto", "classic"])
@pytest.mark.parametrize(("device", "cuda_available"), [("auto", False), ("cpu", True)])
def test_default_auto_skips_all_label_work_on_cpu_for_both_engines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    speaker_engine: str,
    device: str,
    cuda_available: bool,
) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"speaker_engine": speaker_engine, "processing_device": device}),
        encoding="utf-8",
    )
    manager = ConfigManager(config_path=str(path))
    monkeypatch.setattr(setting, "config_manager", manager)
    monkeypatch.setattr(step, "config_manager", manager)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: cuda_available)

    def refuse_label_work(recording_id: int) -> bool:
        pytest.fail("CPU live labels reached the recording/model/state work")

    monkeypatch.setattr(step, "_still_capturing", refuse_label_work)
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    assert setting.live_speaker_labels_requested()

    step.run_live_speaker_labels(step.LiveLabelRun(42, 0, live_dir, (), 0.0, 0.0))

    assert list(live_dir.iterdir()) == []


def test_unrelated_admin_save_keeps_explicit_label_opt_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"live_speaker_labels": "off"}', encoding="utf-8")
    manager = ConfigManager(config_path=str(path))
    monkeypatch.setattr(settings_ep, "config_manager", manager)

    status, body, row = asyncio.run(
        _call_settings("admin", {}, "POST", {"prefer_short_titles": False})
    )

    assert status == 200
    assert row["prefer_short_titles"] is False
    assert body["live_speaker_labels"] == "off"
    assert ConfigManager(config_path=str(path)).get("live_speaker_labels") == "off"
