"""Celery beat's schedule store survives restarts, kills and older files (#917).

Celery's default shelve sits on Berkeley DB in the worker image, whose file a
killed beat left unreadable; every interval then started again. These tests run
the configured scheduler against the real beat schedule.
"""

import json
import logging
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from celery.beat import PersistentScheduler
from celery.utils.imports import symbol_by_name

from backend.celery_app import celery_app
from backend.utils import beat_schedule_store
from backend.utils.beat_schedule_store import JsonFileScheduler

RunState = dict[str, tuple[datetime, int]]


def _start(base: Path) -> JsonFileScheduler:
    return JsonFileScheduler(app=celery_app, schedule_filename=str(base))


def _runs(scheduler: JsonFileScheduler) -> RunState:
    return {
        name: (entry.last_run_at, entry.total_run_count)
        for name, entry in scheduler.schedule.items()
    }


def _run_some(scheduler: JsonFileScheduler) -> None:
    names = sorted(scheduler.schedule)
    for name in names[:3] + names[:1]:
        scheduler.reserve(scheduler.schedule[name])


def test_the_worker_runs_beat_on_the_json_store() -> None:
    # `celery worker -B` resolves its scheduler with this call (WorkController).
    configured = celery_app.either("beat_scheduler", None, None)
    assert symbol_by_name(configured) is JsonFileScheduler


def test_the_schedule_survives_a_close_and_reopen(tmp_path: Path) -> None:
    base = tmp_path / "celerybeat-schedule"
    first = _start(base)
    _run_some(first)
    expected = _runs(first)
    first.close()

    second = _start(base)

    assert _runs(second) == expected
    assert set(second.schedule) == set(celery_app.conf.beat_schedule)
    assert max(count for _, count in expected.values()) == 2


def test_a_dispatch_is_on_disk_before_any_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A beat killed straight after sending a task keeps that run."""
    base = tmp_path / "celerybeat-schedule"
    running = _start(base)
    monkeypatch.setattr(running, "apply_async", lambda *args, **kwargs: None)
    name = sorted(running.schedule)[0]
    entry = running.schedule[name]
    # What Scheduler.tick does with a due entry.
    running.reserve(entry)
    running.apply_entry(entry)

    after_kill = _start(base)

    assert _runs(after_kill)[name] == _runs(running)[name]
    assert after_kill.schedule[name].total_run_count == 1


def test_an_unreadable_store_starts_afresh_and_is_rewritten(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    base = tmp_path / "celerybeat-schedule"
    store = tmp_path / "celerybeat-schedule.json"
    store.write_bytes(b"\x00\x01 half a file")
    before = datetime.now(UTC)

    with caplog.at_level(logging.ERROR, logger=beat_schedule_store.__name__):
        scheduler = _start(base)

    assert "unreadable" in caplog.text
    assert set(scheduler.schedule) == set(celery_app.conf.beat_schedule)
    assert all(
        last >= before - timedelta(seconds=1) for last, _ in _runs(scheduler).values()
    )
    assert json.loads(store.read_text())["version"] == 1


def test_one_damaged_entry_does_not_cost_the_others(tmp_path: Path) -> None:
    base = tmp_path / "celerybeat-schedule"
    first = _start(base)
    _run_some(first)
    first.close()
    store = tmp_path / "celerybeat-schedule.json"
    document = json.loads(store.read_text())
    damaged, *kept = sorted(document["entries"])
    document["entries"][damaged]["last_run_at"] = "yesterday"
    store.write_text(json.dumps(document))

    second = _start(base)

    assert {name: _runs(second)[name] for name in kept} == {
        name: _runs(first)[name] for name in kept
    }


def test_the_previous_shelve_is_carried_over_then_removed(tmp_path: Path) -> None:
    """The first start after the upgrade keeps the intervals the old store held."""
    base = tmp_path / "celerybeat-schedule"
    previous = PersistentScheduler(app=celery_app, schedule_filename=str(base))
    names = sorted(previous.schedule)
    for name in names[:2]:
        previous.reserve(previous.schedule[name])
    expected = {
        name: (entry.last_run_at, entry.total_run_count)
        for name, entry in previous.schedule.items()
    }
    previous.close()

    scheduler = _start(base)

    assert _runs(scheduler) == expected
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "celerybeat-schedule.json"
    ]


def test_a_shelve_this_python_cannot_open_starts_afresh(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A dbm.gnu file from an image whose Python had gdbm, or a damaged one."""
    base = tmp_path / "celerybeat-schedule"
    base.write_bytes(struct.pack("=l", 0x13579ACE) + b"\x00" * 60)

    with caplog.at_level(logging.WARNING, logger=beat_schedule_store.__name__):
        scheduler = _start(base)

    assert "every interval starts afresh" in caplog.text
    assert set(scheduler.schedule) == set(celery_app.conf.beat_schedule)
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "celerybeat-schedule.json"
    ]


def test_a_file_that_only_shares_the_name_is_left_alone(tmp_path: Path) -> None:
    base = tmp_path / "celerybeat-schedule"
    base.write_bytes(b"not a schedule at all")

    _start(base)

    assert base.read_bytes() == b"not a schedule at all"


def test_a_failed_save_does_not_stop_beat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    scheduler = _start(tmp_path / "celerybeat-schedule")

    def full_disk(path: Path, text: str) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(beat_schedule_store, "_write_atomically", full_disk)
    with caplog.at_level(logging.ERROR, logger=beat_schedule_store.__name__):
        scheduler.sync()
        scheduler.close()

    assert "No space left on device" in caplog.text
