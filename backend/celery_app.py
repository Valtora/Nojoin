import os
import threading
import time
import redis
from celery import Celery, bootsteps
from backend.core.audio_setup import setup_audio_environment
from celery.signals import setup_logging
from backend.utils.logging_config import setup_logging as configure_logging

# Setup audio environment (patches torchaudio)
setup_audio_environment()

# Redis Configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

@setup_logging.connect
def config_loggers(*args, **kwargs):
    configure_logging()

celery_app = Celery(
    "nojoin_worker",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["backend.worker.tasks"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
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
                except Exception as e:
                    # Log error but don't crash the thread immediately
                    print(f"Heartbeat error: {e}")
                time.sleep(self.interval)
        except Exception as e:
            print(f"Heartbeat thread failed to start: {e}")

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
