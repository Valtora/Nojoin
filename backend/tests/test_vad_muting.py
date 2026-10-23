"""Which final-pass stages read the VAD-muted audio (#486)."""

from __future__ import annotations

import logging

import pytest

from backend.processing.vad_muting import MutedStages, muted_stages
from backend.utils.config_manager import get_default_user_settings


def test_only_transcription_is_muted_by_default():
    """An install upgraded from before the switches has neither key stored, so
    its speaker side hears the unmuted file."""
    default = MutedStages(transcription=True, diarization=False)
    assert muted_stages({}) == default
    assert muted_stages(None) == default
    unset = {"vad_mute_transcription": None, "vad_mute_diarization": None}
    assert muted_stages(unset) == default


def test_the_user_defaults_match_the_stages_defaults():
    defaults = get_default_user_settings()

    assert defaults["vad_mute_transcription"] is True
    assert defaults["vad_mute_diarization"] is False


@pytest.mark.parametrize(
    ("transcription", "diarization"),
    [(True, False), (False, True), (False, False)],
)
def test_each_switch_sets_only_its_own_stage(transcription, diarization):
    config = {
        "enable_vad": True,
        "vad_mute_transcription": transcription,
        "vad_mute_diarization": diarization,
    }

    assert muted_stages(config) == MutedStages(transcription, diarization)


def test_vad_off_mutes_nothing_whatever_the_switches_hold():
    config = {
        "enable_vad": False,
        "vad_mute_transcription": True,
        "vad_mute_diarization": True,
    }

    assert muted_stages(config) == MutedStages(transcription=False, diarization=False)


@pytest.mark.parametrize("value", ["false", "true", 0, 1])
def test_a_value_that_is_not_a_bool_falls_back_to_the_default(value, caplog):
    """A processing run never fails over a setting: each stage takes its
    shipped default."""
    config = {"vad_mute_transcription": value, "vad_mute_diarization": value}

    with caplog.at_level(logging.WARNING, logger="backend.processing.vad_muting"):
        stages = muted_stages(config)

    assert stages == MutedStages(transcription=True, diarization=False)
    assert "vad_mute_transcription" in caplog.text
    assert "vad_mute_diarization" in caplog.text
