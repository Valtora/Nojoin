"""Writes the step a notes run is on where the page reads it.

See ``backend.utils.notes_progress``. Each step is one short transaction in a
session of its own, on the task session's engine: the task's session is waiting
on the model call, and a step that cannot be written must leave it untouched.
"""

import logging

from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col

from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.utils.generation_claim import GENERATING
from backend.utils.notes_progress import NotesProgressReporter
from backend.utils.time import utc_now

logger = logging.getLogger(__name__)

STEP_MAX_LENGTH = 255


def notes_progress_writer(
    session: Session, recording_id: int, *, processing_step_suffix: str | None = None
) -> NotesProgressReporter:
    """A reporter that records each step on the transcript's notes claim.

    Only a claim still generating takes the step, which also renews it: the
    worker writing it is alive. With ``processing_step_suffix`` the step is the
    recording's processing step as well, for the stage that runs inside the
    pipeline and owns that step while it does.
    """
    bind = session.get_bind()

    def write(step: str) -> None:
        try:
            with Session(bind) as progress_session:
                progress_session.execute(
                    update(Transcript)
                    .where(col(Transcript.recording_id) == recording_id)
                    .where(col(Transcript.notes_status) == GENERATING)
                    .values(notes_progress=step[:STEP_MAX_LENGTH], updated_at=utc_now())
                )
                if processing_step_suffix is not None:
                    processing_step = f"{step}...{processing_step_suffix}"
                    progress_session.execute(
                        update(Recording)
                        .where(col(Recording.id) == recording_id)
                        .where(col(Recording.status) == RecordingStatus.PROCESSING)
                        .values(processing_step=processing_step[:STEP_MAX_LENGTH])
                    )
                progress_session.commit()
        except SQLAlchemyError:
            logger.warning(
                "Could not record notes step %r for recording %s",
                step,
                recording_id,
                exc_info=True,
            )

    return write
