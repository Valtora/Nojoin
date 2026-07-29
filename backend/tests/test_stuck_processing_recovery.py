from __future__ import annotations

import types

from backend.models.recording import Recording, RecordingStatus
from backend.worker.tasks import pipeline

GPU_QUEUE = "gpu"


class FakeSession:
    """Minimal stand-in for the sync SQLModel session the sweep uses."""

    def __init__(self, recordings: list[Recording]) -> None:
        self._recordings = recordings
        self.added: list[Recording] = []
        self.commits = 0

    def exec(self, _statement):
        return types.SimpleNamespace(all=lambda: list(self._recordings))

    def add(self, obj) -> None:
        self.added.append(obj)

    def commit(self) -> None:
        self.commits += 1


def processing(recording_id: int, task_id: str | None) -> Recording:
    return Recording(
        id=recording_id,
        name=f"rec-{recording_id}",
        status=RecordingStatus.PROCESSING,
        celery_task_id=task_id,
    )


def test_reclaims_a_recording_whose_task_is_gone(monkeypatch):
    # The production failure: the worker died mid-pipeline, so the recording sits in
    # PROCESSING forever. The startup sweep ignored it and reprocess refuses it.
    monkeypatch.setattr(pipeline, "_live_task_ids", lambda: {"some-other-task"})
    session = FakeSession([processing(104, "dead-task")])

    reclaimed = pipeline._reclaim_orphaned_processing(session)

    assert [r.id for r in reclaimed] == [104]
    assert reclaimed[0].status == RecordingStatus.QUEUED
    assert session.commits == 1


def test_leaves_a_recording_whose_task_is_still_running(monkeypatch):
    monkeypatch.setattr(pipeline, "_live_task_ids", lambda: {"live-task"})
    session = FakeSession([processing(104, "live-task")])

    assert pipeline._reclaim_orphaned_processing(session) == []
    assert session.commits == 0


def test_reclaims_nothing_when_liveness_cannot_be_established(monkeypatch):
    """A broker hiccup must not let one worker's restart steal another's live job."""
    monkeypatch.setattr(pipeline, "_live_task_ids", lambda: None)
    session = FakeSession([processing(104, "unknown")])

    assert pipeline._reclaim_orphaned_processing(session) == []
    assert session.commits == 0


def test_unrecorded_task_id_is_reclaimable_when_nothing_is_running(monkeypatch):
    """Recordings predating the task-id stamp still recover once the queue is idle."""
    monkeypatch.setattr(pipeline, "_live_task_ids", lambda: set())
    session = FakeSession([processing(104, None)])

    assert [r.id for r in pipeline._reclaim_orphaned_processing(session)] == [104]


def test_live_task_ids_reports_unknown_rather_than_empty(monkeypatch):
    """inspect() returns None when no worker answers; that is not 'nothing running'."""

    def inspect(**_kwargs):
        return types.SimpleNamespace(active=lambda: None)

    monkeypatch.setattr(pipeline, "_live_task_ids", pipeline._live_task_ids)
    from backend import celery_app as celery_module

    monkeypatch.setattr(
        celery_module.celery_app, "control", types.SimpleNamespace(inspect=inspect)
    )

    assert pipeline._live_task_ids() is None


def test_live_task_ids_flattens_every_worker(monkeypatch):
    def inspect(**_kwargs):
        return types.SimpleNamespace(
            active=lambda: {
                "celery@gpu": [{"id": "a"}, {"id": "b"}],
                "celery@io": [{"id": "c"}],
                "celery@cpu": [],
            }
        )

    from backend import celery_app as celery_module

    monkeypatch.setattr(
        celery_module.celery_app, "control", types.SimpleNamespace(inspect=inspect)
    )

    assert pipeline._live_task_ids() == {"a", "b", "c"}


def _sender(queues):
    return types.SimpleNamespace(
        app=types.SimpleNamespace(
            amqp=types.SimpleNamespace(
                queues=types.SimpleNamespace(consume_from=queues)
            )
        )
    )


def test_only_the_lane_running_the_task_sweeps():
    # All three lanes import this module, so without the gate each pending recording
    # would be dispatched once per lane.
    assert pipeline._sweeps_recordings(_sender({GPU_QUEUE: object()}))
    assert not pipeline._sweeps_recordings(_sender({"cpu": object(), "io": object()}))


def test_sweeps_when_the_consumed_queues_cannot_be_read():
    """Prefer a duplicate run over leaving recordings stranded."""
    assert pipeline._sweeps_recordings(_sender(None))
    assert pipeline._sweeps_recordings(types.SimpleNamespace(app=None))
