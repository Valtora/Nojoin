"""Shared in-process registry of restore job records.

The authoritative job state for a queued restore lives in the Celery result backend;
this registry is how the stages inside one worker process accumulate progress and the
skip report before the task returns them.
"""

from typing import Any, Dict

# job_id -> {status, progress, error, warnings}
restore_jobs: Dict[str, Dict[str, Any]] = {}
