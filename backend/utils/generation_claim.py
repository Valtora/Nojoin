"""When a side run's ``generating`` claim still belongs to a live run.

Notes, the AI analysis and delivery measurement each mark their own status
``generating`` on the transcript row while a worker runs them. Celery
acknowledges a task when a worker takes it, so a run lost with its worker is
never delivered again, and its claim would refuse every later request. A claim
whose row has gone unwritten for longer than the expiry reads as failed and
can be claimed again. The row's ``updated_at`` is the claim time: claiming
stamps it, and the worker renews it when it starts and while it waits on a
model, so only a lost worker's claim outlives the expiry.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, NamedTuple
from uuid import uuid4

from backend.utils.time import utc_now

# Far longer than the gap between a running worker's renewals, and long enough
# for the queue wait before a worker starts the run, so only a claim whose
# worker was lost outlives it. Any later write to the row only defers expiry.
GENERATION_CLAIM_EXPIRY = timedelta(minutes=15)
LOST_GENERATION_MESSAGE = "Generation did not finish. Try again."

GENERATING = "generating"


class RunKind(NamedTuple):
    status_field: str
    run_id_field: str
    progress_field: str
    content_field: str
    clear_error: Callable[[Any], None]


NOTES_RUN = RunKind(
    "notes_status",
    "notes_run_id",
    "notes_progress",
    "notes",
    lambda row: row.set_notes_error_message(None),
)


def generation_active(status: str | None, updated_at: datetime | None) -> bool:
    """A ``generating`` claim its worker may still finish; older ones are claimable."""
    return status == GENERATING and (
        updated_at is None or utc_now() - updated_at < GENERATION_CLAIM_EXPIRY
    )


def reported_generation_status(status: str, updated_at: datetime | None) -> str:
    """A lost claim reads as failed, so the interface offers generation again."""
    if status == GENERATING and not generation_active(status, updated_at):
        return "error"
    return status


def reported_generation_error(
    status: str | None, updated_at: datetime | None, message: str | None
) -> str | None:
    """The stored error, or why a lost claim reads as failed."""
    if status == GENERATING and not generation_active(status, updated_at):
        return LOST_GENERATION_MESSAGE
    return message


def claim_generation(row: Any, field: str | RunKind) -> None:
    """Mark ``field`` generating and stamp the claim, over a lost claim too.

    A lost claim already reads ``generating``, so writing that value again
    changes nothing the ORM would flush, and the row's ``updated_at`` would keep
    the lost run's time. Setting it here is what makes the new claim current.
    """
    if isinstance(field, RunKind):
        setattr(row, field.run_id_field, uuid4())
        setattr(row, field.progress_field, None)
        field.clear_error(row)
        field = field.status_field
    setattr(row, field, GENERATING)
    row.updated_at = utc_now()
