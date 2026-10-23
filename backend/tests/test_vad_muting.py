"""Which final-pass stages read the VAD-muted audio (#486)."""

from __future__ import annotations

import logging

import pytest

from backend.processing.vad_muting import MutedStages, muted_stages


def test_both_stages_are_muted_by_default():
    assert muted_stages({}) == MutedStages(transcription=True, diarization=True)
    assert muted_stages(None) == MutedStages(transcription=True, diarization=True)
    unset = {"vad_mute_transcription": None, "vad_mute_diarization": None}
    assert muted_stages(unset) == MutedStages(transcription=True, diarization=True)


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


@pytest.mark.parametrize("value", ["false", 0])
def test_a_value_that_is_not_a_bool_keeps_the_stage_muted(value, caplog):
    """A processing run never fails over a setting: it falls back to muted."""
    config = {"vad_mute_transcription": value, "vad_mute_diarization": value}

    with caplog.at_level(logging.WARNING, logger="backend.processing.vad_muting"):
        stages = muted_stages(config)

    assert stages == MutedStages(transcription=True, diarization=True)
    assert "vad_mute_transcription" in caplog.text
