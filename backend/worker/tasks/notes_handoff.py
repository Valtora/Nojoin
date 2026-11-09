"""Claim and dispatch automatic notes without growing the ASR orchestrator."""

import logging

from sqlmodel import Session

from backend.celery_app import celery_app
from backend.utils.generation_claim import NOTES_RUN
from backend.utils.generation_run import acquire_run

logger = logging.getLogger(__name__)


def defer_automatic_notes(
    session: Session, recording_id: int, provider: str | None
) -> None:
    """Keep an existing run, otherwise dispatch the single token acquired here."""
    transcript = acquire_run(session, recording_id, NOTES_RUN)
    if transcript is None:
        session.commit()
        return
    token = str(transcript.notes_run_id)
    session.commit()
    celery_app.send_task(
        "backend.worker.tasks.generate_meeting_intelligence_task",
        args=[recording_id, token],
    )
    logger.info(
        "Deferred meeting intelligence for recording %s to the IO lane (provider=%s)",
        recording_id,
        provider,
    )
