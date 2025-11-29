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

    transcript = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
    
    if not transcript:
        # If no transcript exists yet, it might be in early processing or queued
        # We leave it as is or set to QUEUED/PROCESSING depending on context, 
        # but usually this function is called when we have a transcript.
        return

    t_status = transcript.transcript_status
    n_status = transcript.notes_status
    
    logger.info(f"Updating status for Recording {recording_id}. Transcript: {t_status}, Notes: {n_status}")

    new_status = recording.status

    # 1. Check for Processing Activity
    if t_status == "processing" or n_status == "generating":
        new_status = RecordingStatus.PROCESSING
    
    # 2. Check for Errors (Transcript error is critical)
    elif t_status == "error":
        new_status = RecordingStatus.ERROR
        
    # 3. Check for Completion
    elif t_status == "completed":
        # If we are here, it means nothing is processing and no critical errors
        # Notes could be 'completed', 'pending', or 'error' - but if transcript is done, 
        # and nothing is processing, we consider the recording 'PROCESSED' 
        # (or at least ready for viewing)
        new_status = RecordingStatus.PROCESSED
        
    # 4. Fallback/Initial states
    elif t_status == "pending":
        if recording.status != RecordingStatus.RECORDED and recording.status != RecordingStatus.UPLOADING:
             new_status = RecordingStatus.QUEUED

    if new_status != recording.status:
        logger.info(f"Transitioning Recording {recording_id} from {recording.status} to {new_status}")
        recording.status = new_status
        session.add(recording)
        session.commit()
        session.refresh(recording)
