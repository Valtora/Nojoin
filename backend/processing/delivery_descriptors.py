"""Measured vocal-delivery descriptors for a processed recording.

What this is, and deliberately is not: it measures *how* people spoke -- pace,
pitch movement, loudness, pausing -- and makes no claim about how they felt. No
emotion model is involved and none should be added here. Every number below is
an arithmetic property of the waveform or of the transcript's own timings, which
is what lets the interface present them without hedging and lets a user who
disagrees check them against the audio.

Deliberately numpy and soundfile only. This runs on the CPU lane, so it must not
pull torch in, and it holds no model, which is why it can also run over a whole
back catalogue on request without competing with recording or processing.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Sequence

logger = logging.getLogger(__name__)

# Bumping this invalidates every stored delivery payload, in the same way
# EMBEDDING_METHOD_VERSION invalidates a voiceprint: a figure produced by one
# extraction procedure is not comparable with one produced by another, and a
# mixed set is worse than a missing one. Change it whenever anything below
# alters the numbers.
DELIVERY_METHOD_VERSION = 1

# Analysis frame geometry. 40ms is long enough to hold two periods of the
# lowest pitch searched for, which an autocorrelation estimator needs, and
# short enough that pitch is roughly stationary across it.
FRAME_MS = 40
HOP_MS = 10

# Human speaking pitch. Wider than a typical adult range on purpose: clipping
# the search band biases the estimate towards the band's edge rather than
# reporting an honest miss.
MIN_F0_HZ = 70.0
MAX_F0_HZ = 400.0

# A frame counts as voiced when its normalised autocorrelation peak clears
# this. Below it the frame is noise, a consonant, or silence, and the lag of
# its strongest peak means nothing.
VOICING_CORRELATION_FLOOR = 0.35

# Frames quieter than this contribute no pitch or loudness reading. -50 dBFS
# is well under speech but above the noise floor of a 16-bit capture.
SILENCE_FLOOR_DBFS = -50.0

# Utterances shorter than this give unstable rate and pitch estimates: one
# word's worth of audio produces a words-per-minute figure driven entirely by
# where the segmenter placed the boundaries.
MIN_UTTERANCE_MS = 1_500

# A speaker needs this many analysed utterances before descriptors are
# reported at all. Below it the medians describe a handful of moments rather
# than how someone spoke, and a confident-looking number over three samples is
# worse than none.
MIN_UTTERANCES_PER_SPEAKER = 5

# Channel dominance, matching the live lane's thresholds in live_transcribe so
# one capture is not read two different ways by two parts of the pipeline.
DOMINANT_SHARE_THRESHOLD = 0.65
DOMINANCE_RATIO_THRESHOLD = 1.5


@dataclass(frozen=True)
class DeliveryUtterance:
    """One utterance to measure, in the form this module needs."""

    speaker_key: str
    start_ms: int
    end_ms: int
    word_count: int
    # Overlapping speech contaminates every descriptor here: two voices in one
    # slice produce a pitch reading belonging to neither.
    overlapped: bool


def _frame_signal(samples, frame_length: int, hop_length: int):
    import numpy as np

    if samples.size < frame_length:
        return np.empty((0, frame_length), dtype=np.float32)
    frame_count = 1 + (samples.size - frame_length) // hop_length
    strides = (samples.strides[0] * hop_length, samples.strides[0])
    return np.lib.stride_tricks.as_strided(
        samples, shape=(frame_count, frame_length), strides=strides
    )


def _estimate_frame_f0(frame, sample_rate: int) -> float | None:
    """Autocorrelation pitch estimate for one frame, or None if unvoiced.

    Autocorrelation rather than a learned estimator because this has to run
    with no model on the CPU lane. Its known weakness is octave error, which
    is why the caller takes a median over many frames rather than trusting any
    single one, and why pitch is reported as a distribution rather than a
    value.
    """
    import numpy as np

    windowed = frame - frame.mean()
    energy = float(np.dot(windowed, windowed))
    if energy <= 0:
        return None

    # Autocorrelation via FFT: cheaper than the direct form at this size, and
    # this runs over every frame of every utterance.
    padded = int(2 ** math.ceil(math.log2(len(windowed) * 2)))
    spectrum = np.fft.rfft(windowed * np.hanning(len(windowed)), padded)
    autocorr = np.fft.irfft(spectrum * np.conjugate(spectrum), padded)[: len(windowed)]
    if autocorr[0] <= 0:
        return None

    min_lag = max(1, int(sample_rate / MAX_F0_HZ))
    max_lag = min(len(autocorr) - 1, int(sample_rate / MIN_F0_HZ))
    if max_lag <= min_lag:
        return None

    search = autocorr[min_lag : max_lag + 1]
    peak_index = int(np.argmax(search))
    peak_value = float(search[peak_index]) / float(autocorr[0])
    if peak_value < VOICING_CORRELATION_FLOOR:
        return None

    lag = min_lag + peak_index
    return float(sample_rate) / float(lag)


def _select_channel(block, browser_capture: bool) -> tuple[Any, str | None, bool]:
    """Pick the channel that carried this utterance.

    Returns the mono signal, which capture source it came from, and whether
    that attribution was unambiguous.

    The channel layout only carries meaning for a browser capture, where the
    transcode contract fixes channel 0 as shared audio and channel 1 as the
    microphone. An imported stereo file's channels are left and right, so
    reading them as sources would be inventing provenance; those are downmixed.
    """
    import numpy as np

    if block.ndim == 1:
        return block, None, True
    if block.shape[1] < 2 or not browser_capture:
        return block.mean(axis=1), None, True

    from backend.processing.browser_live_audio import (
        BROWSER_LIVE_SOURCE_NAME_BY_CHANNEL,
    )

    rms = np.sqrt(np.mean(np.square(block.astype(np.float64)), axis=0) + 1e-12)
    total = float(np.sum(rms))
    if total <= 1e-8:
        return block.mean(axis=1), None, False

    shares = rms / total
    primary = int(np.argmax(rms))
    ordered = sorted(shares, reverse=True)
    secondary = ordered[1] if len(ordered) > 1 else 0.0
    unambiguous = ordered[0] >= DOMINANT_SHARE_THRESHOLD and (
        secondary <= 0.0 or ordered[0] / secondary >= DOMINANCE_RATIO_THRESHOLD
    )
    source = BROWSER_LIVE_SOURCE_NAME_BY_CHANNEL.get(primary)
    return block[:, primary], source, bool(unambiguous)


def _measure_slice(signal, sample_rate: int) -> dict[str, Any] | None:
    """Loudness and pitch for one utterance's audio."""
    import numpy as np

    frame_length = int(sample_rate * FRAME_MS / 1000)
    hop_length = int(sample_rate * HOP_MS / 1000)
    frames = _frame_signal(
        np.ascontiguousarray(signal, dtype=np.float32), frame_length, hop_length
    )
    if frames.shape[0] == 0:
        return None

    frame_rms = np.sqrt(np.mean(np.square(frames.astype(np.float64)), axis=1) + 1e-12)
    frame_dbfs = 20.0 * np.log10(np.maximum(frame_rms, 1e-9))
    audible = frame_dbfs > SILENCE_FLOOR_DBFS
    if not np.any(audible):
        return None

    f0_values = [
        value
        for index in np.flatnonzero(audible)
        if (value := _estimate_frame_f0(frames[index], sample_rate)) is not None
    ]

    return {
        "loudness_dbfs": float(np.median(frame_dbfs[audible])),
        "f0_hz": [float(value) for value in f0_values],
        "voiced_frames": len(f0_values),
        "audible_frames": int(np.count_nonzero(audible)),
    }


def _semitone_spread(f0_values: Sequence[float]) -> float | None:
    """Pitch movement in semitones, as the interquartile range.

    Semitones rather than hertz because hertz is not comparable between
    voices: the same expressive range measures roughly twice as wide in hertz
    on a high voice as on a low one, so a raw-hertz spread would report every
    low voice as flat.
    """
    import numpy as np

    if len(f0_values) < 8:
        return None
    values = np.asarray(f0_values, dtype=np.float64)
    reference = float(np.median(values))
    if reference <= 0:
        return None
    semitones = 12.0 * np.log2(values / reference)
    return float(np.percentile(semitones, 75) - np.percentile(semitones, 25))


def _pause_structure(
    utterances: Sequence[DeliveryUtterance],
) -> dict[str, dict[str, Any]]:
    """Within-turn pauses per speaker.

    Only gaps *inside* a turn count. The gap between two speakers' turns is a
    handover, already reported as response latency, and folding it in here
    would describe someone who waits their turn as someone who pauses a lot.
    Turn boundaries come from the analytics tier so both surfaces mean the same
    thing by a turn.
    """
    import numpy as np

    from backend.services.meeting_analytics.constants import TURN_GAP_MS

    ordered = sorted(utterances, key=lambda u: (u.start_ms, u.end_ms))
    gaps: dict[str, list[int]] = {}
    for previous, current in zip(ordered, ordered[1:]):
        if previous.speaker_key != current.speaker_key:
            continue
        gap = current.start_ms - previous.end_ms
        if 0 < gap < TURN_GAP_MS:
            gaps.setdefault(current.speaker_key, []).append(gap)

    return {
        speaker_key: {
            "pause_count": len(samples),
            "median_pause_ms": int(np.median(samples)),
        }
        for speaker_key, samples in gaps.items()
    }


def _aggregate_speaker(readings: list[dict[str, Any]]) -> dict[str, Any] | None:
    import numpy as np

    if len(readings) < MIN_UTTERANCES_PER_SPEAKER:
        return None

    speech_rates = [r["words_per_minute"] for r in readings if r["words_per_minute"]]
    loudness = [r["loudness_dbfs"] for r in readings]
    f0_values = [value for reading in readings for value in reading["f0_hz"]]
    sources = {r["source"] for r in readings if r["source"]}

    return {
        "analysed_utterances": len(readings),
        "words_per_minute": (
            int(round(float(np.median(speech_rates)))) if speech_rates else None
        ),
        "median_f0_hz": (
            int(round(float(np.median(f0_values)))) if len(f0_values) >= 8 else None
        ),
        "pitch_spread_semitones": (
            round(spread, 2)
            if (spread := _semitone_spread(f0_values)) is not None
            else None
        ),
        "median_loudness_dbfs": round(float(np.median(loudness)), 1),
        "loudness_range_db": round(
            float(np.percentile(loudness, 75) - np.percentile(loudness, 25)), 1
        ),
        # Which capture source carried this speaker, where that is knowable.
        # More than one means the speaker was heard on both, which is normal
        # for someone in the room on a call with shared audio.
        "capture_sources": sorted(sources),
    }


def analyse_delivery(
    audio_path: str,
    utterances: Sequence[DeliveryUtterance],
    *,
    browser_capture: bool,
) -> dict[str, Any]:
    """Measure delivery descriptors for every speaker in a recording."""
    import numpy as np
    import soundfile as sf

    readings: dict[str, list[dict[str, Any]]] = {}
    skipped_overlapping = 0
    skipped_short = 0
    ambiguous_channel = 0

    with sf.SoundFile(audio_path) as handle:
        sample_rate = handle.samplerate
        total_frames = len(handle)

        for utterance in utterances:
            if utterance.overlapped:
                skipped_overlapping += 1
                continue
            duration_ms = utterance.end_ms - utterance.start_ms
            if duration_ms < MIN_UTTERANCE_MS:
                skipped_short += 1
                continue

            start_frame = int(utterance.start_ms * sample_rate / 1000)
            stop_frame = min(int(utterance.end_ms * sample_rate / 1000), total_frames)
            if stop_frame <= start_frame:
                continue

            handle.seek(start_frame)
            block = handle.read(
                stop_frame - start_frame, dtype="float32", always_2d=True
            )
            if block.size == 0:
                continue

            signal, source, unambiguous = _select_channel(block, browser_capture)
            if not unambiguous:
                ambiguous_channel += 1
            measured = _measure_slice(np.asarray(signal), sample_rate)
            if measured is None:
                continue

            readings.setdefault(utterance.speaker_key, []).append(
                {
                    "loudness_dbfs": measured["loudness_dbfs"],
                    "f0_hz": measured["f0_hz"],
                    "source": source,
                    "words_per_minute": (
                        round(utterance.word_count / (duration_ms / 60_000), 1)
                        if utterance.word_count and duration_ms > 0
                        else None
                    ),
                }
            )

    pauses = _pause_structure(utterances)
    speakers: dict[str, Any] = {}
    for speaker_key, speaker_readings in readings.items():
        aggregate = _aggregate_speaker(speaker_readings)
        if aggregate is None:
            continue
        aggregate.update(pauses.get(speaker_key, {"pause_count": 0}))
        speakers[speaker_key] = aggregate

    # Loudness is only comparable between speakers who arrived through the same
    # signal chain. A remote voice has been through a codec and the far end's
    # automatic gain control; a local microphone has not, and the difference
    # dwarfs how loudly either person actually spoke.
    observed_sources = {
        source
        for aggregate in speakers.values()
        for source in aggregate.get("capture_sources", [])
    }

    return {
        "method_version": DELIVERY_METHOD_VERSION,
        "speakers": speakers,
        "cross_speaker_loudness_comparable": len(observed_sources) <= 1,
        "channel_layout": "browser_live" if browser_capture else "single_source",
        "skipped_overlapping": skipped_overlapping,
        "skipped_short": skipped_short,
        "ambiguous_channel": ambiguous_channel,
    }
