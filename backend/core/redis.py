"""Canonical Redis connection URL shared by the API, worker, and services.

Deployments inject ``REDIS_URL`` through the compose environment (derived from
``REDIS_PASSWORD`` in ``.env``); the localhost default only applies to host-run
development and tests. ``load_dotenv()`` mirrors ``backend.core.db`` so the
value never depends on which module happened to load ``.env`` first.

``backend.utils.deployment_warnings`` deliberately reads the raw environment
variable instead of importing from here: it must tell an unset value apart
from a placeholder password.
"""

import os

from dotenv import load_dotenv

load_dotenv()


def get_redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


REDIS_URL = get_redis_url()
