import os
from backend.core.audio_setup import setup_audio_environment
from celery import Celery
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

if __name__ == "__main__":
    celery_app.start()
