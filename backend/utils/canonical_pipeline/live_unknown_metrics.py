"""Per-commit observations of covered UNKNOWN live words."""

from logging import Logger

from backend.processing.pipeline_metrics import record_pipeline_metric


def log_covered_unknown_words(
    recording_id: int, count: int | None, *, log: Logger
) -> None:
    """Log the committed observation without persisting counter state."""
    # Readers may sum by recording; a word re-covered later may count twice.
    if count is not None:
        record_pipeline_metric(
            stage="live_covered_unknown_words",
            recording_id=recording_id,
            payload={"covered_unknown_words": count},
            log=log,
        )
