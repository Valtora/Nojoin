import os
import logging
from celery import Task
from sqlmodel import select
from backend.celery_app import celery_app
from backend.core.db import get_sync_session
from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.models.speaker import RecordingSpeaker
from backend.models.tag import RecordingTag  # Import this to resolve the relationship
from backend.processing.vad import mute_non_speech_segments
from backend.processing.audio_preprocessing import convert_wav_to_mp3, preprocess_audio_for_vad
from backend.processing.transcribe import transcribe_audio
from backend.processing.diarize import diarize_audio
from backend.utils.transcript_utils import combine_transcription_diarization, consolidate_diarized_transcript
from backend.utils.audio import get_audio_duration

logger = logging.getLogger(__name__)

class DatabaseTask(Task):
    _session = None

    @property
    def session(self):
        if self._session is None:
            self._session = get_sync_session()
        return self._session

    def after_return(self, status, retval, task_id, args, kwargs, einfo):
        if self._session:
            self._session.close()

@celery_app.task(base=DatabaseTask, bind=True)
def process_recording_task(self, recording_id: int):
    """
    Full processing pipeline: VAD -> Transcribe -> Diarize -> Save
    """
    session = self.session
    
    # 1. Fetch Recording
    recording = session.get(Recording, recording_id)
    if not recording:
        logger.error(f"Recording {recording_id} not found.")
        return
    
    # Fix missing duration if needed
    if (not recording.duration_seconds or recording.duration_seconds == 0) and os.path.exists(recording.audio_path):
        try:
            duration = get_audio_duration(recording.audio_path)
            recording.duration_seconds = duration
            session.add(recording)
            session.commit()
            session.refresh(recording)
        except Exception as e:
            logger.warning(f"Could not determine duration for recording {recording_id}: {e}")
    
    try:
        # Update Status
        recording.status = RecordingStatus.PROCESSING
        session.add(recording)
        session.commit()
        
        audio_path = recording.audio_path
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        # --- VAD Stage ---
        self.update_state(state='PROCESSING', meta={'progress': 10, 'stage': 'VAD'})
        
        # Preprocess for VAD (resample to 16k mono)
        vad_input_path = preprocess_audio_for_vad(audio_path)
        if not vad_input_path:
            raise RuntimeError("VAD preprocessing failed")
            
        # Run VAD (mute silence)
        vad_output_path = vad_input_path.replace("_vad.wav", "_vad_processed.wav")
        vad_success = mute_non_speech_segments(vad_input_path, vad_output_path)
        if not vad_success:
             raise RuntimeError("VAD execution failed")
             
        # Use the processed WAV for subsequent steps
        processed_audio_path = vad_output_path
        
        # --- Transcription Stage ---
        self.update_state(state='PROCESSING', meta={'progress': 30, 'stage': 'Transcription'})
        
        # Run Whisper
        transcription_result = transcribe_audio(processed_audio_path)
        
        # --- Diarization Stage ---
        self.update_state(state='PROCESSING', meta={'progress': 60, 'stage': 'Diarization'})
        
        # Run Pyannote
        diarization_result = diarize_audio(processed_audio_path)
        
        # --- Merge & Save ---
        self.update_state(state='PROCESSING', meta={'progress': 80, 'stage': 'Saving'})
        
        # Combine Transcription and Diarization
        combined_segments = combine_transcription_diarization(transcription_result, diarization_result)
        
        if not combined_segments:
            # Fallback if combination fails (e.g. no diarization segments)
            logger.warning("Combination failed, using raw transcription segments with UNKNOWN speaker.")
            combined_segments = [
                {
                    "start": seg["start"],
                    "end": seg["end"],
                    "speaker": "UNKNOWN",
                    "text": seg["text"].strip()
                }
                for seg in transcription_result.get('segments', [])
            ]

        # Consolidate segments
        final_segments = consolidate_diarized_transcript(combined_segments)
        
        # Create Transcript Record
        transcript = Transcript(
            recording_id=recording.id,
            text=transcription_result.get('text', ''),
            segments=final_segments
        )
        session.add(transcript)
        
        # Save Speakers
        # Extract unique speakers from the final segments
        unique_speakers = set(seg['speaker'] for seg in final_segments)
        
        # Also check diarization result for any speakers that might have been missed in consolidation
        # (though we only care about speakers who actually spoke in the transcript)
        
        for label in unique_speakers:
            # Check if speaker already exists for this recording (idempotency)
            existing_speaker = session.exec(
                select(RecordingSpeaker)
                .where(RecordingSpeaker.recording_id == recording.id)
                .where(RecordingSpeaker.diarization_label == label)
            ).first()
            
            if not existing_speaker:
                rec_speaker = RecordingSpeaker(
                    recording_id=recording.id,
                    diarization_label=label,
                    name=label # Default name to label
                )
                session.add(rec_speaker)
            
        # Update Recording Status
        recording.status = RecordingStatus.PROCESSED
        session.add(recording)
        session.commit()
        
        # Cleanup temp files
        try:
            if os.path.exists(vad_input_path): os.remove(vad_input_path)
            if os.path.exists(vad_output_path): os.remove(vad_output_path)
        except Exception as cleanup_error:
            logger.warning(f"Failed to cleanup temp files: {cleanup_error}")
        
        return {"status": "success", "recording_id": recording_id}

    except Exception as e:
        logger.error(f"Processing failed for {recording_id}: {e}", exc_info=True)
        # Re-fetch recording to ensure we are attached to session if rollback happened
        recording = session.get(Recording, recording_id)
        if recording:
            recording.status = RecordingStatus.ERROR
            session.add(recording)
            session.commit()
        raise e
