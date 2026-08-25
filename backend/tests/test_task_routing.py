"""Guards for Celery task routing into resource lanes.

Work is split across ``gpu`` / ``cpu`` / ``io`` queues (see
``backend/celery_app.py``) so a long GPU job never blocks lightweight CPU or
network tasks. A regression here would silently re-serialise the worker or route
a GPU task to a GPU-less lane where it would fail.
"""

# Importing the task packages registers every task on the app so the
# completeness check below sees the full surface.
from types import SimpleNamespace

import backend.processing.live_transcribe  # noqa: F401
import backend.processing.segment_transcode  # noqa: F401
import backend.worker.tasks  # noqa: F401
from backend.celery_app import (
    CPU_QUEUE,
    GPU_QUEUE,
    IO_QUEUE,
    TASK_ROUTES,
    celery_app,
)
from backend.worker.tasks.pipeline import _meeting_intelligence_runs_on_io


def _queue(task_name: str) -> str:
    return TASK_ROUTES[task_name]["queue"]


def test_heavy_gpu_tasks_route_to_gpu_lane() -> None:
    for task in (
        "backend.worker.tasks.process_recording_task",
        "backend.processing.live_transcribe.transcribe_segment_live_task",
        "backend.worker.tasks.extract_embedding_task",
        "backend.worker.tasks.update_speaker_embedding_task",
    ):
        assert _queue(task) == GPU_QUEUE


def test_ffmpeg_tasks_route_to_cpu_lane() -> None:
    for task in (
        "backend.processing.segment_transcode.transcode_segment_task",
        "backend.worker.tasks.generate_proxy_task",
    ):
        assert _queue(task) == CPU_QUEUE


def test_network_tasks_route_to_io_lane() -> None:
    for task in (
        "backend.worker.tasks.refresh_meeting_edge_task",
        "backend.worker.tasks.generate_notes_task",
        "backend.worker.tasks.generate_meeting_intelligence_task",
        "backend.worker.tasks.infer_speakers_task",
        "backend.worker.tasks.meeting_chat_task",
        "backend.worker.tasks.sync_calendar_connections_task",
    ):
        assert _queue(task) == IO_QUEUE


def test_unrouted_tasks_fall_back_to_gpu_lane() -> None:
    # Safe default: a mis-routed GPU task still finds the card, whereas a
    # GPU-less lane would fail it outright.
    assert celery_app.conf.task_default_queue == GPU_QUEUE


def test_prefetch_multiplier_is_one_for_fair_dispatch() -> None:
    assert celery_app.conf.worker_prefetch_multiplier == 1


def test_pool_children_are_recycled_as_a_drift_backstop() -> None:
    """A prefork child must be retired eventually, and not often.

    The limit is a backstop against an undetected leak, not a memory
    optimisation, so both directions are a regression. Removing it lets a leak
    run for the life of the container; dropping it low turns every lane into a
    fork-and-reimport loop, which lands inside live meetings on the cpu and io
    lanes. worker-parse overrides this on its command line, which wins over the
    setting here.
    """
    assert celery_app.conf.worker_max_tasks_per_child == 500


def test_no_memory_based_recycle_limit_is_configured() -> None:
    """`worker_max_memory_per_child` must stay unset.

    Billiard reads `resource.getrusage(RUSAGE_SELF).ru_maxrss`, which on Linux
    is a peak high-water mark that never falls. A child that once exceeded the
    threshold therefore recycles after every subsequent task, and the per-task
    `malloc_trim` in this module cannot bring the reading back down. Setting one
    needs a measured per-lane peak to calibrate against; none exists yet.
    """
    assert celery_app.conf.worker_max_memory_per_child is None


def _llm_config(provider, *, missing=None):
    # The routing decision only reads .provider and .missing_configuration_message().
    return SimpleNamespace(
        provider=provider,
        missing_configuration_message=lambda: missing,
    )


def test_non_local_intelligence_is_deferred_to_the_io_lane() -> None:
    # Cloud APIs and the CLI OAuth subscription are network-bound and need no GPU,
    # so their meeting-intelligence generation runs on the IO worker.
    for provider in ("anthropic", "openai", "gemini", "cli"):
        assert _meeting_intelligence_runs_on_io(_llm_config(provider)) is True


def test_local_or_unconfigured_intelligence_stays_inline_on_gpu() -> None:
    # Local Ollama stays inline; a blank/unconfigured provider is left to the
    # inline stage, which cheaply falls back to rule-based suggestions with no
    # network call (and no risk of a stuck notes_status on the IO lane).
    assert _meeting_intelligence_runs_on_io(_llm_config("ollama")) is False
    assert _meeting_intelligence_runs_on_io(_llm_config("")) is False
    assert _meeting_intelligence_runs_on_io(_llm_config(None)) is False
    assert (
        _meeting_intelligence_runs_on_io(_llm_config("anthropic", missing="No API key"))
        is False
    )


def test_every_nojoin_task_has_an_explicit_route() -> None:
    """A task without a route silently lands on the GPU lane; catch that here."""
    unrouted = [
        name
        for name in celery_app.tasks
        if name.startswith(("backend.worker.tasks.", "backend.processing."))
        and name not in TASK_ROUTES
    ]
    assert not unrouted, f"tasks missing an explicit lane route: {sorted(unrouted)}"
