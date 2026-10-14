"""Plain-language readings of the measured delivery figures.

A port of ``frontend/src/components/analytics/deliveryInsights.ts``, so the
words beside a figure are the same in an export as on the Analytics tab. The
evidence behind every band and threshold is in docs/ANALYTICS_EVIDENCE.md.
Change both together.

Two rules carry over unchanged. The readings are descriptive, never
evaluative: "fast" is a property of speech, "rushed" would be a judgement of a
person. And pitch *height* is never described, because that would characterise
someone's voice rather than how they used it.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Mapping

# Pace bands, pinned to corpus figures (see the module docstring).
PACE_DELIBERATE_MAX = 120
PACE_MEASURED_MAX = 160
PACE_CONVERSATIONAL_MAX = 200
PACE_BRISK_MAX = 240

PITCH_DEVIATION = 0.2
PAUSE_DEVIATION = 0.3

BASELINE_PACE_DEVIATION = 0.15
BASELINE_PITCH_DEVIATION = 0.2
BASELINE_PAUSE_DEVIATION = 0.3

MIN_UTTERANCES_FOR_COMPARISON = 5


@dataclass(frozen=True)
class DeliveryReading:
    pace: str | None
    pitch_movement: str | None
    pauses: str | None
    pause_rate: float | None
    baseline: str | None


def pace_band(wpm: float | None) -> str | None:
    if wpm is None or wpm <= 0:
        return None
    for limit, word in (
        (PACE_DELIBERATE_MAX, "deliberate"),
        (PACE_MEASURED_MAX, "measured"),
        (PACE_CONVERSATIONAL_MAX, "conversational"),
        (PACE_BRISK_MAX, "brisk"),
    ):
        if wpm < limit:
            return word
    return "fast"


def _compared_with(
    value: float | None,
    reference: float | None,
    deviation: float,
    words: tuple[str, str],
) -> str | None:
    if value is None or reference is None or reference <= 0:
        return None
    ratio = value / reference
    if ratio >= 1 + deviation:
        return words[0]
    if ratio <= 1 - deviation:
        return words[1]
    return None


def _pause_rate(figures: Mapping[str, Any], speech_ms: float | None) -> float | None:
    if not speech_ms or speech_ms <= 0:
        return None
    return float(figures.get("pause_count") or 0) / (speech_ms / 60_000)


def _baseline_reading(
    figures: Mapping[str, Any],
    pause_rate: float | None,
    baseline: Mapping[str, Any] | None,
) -> str | None:
    """At most one remark against the person's own history, in a fixed order."""
    if not baseline:
        return None
    across = f"their usual across {baseline.get('meetings')} meetings"
    pace = _compared_with(
        figures.get("words_per_minute"),
        baseline.get("words_per_minute"),
        BASELINE_PACE_DEVIATION,
        (f"faster than {across}", f"slower than {across}"),
    )
    if pace:
        return pace
    pitch = _compared_with(
        figures.get("pitch_spread_semitones"),
        baseline.get("pitch_spread_semitones"),
        BASELINE_PITCH_DEVIATION,
        (f"more varied than {across}", f"flatter than {across}"),
    )
    if pitch:
        return pitch
    return _compared_with(
        pause_rate,
        baseline.get("pauses_per_minute"),
        BASELINE_PAUSE_DEVIATION,
        (f"pauses more than {across}", f"pauses less than {across}"),
    )


def _positive_median(values: list[float | None]) -> float | None:
    usable = [value for value in values if value is not None and value > 0]
    return float(median(usable)) if usable else None


def build_delivery_readings(
    delivery: Mapping[str, Any],
    talk_time_ms: Mapping[str, float],
    baselines: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, DeliveryReading]:
    """One reading per measured speaker, keyed by speaker key."""
    speakers: Mapping[str, Mapping[str, Any]] = delivery.get("speakers") or {}
    baselines = baselines or {}
    comparable = {
        key: figures
        for key, figures in speakers.items()
        if (figures.get("analysed_utterances") or 0) >= MIN_UTTERANCES_FOR_COMPARISON
    }
    median_pitch = _positive_median(
        [figures.get("pitch_spread_semitones") for figures in comparable.values()]
    )
    median_pause_rate = _positive_median(
        [
            _pause_rate(figures, talk_time_ms.get(key))
            for key, figures in comparable.items()
        ]
    )

    readings: dict[str, DeliveryReading] = {}
    for key, figures in speakers.items():
        rate = _pause_rate(figures, talk_time_ms.get(key))
        enough = key in comparable
        readings[key] = DeliveryReading(
            pace=pace_band(figures.get("words_per_minute")),
            pitch_movement=_compared_with(
                figures.get("pitch_spread_semitones"),
                median_pitch,
                PITCH_DEVIATION,
                ("more varied than others here", "flatter than others here"),
            )
            if enough
            else None,
            pauses=_compared_with(
                rate,
                median_pause_rate,
                PAUSE_DEVIATION,
                ("pauses more than others here", "pauses less than others here"),
            )
            if enough
            else None,
            pause_rate=rate,
            baseline=_baseline_reading(figures, rate, baselines.get(key))
            if enough
            else None,
        )
    return readings
