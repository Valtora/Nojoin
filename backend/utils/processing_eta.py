from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional, Sequence, Tuple


MIN_HISTORY_SAMPLES = 3
MIN_AUDIO_DURATION_SECONDS = 60.0
MAX_REASONABLE_SECONDS_PER_AUDIO_MINUTE = 600.0


@dataclass(frozen=True)
class ProcessingEtaEstimate:
    eta_seconds: Optional[int]
    sample_size: int
    learning: bool


HistorySample = Tuple[Optional[datetime], Optional[datetime], Optional[float]]


def extract_processing_rate_samples(samples: Iterable[HistorySample]) -> list[float]:
    rates: list[float] = []

    for started_at, completed_at, duration_seconds in samples:
        if (
            started_at is None
            or completed_at is None
            or duration_seconds is None
            or duration_seconds < MIN_AUDIO_DURATION_SECONDS
        ):
            continue

        elapsed_seconds = (completed_at - started_at).total_seconds()
        if elapsed_seconds <= 0:
            continue

        audio_minutes = duration_seconds / 60.0
        if audio_minutes <= 0:
            continue

        seconds_per_audio_minute = elapsed_seconds / audio_minutes
        if seconds_per_audio_minute > MAX_REASONABLE_SECONDS_PER_AUDIO_MINUTE:
            continue

        rates.append(seconds_per_audio_minute)

    return rates


def estimate_processing_eta(
    history_samples: Sequence[HistorySample],
    current_duration_seconds: Optional[float],
    processing_started_at: Optional[datetime],
    now: Optional[datetime] = None,
) -> ProcessingEtaEstimate:
    if (
        current_duration_seconds is None
        or current_duration_seconds <= 0
        or processing_started_at is None
    ):
        return ProcessingEtaEstimate(eta_seconds=None, sample_size=0, learning=False)

    rate_samples = extract_processing_rate_samples(history_samples)
    sample_size = len(rate_samples)
    if sample_size < MIN_HISTORY_SAMPLES:
        return ProcessingEtaEstimate(eta_seconds=None, sample_size=sample_size, learning=True)

    reference_time = now or datetime.utcnow()
    elapsed_seconds = max(0.0, (reference_time - processing_started_at).total_seconds())
    average_seconds_per_audio_minute = sum(rate_samples) / sample_size
    estimated_total_seconds = average_seconds_per_audio_minute * (current_duration_seconds / 60.0)
    remaining_seconds = max(0, int(round(estimated_total_seconds - elapsed_seconds)))

    return ProcessingEtaEstimate(
        eta_seconds=remaining_seconds,
        sample_size=sample_size,
        learning=False,
    )