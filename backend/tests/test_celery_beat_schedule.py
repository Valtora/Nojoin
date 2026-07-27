"""Guards for the Celery Beat schedule.

The worker runs embedded beat (``celery worker -B``); these entries are what make
calendar sync and push-channel renewal fire on a cadence, so a regression here
silently stops background calendar work.
"""

from backend.celery_app import AUTOMATIC_VOICEPRINT_REBUILD_LIMIT, celery_app


def test_beat_schedule_runs_calendar_sync_every_15_minutes() -> None:
    entry = celery_app.conf.beat_schedule["sync-calendar-connections-every-15m"]
    assert entry["task"] == "backend.worker.tasks.sync_calendar_connections_task"
    assert entry["schedule"] == 900.0


def test_beat_schedule_renews_push_channels_every_30_minutes() -> None:
    entry = celery_app.conf.beat_schedule["renew-calendar-push-channels-every-30m"]
    assert entry["task"] == "backend.worker.tasks.renew_calendar_push_channels_task"
    assert entry["schedule"] == 1800.0


def test_beat_schedule_retains_temp_recording_cleanup() -> None:
    assert "cleanup-temp-recordings-every-24h" in celery_app.conf.beat_schedule


def test_beat_schedule_rebuilds_stale_voiceprints() -> None:
    """The rebuild has no UI trigger, so the schedule is the only thing that runs it."""
    entry = celery_app.conf.beat_schedule["rebuild-stale-voiceprints-every-6h"]
    assert entry["task"] == "backend.worker.tasks.rebuild_voiceprints_task"
    assert entry["schedule"] == 21600.0


def test_automatic_voiceprint_rebuild_is_bounded_per_run() -> None:
    """An unbounded sweep would queue the whole library's GPU work at once.

    The rebuild shares the GPU lane with live transcription and final
    processing, so the per-run cap is what keeps an upgrade from starving a
    meeting in progress. Convergence comes from repeating the sweep.
    """
    entry = celery_app.conf.beat_schedule["rebuild-stale-voiceprints-every-6h"]
    limit = entry["kwargs"]["limit"]
    assert limit == AUTOMATIC_VOICEPRINT_REBUILD_LIMIT
    assert 0 < limit < 500
