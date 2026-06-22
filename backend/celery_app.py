import os
import threading
import time
import logging
import redis
from celery import Celery, bootsteps
from celery.signals import setup_logging, task_postrun, worker_ready
from backend.utils.logging_config import setup_logging as configure_logging
from backend.utils.deployment_warnings import log_deployment_warnings

logger = logging.getLogger(__name__)


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

@setup_logging.connect
def config_loggers(*args, **kwargs):
    configure_logging()


@worker_ready.connect
def log_placeholder_secret_warnings_on_worker_start(**kwargs):
    log_deployment_warnings(startup_path="worker startup", logger_instance=logger)


def release_worker_model_caches() -> None:
    try:
        import sys
        import ctypes

        from backend.utils.config_manager import config_manager

        if config_manager.get("keep_models_loaded", False):
            return

        logger.info("Releasing worker model caches (keep_models_loaded=False)...")

        loaded_release_hooks = (
            ("backend.processing.transcribe", "release_model_cache"),
            ("backend.processing.diarize", "release_pipeline_cache"),
            ("backend.processing.embedding_core", "release_embedding_model_cache"),
            ("backend.processing.segmentation_refinement", "release_segmentation_model_cache"),
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
        except Exception as trim_exc:
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
    ]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "cleanup-temp-recordings-every-24h": {
            "task": "backend.worker.tasks.cleanup_temp_recordings",
            "schedule": 86400.0,  # 24 hours in seconds
        },
        "sync-calendar-connections-every-15m": {
            "task": "backend.worker.tasks.sync_calendar_connections_task",
            "schedule": 900.0,
        },
    },
)

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

celery_app.steps['worker'].add(HeartbeatStep)

if __name__ == "__main__":
    celery_app.start()
