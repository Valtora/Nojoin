"""Guards for the Celery Beat schedule.

The worker runs embedded beat (``celery worker -B``); these entries are what make
calendar sync and push-channel renewal fire on a cadence, so a regression here
silently stops background calendar work.
"""

from backend.celery_app import celery_app


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
