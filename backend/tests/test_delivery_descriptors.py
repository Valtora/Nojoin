"""Tests for the measured vocal-delivery descriptors.

Synthetic audio with known ground truth throughout: a pitch estimator that is
only ever run against real speech cannot be shown to be right, because nobody
knows what the answer was. Every fixture here has an F0 and an amplitude the
test chose, so the assertions are about accuracy rather than about not
crashing.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from backend.processing.delivery_descriptors import (
    DELIVERY_METHOD_VERSION,
    MIN_UTTERANCES_PER_SPEAKER,
    DeliveryUtterance,
    _estimate_frame_f0,
    _select_channel,
    _semitone_spread,
    analyse_delivery,
)

SAMPLE_RATE = 16_000


def voiced_tone(f0_hz: float, duration_s: float, amplitude: float = 0.3) -> np.ndarray:
    """A glottal-ish pulse train: harmonically rich, so autocorrelation has
    something to lock onto, as it does with speech and does not with a sine."""
    t = np.arange(int(SAMPLE_RATE * duration_s)) / SAMPLE_RATE
    signal = np.zeros_like(t)
    for harmonic in range(1, 12):
        signal += np.sin(2 * math.pi * f0_hz * harmonic * t) / harmonic
    peak = np.max(np.abs(signal)) or 1.0
    return (signal / peak * amplitude).astype(np.float32)


class TestPitchEstimation:
    @pytest.mark.parametrize("f0", [85.0, 120.0, 180.0, 240.0])
    def test_recovers_a_known_pitch_within_a_few_percent(self, f0: float):
        frame = voiced_tone(f0, 0.04)

        estimate = _estimate_frame_f0(frame, SAMPLE_RATE)

        assert estimate is not None
        assert abs(estimate - f0) / f0 < 0.05

    def test_reports_no_pitch_for_noise(self):
        rng = np.random.default_rng(1234)
        frame = rng.normal(0, 0.1, int(SAMPLE_RATE * 0.04)).astype(np.float32)

        assert _estimate_frame_f0(frame, SAMPLE_RATE) is None

    def test_reports_no_pitch_for_silence(self):
        frame = np.zeros(int(SAMPLE_RATE * 0.04), dtype=np.float32)

        assert _estimate_frame_f0(frame, SAMPLE_RATE) is None


class TestSemitoneSpread:
    def test_a_monotone_voice_has_almost_no_spread(self):
        assert _semitone_spread([120.0] * 40) == pytest.approx(0.0, abs=0.01)

    def test_spread_is_scale_invariant_across_voices(self):
        # The same expressive range on a low and a high voice must measure the
        # same. In hertz it would not, which is the whole reason for semitones.
        low = [100.0 * (2 ** (n / 12)) for n in range(-6, 7)]
        high = [220.0 * (2 ** (n / 12)) for n in range(-6, 7)]

        assert _semitone_spread(low) == pytest.approx(_semitone_spread(high), abs=0.01)

    def test_too_few_samples_yields_no_reading(self):
        assert _semitone_spread([120.0, 130.0]) is None


class TestChannelSelection:
    def test_browser_capture_picks_the_dominant_source(self):
        block = np.zeros((SAMPLE_RATE, 2), dtype=np.float32)
        block[:, 1] = voiced_tone(140.0, 1.0, amplitude=0.4)  # microphone

        signal, source, unambiguous = _select_channel(block, browser_capture=True)

        assert source == "microphone"
        assert unambiguous is True
        assert signal.shape == (SAMPLE_RATE,)

    def test_both_channels_active_is_flagged_ambiguous(self):
        block = np.zeros((SAMPLE_RATE, 2), dtype=np.float32)
        block[:, 0] = voiced_tone(140.0, 1.0, amplitude=0.3)
        block[:, 1] = voiced_tone(200.0, 1.0, amplitude=0.3)

        _, _, unambiguous = _select_channel(block, browser_capture=True)

        assert unambiguous is False

    def test_an_imported_stereo_file_is_downmixed_not_attributed(self):
        # Channel 0 is only "shared audio" under the browser transcode
        # contract. An imported file's channels are left and right, and
        # reporting one as a capture source would be inventing provenance.
        block = np.zeros((SAMPLE_RATE, 2), dtype=np.float32)
        block[:, 0] = voiced_tone(140.0, 1.0, amplitude=0.4)

        signal, source, _ = _select_channel(block, browser_capture=False)

        assert source is None
        assert signal.shape == (SAMPLE_RATE,)


def write_wav(path, channels: list[np.ndarray]) -> None:
    import soundfile as sf

    data = np.stack(channels, axis=1) if len(channels) > 1 else channels[0]
    sf.write(str(path), data, SAMPLE_RATE)


def utterances_for(speaker: str, count: int, start_ms: int = 0) -> list:
    return [
        DeliveryUtterance(
            speaker_key=speaker,
            start_ms=start_ms + index * 3_000,
            end_ms=start_ms + index * 3_000 + 2_000,
            word_count=6,
            overlapped=False,
        )
        for index in range(count)
    ]


class TestAnalyseDelivery:
    def test_measures_a_speaker_and_stamps_the_method_version(self, tmp_path):
        audio = tmp_path / "meeting.wav"
        write_wav(audio, [voiced_tone(150.0, 40.0)])

        result = analyse_delivery(
            str(audio),
            utterances_for("rs:1", MIN_UTTERANCES_PER_SPEAKER),
            browser_capture=False,
        )

        assert result["method_version"] == DELIVERY_METHOD_VERSION
        speaker = result["speakers"]["rs:1"]
        assert abs(speaker["median_f0_hz"] - 150) / 150 < 0.05
        # Six words in two seconds is 180 wpm.
        assert speaker["words_per_minute"] == pytest.approx(180, abs=1)

    def test_a_speaker_with_too_few_utterances_gets_no_descriptors(self, tmp_path):
        audio = tmp_path / "meeting.wav"
        write_wav(audio, [voiced_tone(150.0, 40.0)])

        result = analyse_delivery(
            str(audio),
            utterances_for("rs:1", MIN_UTTERANCES_PER_SPEAKER - 1),
            browser_capture=False,
        )

        assert "rs:1" not in result["speakers"]

    def test_overlapping_utterances_are_excluded_and_counted(self, tmp_path):
        audio = tmp_path / "meeting.wav"
        write_wav(audio, [voiced_tone(150.0, 40.0)])
        utterances = utterances_for("rs:1", MIN_UTTERANCES_PER_SPEAKER)
        utterances.append(
            DeliveryUtterance(
                speaker_key="rs:1",
                start_ms=20_000,
                end_ms=23_000,
                word_count=5,
                overlapped=True,
            )
        )

        result = analyse_delivery(str(audio), utterances, browser_capture=False)

        assert result["skipped_overlapping"] == 1
        assert result["speakers"]["rs:1"]["analysed_utterances"] == (
            MIN_UTTERANCES_PER_SPEAKER
        )

    def test_short_utterances_are_excluded_and_counted(self, tmp_path):
        audio = tmp_path / "meeting.wav"
        write_wav(audio, [voiced_tone(150.0, 40.0)])
        utterances = utterances_for("rs:1", MIN_UTTERANCES_PER_SPEAKER)
        utterances.append(
            DeliveryUtterance(
                speaker_key="rs:1",
                start_ms=25_000,
                end_ms=25_400,
                word_count=1,
                overlapped=False,
            )
        )

        result = analyse_delivery(str(audio), utterances, browser_capture=False)

        assert result["skipped_short"] == 1

    def test_loudness_is_flagged_incomparable_across_capture_sources(self, tmp_path):
        # One speaker on the microphone and one on shared audio have been
        # through different signal chains, so comparing their loudness compares
        # codecs and gain control rather than how loudly they spoke.
        audio = tmp_path / "meeting.wav"
        duration_s = 60.0
        system = np.zeros(int(SAMPLE_RATE * duration_s), dtype=np.float32)
        microphone = np.zeros_like(system)

        local = utterances_for("rs:local", MIN_UTTERANCES_PER_SPEAKER)
        remote = utterances_for(
            "rs:remote", MIN_UTTERANCES_PER_SPEAKER, start_ms=30_000
        )
        for utterance in local:
            start = int(utterance.start_ms * SAMPLE_RATE / 1000)
            tone = voiced_tone(190.0, 2.0, amplitude=0.4)
            microphone[start : start + tone.size] = tone
        for utterance in remote:
            start = int(utterance.start_ms * SAMPLE_RATE / 1000)
            tone = voiced_tone(110.0, 2.0, amplitude=0.4)
            system[start : start + tone.size] = tone

        write_wav(audio, [system, microphone])

        result = analyse_delivery(str(audio), local + remote, browser_capture=True)

        assert result["channel_layout"] == "browser_live"
        assert result["speakers"]["rs:local"]["capture_sources"] == ["microphone"]
        assert result["speakers"]["rs:remote"]["capture_sources"] == ["system"]
        assert result["cross_speaker_loudness_comparable"] is False

    def test_pauses_inside_a_turn_are_counted_but_handovers_are_not(self, tmp_path):
        audio = tmp_path / "meeting.wav"
        write_wav(audio, [voiced_tone(150.0, 60.0)])

        # Five of one speaker's utterances back to back with 1s gaps, then
        # another speaker. The gap between the two speakers is a handover, and
        # response latency already reports it.
        speaker_a = [
            DeliveryUtterance("rs:1", index * 3_000, index * 3_000 + 2_000, 6, False)
            for index in range(MIN_UTTERANCES_PER_SPEAKER)
        ]
        speaker_b = [
            DeliveryUtterance(
                "rs:2", 30_000 + index * 3_000, 30_000 + index * 3_000 + 2_000, 6, False
            )
            for index in range(MIN_UTTERANCES_PER_SPEAKER)
        ]

        result = analyse_delivery(
            str(audio), speaker_a + speaker_b, browser_capture=False
        )

        # Four 1s gaps within speaker A's run, none attributed across the change.
        assert result["speakers"]["rs:1"]["pause_count"] == (
            MIN_UTTERANCES_PER_SPEAKER - 1
        )
        assert result["speakers"]["rs:1"]["median_pause_ms"] == 1_000

    def test_a_silent_recording_produces_no_speakers_rather_than_zeros(self, tmp_path):
        audio = tmp_path / "silence.wav"
        write_wav(audio, [np.zeros(int(SAMPLE_RATE * 40), dtype=np.float32)])

        result = analyse_delivery(
            str(audio),
            utterances_for("rs:1", MIN_UTTERANCES_PER_SPEAKER),
            browser_capture=False,
        )

        assert result["speakers"] == {}
