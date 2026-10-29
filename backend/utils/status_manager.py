import logging

from sqlmodel import Session, select

from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.utils.reprocess_publication import clear_reprocess_published

logger = logging.getLogger(__name__)


def _retire_run_state(transcript: Transcript, new_status: RecordingStatus) -> None:
    """Clear what only described the run that just ended."""
    if new_status in {RecordingStatus.PROCESSED, RecordingStatus.ERROR}:
        clear_reprocess_published(transcript)
    if new_status == RecordingStatus.PROCESSED:
        transcript.clear_meeting_edge_error()


def update_recording_status(session: Session, recording_id: int) -> None:
    """
    Update final processing status from transcription, independently of outputs.

    Capture owns UPLOADING and PAUSED until Stop explicitly queues the recording.
    Notes and other derived outputs keep their own generating and error states.
    """
    recording = session.get(Recording, recording_id)
    if not recording:
        logger.error(f"Recording {recording_id} not found during status update.")
        return
    if recording.status in {RecordingStatus.UPLOADING, RecordingStatus.PAUSED}:
        return

    transcript = session.exec(
        select(Transcript).where(Transcript.recording_id == recording_id)
    ).first()

    if not transcript:
        # No transcript yet: leave the status to whatever is driving it.
        return

    t_status = transcript.transcript_status
    logger.info(f"Updating status for Recording {recording_id}. Transcript: {t_status}")

    new_status = recording.status

    if t_status == "processing":
        new_status = RecordingStatus.PROCESSING

    # A transcript error marks the recording failed.
    elif t_status == "error":
        new_status = RecordingStatus.ERROR

    elif t_status == "completed":
        # The transcript is viewable whatever state its derived outputs are in.
        new_status = RecordingStatus.PROCESSED

    elif t_status == "pending":
        if (
            recording.status != RecordingStatus.RECORDED
            and recording.status != RecordingStatus.UPLOADING
        ):
            new_status = RecordingStatus.QUEUED

    if new_status != recording.status:
        _retire_run_state(transcript, new_status)
        logger.info(
            f"Transitioning Recording {recording_id} from {recording.status} to {new_status}"
        )
        recording.status = new_status
        session.add(recording)
        session.commit()
        session.refresh(recording)
