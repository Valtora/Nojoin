"""When a notes or discussion ``generating`` claim still belongs to a live run."""

from datetime import datetime, timedelta
from typing import Any

from backend.utils.time import utc_now

# Longer than any output's LLM call (notes 300 s, discussion 600 s) plus its
# queue wait, so only a claim whose worker was lost outlives it. The row's
# updated_at is the claim time; a later write to the row only defers expiry.
GENERATION_CLAIM_EXPIRY = timedelta(minutes=15)
LOST_GENERATION_MESSAGE = "Generation did not finish. Try again."


def generation_active(status: str | None, updated_at: datetime | None) -> bool:
    """A ``generating`` claim its worker may still finish; older ones are claimable."""
    return status == "generating" and (
        updated_at is None or utc_now() - updated_at < GENERATION_CLAIM_EXPIRY
    )


def reported_generation_status(status: str, updated_at: datetime | None) -> str:
    """A lost claim reads as failed, so the interface offers generation again."""
    if status == "generating" and not generation_active(status, updated_at):
        return "error"
    return status


def public_generation_metadata(
    metadata: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Coverage for clients; the request ID only fences workers."""
    if metadata is None:
        return None
    return {key: value for key, value in metadata.items() if key != "request_id"}
