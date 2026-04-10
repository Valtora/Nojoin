from datetime import datetime, timedelta

from backend.utils.processing_eta import estimate_processing_eta


def test_estimate_processing_eta_returns_learning_when_history_is_too_small() -> None:
    now = datetime(2026, 4, 10, 12, 0, 0)
    history = [
        (now - timedelta(minutes=12), now - timedelta(minutes=8), 600.0),
        (now - timedelta(minutes=24), now - timedelta(minutes=16), 1200.0),
    ]

    result = estimate_processing_eta(
        history,
        current_duration_seconds=1800.0,
        processing_started_at=now - timedelta(minutes=3),
        now=now,
    )

    assert result.eta_seconds is None
    assert result.learning is True
    assert result.sample_size == 2


def test_estimate_processing_eta_uses_average_seconds_per_audio_minute() -> None:
    now = datetime(2026, 4, 10, 12, 0, 0)
    history = [
        (now - timedelta(minutes=20), now - timedelta(minutes=10), 600.0),
        (now - timedelta(minutes=35), now - timedelta(minutes=20), 900.0),
        (now - timedelta(minutes=50), now - timedelta(minutes=30), 1200.0),
    ]

    result = estimate_processing_eta(
        history,
        current_duration_seconds=1800.0,
        processing_started_at=now - timedelta(minutes=4),
        now=now,
    )

    assert result.learning is False
    assert result.sample_size == 3
    assert result.eta_seconds == 1560