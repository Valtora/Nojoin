"""Which final-pass stages read the VAD-muted audio (#486)."""

from __future__ import annotations

import logging

import numpy as np
import pytest
import soundfile

from backend.processing import vad_muting
from backend.processing.vad_muting import (
    MutedStages,
    diarised_turns,
    muted_stages,
    write_union_muted_audio,
)
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


# --- the union file transcription hears (#670) -------------------------------

_RATE = 16000
_SECONDS = 20


def _write_pair(tmp_path, *, frames=_SECONDS * _RATE):
    """An unmuted file with no zero sample, and its muted copy keeping 4-6 s."""
    unmuted = (np.arange(frames) % 1000 + 1).astype(np.int16)
    muted = np.zeros_like(unmuted)
    muted[4 * _RATE : 6 * _RATE] = unmuted[4 * _RATE : 6 * _RATE]
    paths = (str(tmp_path / "rec_vad.wav"), str(tmp_path / "rec_vad_processed.wav"))
    for path, samples in zip(paths, (unmuted, muted)):
        soundfile.write(path, samples, _RATE, subtype="PCM_16")
    return paths, unmuted, muted


def _union(tmp_path, turns, **kwargs):
    (unmuted_path, muted_path), unmuted, muted = _write_pair(tmp_path)
    out = str(tmp_path / "rec_vad_union.wav")
    restored = write_union_muted_audio(unmuted_path, muted_path, out, turns, **kwargs)
    written, rate = soundfile.read(out, dtype="int16")
    assert rate == _RATE
    assert soundfile.info(out).subtype == "PCM_16"
    return written, unmuted, restored


def _at(seconds: float) -> int:
    return round(seconds * _RATE)


@pytest.mark.parametrize(
    ("where", "start", "end", "heard"),
    [
        ("VAD speech, also in the turn", 5.0, 6.0, True),
        ("VAD speech, outside every turn", 4.0, 5.0, True),
        ("a turn VAD muted", 6.0, 9.0, True),
        ("neither", 9.0, 19.0, False),
        ("neither, before VAD speech", 1.0, 4.0, False),
    ],
)
def test_the_union_mutes_only_where_neither_vad_nor_a_turn_heard_speech(
    tmp_path, where, start, end, heard
):
    written, unmuted, _ = _union(tmp_path, [(5.0, 9.0)])

    span = slice(_at(start), _at(end))
    if heard:
        assert np.array_equal(written[span], unmuted[span]), where
    else:
        assert not written[span].any(), where


def test_the_union_puts_back_only_what_the_turns_add(tmp_path):
    _, _, restored = _union(tmp_path, [(5.0, 9.0), (12.0, 12.5)])

    assert restored == pytest.approx(3.5)


def test_a_turn_opens_exactly_its_own_samples(tmp_path):
    written, unmuted, _ = _union(tmp_path, [(10.0, 11.0)])

    assert written[_at(10.0) - 1] == 0
    assert written[_at(10.0)] == unmuted[_at(10.0)]
    assert written[_at(11.0) - 1] == unmuted[_at(11.0) - 1]
    assert written[_at(11.0)] == 0


def test_the_first_and_last_second_stay_muted_inside_a_turn(tmp_path):
    written, unmuted, restored = _union(tmp_path, [(0.0, 3.0), (18.0, 20.0)])

    assert not written[: _at(1.0)].any()
    assert np.array_equal(written[_at(1.0) : _at(3.0)], unmuted[_at(1.0) : _at(3.0)])
    assert np.array_equal(
        written[_at(18.0) : _at(19.0)], unmuted[_at(18.0) : _at(19.0)]
    )
    assert not written[_at(19.0) :].any()
    assert restored == pytest.approx(3.0)


def test_with_no_turns_the_union_is_the_muted_file(tmp_path):
    (unmuted_path, muted_path), _, muted = _write_pair(tmp_path)
    out = str(tmp_path / "rec_vad_union.wav")

    restored = write_union_muted_audio(unmuted_path, muted_path, out, [])

    assert restored == 0
    assert np.array_equal(soundfile.read(out, dtype="int16")[0], muted)


def test_a_turn_across_read_blocks_is_opened_whole(tmp_path, monkeypatch):
    """A long recording is read a block at a time; a turn spanning blocks, and
    one inside a later block, open the same samples as in one read."""
    monkeypatch.setattr(vad_muting, "_UNION_BLOCK_FRAMES", 7001)
    written, unmuted, _ = _union(tmp_path, [(6.5, 9.25), (15.1, 15.2)])

    expected = np.zeros_like(unmuted)
    for start, end in ((4.0, 6.0), (6.5, 9.25), (15.1, 15.2)):
        expected[_at(start) : _at(end)] = unmuted[_at(start) : _at(end)]
    assert np.array_equal(written, expected)


def test_files_of_different_lengths_are_refused(tmp_path):
    (unmuted_path, _), _, _ = _write_pair(tmp_path)
    (tmp_path / "short").mkdir()
    (_, shorter), _, _ = _write_pair(tmp_path / "short", frames=_RATE * 19)

    with pytest.raises(ValueError):
        write_union_muted_audio(
            unmuted_path, shorter, str(tmp_path / "out.wav"), [(5.0, 9.0)]
        )


def test_turns_are_read_from_a_pyannote_annotation():
    from pyannote.core import Annotation, Segment

    annotation = Annotation()
    annotation[Segment(1.5, 2.25)] = "SPEAKER_00"
    annotation[Segment(2.0, 4.0), "b"] = "SPEAKER_01"

    assert sorted(diarised_turns(annotation)) == [(1.5, 2.25), (2.0, 4.0)]
    assert diarised_turns(Annotation()) == []
