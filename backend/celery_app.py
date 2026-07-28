import logging
import threading
import time

import redis
from celery import Celery, bootsteps
from celery.signals import setup_logging, task_postrun, worker_ready

from backend.core.redis import REDIS_URL
from backend.utils.deployment_warnings import log_deployment_warnings
from backend.utils.logging_config import setup_logging as configure_logging

logger = logging.getLogger(__name__)


@setup_logging.connect
def config_loggers(*args, **kwargs):
    configure_logging()


@worker_ready.connect
def log_placeholder_secret_warnings_on_worker_start(**kwargs):
    log_deployment_warnings(startup_path="worker startup", logger_instance=logger)


_LIVE_CAPTURE_CHECK_TTL_SECONDS = 2.0
_live_capture_check_cache: tuple[float, bool] | None = None


def _has_active_live_capture() -> bool:
    """Return True while any recording is actively uploading live segments.

    Reloading the live ASR model on every segment costs ~9s (observed in worker
    logs), so while a capture is in flight the per-task cache release is skipped
    to keep Whisper resident. The result is cached briefly so this does not issue
    a DB query after every task on busy workers.
    """
    global _live_capture_check_cache

    now = time.time()
    cached = _live_capture_check_cache
    if cached is not None and now - cached[0] < _LIVE_CAPTURE_CHECK_TTL_SECONDS:
        return cached[1]

    active = False
    try:
        from sqlmodel import select

        from backend.core.db import get_sync_session
        from backend.models.recording import Recording, RecordingStatus

        with get_sync_session() as session:
            row = session.exec(
                select(Recording.id)
                .where(Recording.status == RecordingStatus.UPLOADING)
                .where(Recording.is_deleted == False)  # noqa: E712
                .limit(1)
            ).first()
        active = row is not None
    except Exception as exc:  # noqa: BLE001 -- boundary: a check failure must not block cleanup
        logger.debug("Active-capture check failed; assuming idle: %s", exc)
        active = False

    _live_capture_check_cache = (now, active)
    return active


def _should_release_model_caches() -> bool:
    """Whether the per-task model-cache release should run.

    Skipped when the operator pinned models (``keep_models_loaded``) or while a
    live capture is in flight (keep the live ASR model warm across segments).
    """
    from backend.utils.config_manager import config_manager

    if config_manager.get("keep_models_loaded", False):
        return False
    if _has_active_live_capture():
        return False
    return True


def release_worker_model_caches() -> None:
    try:
        import ctypes
        import sys

        if not _should_release_model_caches():
            return

        logger.info("Releasing worker model caches...")

        loaded_release_hooks = (
            ("backend.processing.transcribe", "release_model_cache"),
            ("backend.processing.diarize", "release_pipeline_cache"),
            ("backend.processing.embedding_core", "release_embedding_model_cache"),
            (
                "backend.processing.segmentation_refinement",
                "release_segmentation_model_cache",
            ),
            ("backend.processing.text_embedding", "release_embedding_model"),
        )
        for module_name, release_name in loaded_release_hooks:
            module = sys.modules.get(module_name)
            if module is None:
                continue
            release = getattr(module, release_name, None)
            if callable(release):
                release()

        import gc

        gc.collect()

        torch_module = sys.modules.get("torch")
        if torch_module is not None:
            try:
                if torch_module.cuda.is_available():
                    torch_module.cuda.empty_cache()
            except Exception as exc:  # noqa: BLE001
                logger.debug("CUDA cache cleanup skipped: %s", exc)

        # Force glibc allocator to release freed pages back to OS
        try:
            ctypes.CDLL("libc.so.6").malloc_trim(0)
            logger.info("Forced glibc malloc_trim cleanup successfully.")
        except Exception as trim_exc:  # noqa: BLE001 -- boundary: malloc_trim only exists on glibc Linux
            logger.debug("malloc_trim skipped or failed: %s", trim_exc)

        logger.info("Worker model caches released.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to release worker model caches: %s", exc)


@task_postrun.connect
def release_model_caches_after_task(**kwargs):
    release_worker_model_caches()


celery_app = Celery(
    "nojoin_worker",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=[
        "backend.worker.tasks",
        "backend.processing.live_transcribe",
        "backend.processing.segment_transcode",
    ],
)

# --- Task routing: resource lanes -------------------------------------------
# Split work so a long GPU job never blocks lightweight CPU/network tasks. Each
# worker process consumes one lane (`-Q gpu|cpu|io`); see docker-compose.
GPU_QUEUE = "gpu"
CPU_QUEUE = "cpu"
IO_QUEUE = "io"

# Explicit per-task routing. Anything unrouted falls back to the GPU lane
# (`task_default_queue`), the safe default: a mis-routed GPU task still finds
# the card, whereas routing it to a GPU-less worker would fail. New tasks must
# be added here when introduced.
TASK_ROUTES = {
    # GPU lane: heavy ML inference. Serialised — the host has one 8 GB card.
    "backend.worker.tasks.process_recording_task": {"queue": GPU_QUEUE},
    "backend.processing.live_transcribe.transcribe_segment_live_task": {
        "queue": GPU_QUEUE
    },
    "backend.worker.tasks.extract_embedding_task": {"queue": GPU_QUEUE},
    "backend.worker.tasks.update_speaker_embedding_task": {"queue": GPU_QUEUE},
    # Re-extracts stored voiceprints after an extraction-method bump. Runs the
    # embedding model over archived audio, so it belongs on the GPU lane where
    # it is serialised behind real recordings rather than competing with them.
    "backend.worker.tasks.rebuild_voiceprints_task": {"queue": GPU_QUEUE},
    "backend.worker.tasks.download_models_task": {"queue": GPU_QUEUE},
    "backend.worker.tasks.get_worker_device_status": {"queue": GPU_QUEUE},
    # CPU lane: ffmpeg transcode/proxy and local disk work.
    "backend.processing.segment_transcode.transcode_segment_task": {"queue": CPU_QUEUE},
    "backend.worker.tasks.generate_proxy_task": {"queue": CPU_QUEUE},
    "backend.worker.tasks.create_backup_task": {"queue": CPU_QUEUE},
    # Orchestrates post-restore rebuilds; dispatches the ffmpeg work to the cpu lane.
    "backend.worker.tasks.finalize_restored_recording_task": {"queue": IO_QUEUE},
    # Restore is IO-bound: unpack an archive and write rows. Running it here rather than
    # in the API keeps job state durable and heavy extraction off the request path.
    "backend.worker.tasks.restore_backup_task": {"queue": IO_QUEUE},
    "backend.worker.tasks.cleanup_backup_artifacts": {"queue": IO_QUEUE},
    # IO/LLM lane: network-bound (LLM APIs, calendar) and light bookkeeping.
    "backend.worker.tasks.refresh_meeting_edge_task": {"queue": IO_QUEUE},
    "backend.worker.tasks.generate_notes_task": {"queue": IO_QUEUE},
    "backend.worker.tasks.generate_meeting_intelligence_task": {"queue": IO_QUEUE},
    "backend.worker.tasks.infer_speakers_task": {"queue": IO_QUEUE},
    "backend.worker.tasks.meeting_chat_task": {"queue": IO_QUEUE},
    "backend.worker.tasks.generate_notes_structure_task": {"queue": IO_QUEUE},
    "backend.worker.tasks.codex_device_login_task": {"queue": IO_QUEUE},
    "backend.worker.tasks.refresh_codex_models_task": {"queue": IO_QUEUE},
    "backend.worker.tasks.process_document_task": {"queue": IO_QUEUE},
    "backend.worker.tasks.index_transcript_task": {"queue": IO_QUEUE},
    "backend.worker.tasks.get_text_embedding_task": {"queue": IO_QUEUE},
    "backend.worker.tasks.sync_calendar_connection_task": {"queue": IO_QUEUE},
    "backend.worker.tasks.sync_calendar_connections_task": {"queue": IO_QUEUE},
    "backend.worker.tasks.ensure_calendar_push_channels_task": {"queue": IO_QUEUE},
    "backend.worker.tasks.renew_calendar_push_channels_task": {"queue": IO_QUEUE},
    "backend.worker.tasks.cleanup_temp_recordings": {"queue": IO_QUEUE},
    # Local disk work on the model volume. Belongs on a worker at all because
    # the API mounts that volume read-only.
    "backend.worker.tasks.delete_model_task": {"queue": IO_QUEUE},
    "backend.worker.tasks.send_telemetry_ping_task": {"queue": IO_QUEUE},
}

# Recordings touched by one automatic voiceprint-rebuild tick. The rebuild runs
# the embedding model on the GPU lane, which is also where live transcription
# and final processing run, so a sweep must never enqueue an unbounded pile of
# work ahead of a real meeting. Bounding each tick trades a slower repair for a
# GPU that stays responsive; the sweep repeats, so a large library still
# converges, just over several ticks rather than one.
AUTOMATIC_VOICEPRINT_REBUILD_LIMIT = 25

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_routes=TASK_ROUTES,
    task_default_queue=GPU_QUEUE,
    # One reserved message per worker process so a slow task cannot hoard a batch
    # queued behind it; keeps the CPU/IO lanes fair under load.
    worker_prefetch_multiplier=1,
    beat_schedule={
        "cleanup-temp-recordings-every-24h": {
            "task": "backend.worker.tasks.cleanup_temp_recordings",
            "schedule": 86400.0,  # 24 hours in seconds
        },
        # Reclaims exported archives, uploaded archives, abandoned multipart uploads and
        # any restore staging left behind by a worker that died mid-restore.
        "cleanup-backup-artifacts-every-6h": {
            "task": "backend.worker.tasks.cleanup_backup_artifacts",
            "schedule": 21600.0,
        },
        "sync-calendar-connections-every-15m": {
            "task": "backend.worker.tasks.sync_calendar_connections_task",
            "schedule": 900.0,
        },
        "renew-calendar-push-channels-every-30m": {
            "task": "backend.worker.tasks.renew_calendar_push_channels_task",
            "schedule": 1800.0,
        },
        # Anonymous opt-out telemetry (docs/TELEMETRY.md). The task itself is a
        # no-op unless the install is enabled and has consented, so scheduling it
        # unconditionally is safe. Beat runs on the io lane only, so exactly one
        # ping is attempted per day per install regardless of worker count.
        "send-telemetry-ping-every-24h": {
            "task": "backend.worker.tasks.send_telemetry_ping_task",
            "schedule": 86400.0,
        },
        # Repairs voiceprints stranded by an extraction-method upgrade. Stale
        # voiceprints stop contributing to speaker identification silently, so
        # waiting for someone to notice and ask for a repair means the feature
        # degrades unobserved. The task queries first and returns in
        # milliseconds when nothing is stale, which is the steady state, so
        # scheduling it unconditionally costs nothing.
        "rebuild-stale-voiceprints-every-6h": {
            "task": "backend.worker.tasks.rebuild_voiceprints_task",
            "schedule": 21600.0,
            "kwargs": {"limit": AUTOMATIC_VOICEPRINT_REBUILD_LIMIT},
        },
    },
)


# --- Behaviour when Redis is unreachable -------------------------------------
#
# Dispatching a task is a blocking call, and the API dispatches from `async def`
# handlers, so whatever it blocks for stalls that worker process's whole event
# loop -- every concurrent request, not just the one dispatching.
#
# The default bound on that is the operating system's. A broker that refuses
# connections fails per attempt in microseconds, but one that is merely
# unreachable (a partition, a hung container, a dropped-packet firewall rule)
# does not answer at all, and each attempt then waits out the kernel's TCP
# connect timeout -- roughly two minutes at the default `tcp_syn_retries`.
# Multiplied by the result backend's own 20 retries that is nearly an hour of
# wedged event loop for one best-effort refresh nobody is waiting on.
#
# Capping the connect is the fix, and it is safe for both processes: it bounds
# how long one attempt waits for a TCP handshake and does nothing to an
# established connection, so the worker's blocking queue reads are unaffected.
# How many attempts to make is where the two processes genuinely differ; see
# `apply_api_dispatch_limits` below and ADR-0007.
REDIS_CONNECT_TIMEOUT_SECONDS = 2.0

# The two halves are configured through different surfaces, which is easy to
# get wrong: the broker reads its transport options, while the result backend
# reads top-level `redis_*` keys and ignores anything put in
# `result_backend_transport_options` except its retry policy.
celery_app.conf.broker_transport_options = {
    **celery_app.conf.broker_transport_options,
    "socket_connect_timeout": REDIS_CONNECT_TIMEOUT_SECONDS,
}
celery_app.conf.redis_socket_connect_timeout = REDIS_CONNECT_TIMEOUT_SECONDS


def apply_api_dispatch_limits() -> None:
    """Make a dispatch from the API give up quickly. Call from the API only.

    The worker and the API want opposite things from a Redis outage. A worker
    writing a result is on its own thread with nothing waiting on it, so
    retrying hard is right: the alternative is a finished job whose result is
    lost. An API handler is on the event loop, so retrying hard is exactly
    wrong: it converts one unavailable dependency into a stalled process.

    So the retry *counts* are set here, in the API process, rather than in the
    shared configuration above. A dispatch against an unreachable broker then
    costs about 6s rather than being unbounded: a small multiple of the connect
    cap, since the pubsub reconnect makes an attempt of its own either side of
    the retry loop. Every API dispatch either queues background work whose
    failure the caller already tolerates, or returns a task id the client
    re-polls, so failing fast costs a retry the client can make and never a
    result that cannot be recovered.
    """
    fail_fast = {
        "max_retries": 1,
        "interval_start": 0,
        "interval_step": 0.2,
        "interval_max": 0.5,
    }

    celery_app.conf.task_publish_retry_policy = fail_fast
    # The result backend keeps its own retry policy, which no top-level Celery
    # setting reaches; `result_backend_transport_options["retry_policy"]` is
    # the documented way in. It matters because `send_task` subscribes the
    # backend to the new task's channel *before* publishing, so an unreachable
    # backend blocks the dispatch even though the caller wants only to enqueue.
    celery_app.conf.result_backend_transport_options = {
        **celery_app.conf.result_backend_transport_options,
        "retry_policy": fail_fast,
    }
    # The backend object is built once from the configuration above and cached,
    # so an already-instantiated one would keep the unbounded policy. Drop both
    # caches Celery may hold it in and let the next access rebuild it.
    celery_app._backend_cache = None
    celery_app._local.__dict__.pop("backend", None)


# Heartbeat implementation to keep worker "active" during heavy tasks
class HeartbeatThread(threading.Thread):
    def __init__(self, redis_url, interval=5.0, expire=15):
        super().__init__()
        self.redis_url = redis_url
        self.interval = interval
        self.expire = expire
        self.daemon = True
        self.stop_event = threading.Event()

    def run(self):
        try:
            r = redis.from_url(self.redis_url)
            while not self.stop_event.is_set():
                try:
                    r.set("nojoin:worker:heartbeat", "1", ex=self.expire)
                except Exception as e:  # noqa: BLE001
                    # Log error but don't crash the thread immediately
                    logger.warning(f"Heartbeat error: {e}")
                time.sleep(self.interval)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Heartbeat thread failed to start: {e}")

    def stop(self):
        self.stop_event.set()


class HeartbeatStep(bootsteps.StartStopStep):
    def start(self, worker):
        self.t = HeartbeatThread(REDIS_URL)
        self.t.start()

    def stop(self, worker):
        self.t.stop()
        self.t.join()


celery_app.steps["worker"].add(HeartbeatStep)

if __name__ == "__main__":
    celery_app.start()
