import logging

from sqlmodel import Session, select

from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript

logger = logging.getLogger(__name__)


def update_recording_status(session: Session, recording_id: int):
    """
    Updates the parent Recording status based on the status of its components (Transcript, Notes).

    Logic:
    - If any component is processing/generating -> PROCESSING
    - If transcript is error -> ERROR
    - If transcript is completed -> PROCESSED (unless notes are processing)
    """
    recording = session.get(Recording, recording_id)
    if not recording:
        logger.error(f"Recording {recording_id} not found during status update.")
        return

    transcript = session.exec(
        select(Transcript).where(Transcript.recording_id == recording_id)
    ).first()

    if not transcript:
        # No transcript yet: leave the status to whatever is driving it.
        return

    t_status = transcript.transcript_status
    n_status = transcript.notes_status

    logger.info(
        f"Updating status for Recording {recording_id}. Transcript: {t_status}, Notes: {n_status}"
    )

    new_status = recording.status

    if t_status == "processing" or n_status == "generating":
        new_status = RecordingStatus.PROCESSING

    # A transcript error marks the recording failed.
    elif t_status == "error":
        new_status = RecordingStatus.ERROR

    elif t_status == "completed":
        # Nothing is processing and the transcript is done, so the recording is
        # viewable whatever state its notes are in.
        new_status = RecordingStatus.PROCESSED

    elif t_status == "pending":
        if (
            recording.status != RecordingStatus.RECORDED
            and recording.status != RecordingStatus.UPLOADING
        ):
            new_status = RecordingStatus.QUEUED

    if new_status != recording.status:
        logger.info(
            f"Transitioning Recording {recording_id} from {recording.status} to {new_status}"
        )
        recording.status = new_status
        session.add(recording)
        session.commit()
        session.refresh(recording)
