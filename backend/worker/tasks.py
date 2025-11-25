import os
import logging
from celery import Task
from sqlmodel import select
from backend.celery_app import celery_app
from backend.core.db import get_sync_session
from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.models.speaker import RecordingSpeaker, GlobalSpeaker
from backend.models.tag import RecordingTag  # Import this to resolve the relationship
from backend.processing.vad import mute_non_speech_segments
from backend.processing.audio_preprocessing import convert_wav_to_mp3, preprocess_audio_for_vad
from backend.processing.transcribe import transcribe_audio
from backend.processing.diarize import diarize_audio
from backend.processing.embedding import extract_embeddings, cosine_similarity, merge_embeddings
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
             
        # Convert to MP3 (aligning with pipeline.py)
        vad_processed_mp3 = vad_output_path.replace(".wav", ".mp3")
        mp3_success = convert_wav_to_mp3(vad_output_path, vad_processed_mp3)
        if not mp3_success:
             logger.warning("MP3 conversion failed, falling back to WAV")
             # processed_audio_path = vad_output_path # Already WAV
        
        # CRITICAL FIX: Use WAV for processing to avoid sample count mismatches in Pyannote
        processed_audio_path = vad_output_path

        logger.info(f"Using processed audio for transcription/diarization: {processed_audio_path}")
        if not os.path.exists(processed_audio_path):
             raise FileNotFoundError(f"Processed audio file missing: {processed_audio_path}")
        
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
            
            # Check if transcription_result is None before accessing
            if transcription_result and 'segments' in transcription_result:
                combined_segments = [
                    {
                        "start": seg["start"],
                        "end": seg["end"],
                        "speaker": "UNKNOWN",
                        "text": seg["text"].strip()
                    }
                    for seg in transcription_result.get('segments', [])
                ]
            else:
                logger.error("Transcription result is None or missing segments during fallback.")
                combined_segments = []

        # Consolidate segments
        final_segments = consolidate_diarized_transcript(combined_segments)
        
        # Create or Update Transcript Record
        transcript = session.exec(select(Transcript).where(Transcript.recording_id == recording.id)).first()
        
        # Handle case where transcription_result is None (e.g. due to error)
        full_text = transcription_result.get('text', '') if transcription_result else ''
        
        if transcript:
            transcript.text = full_text
            transcript.segments = final_segments
            session.add(transcript)
        else:
            transcript = Transcript(
                recording_id=recording.id,
                text=full_text,
                segments=final_segments
            )
            session.add(transcript)
        
        # Save Speakers & Embeddings
        # Extract unique speakers from the final segments
        unique_speakers = set(seg['speaker'] for seg in final_segments)
        
        # Extract embeddings for all speakers in the diarization result
        # We use the processed_audio_path (MP3) which pyannote can handle
        if diarization_result:
            speaker_embeddings = extract_embeddings(processed_audio_path, diarization_result)
        else:
            speaker_embeddings = {}
        
        # Map local labels (SPEAKER_00) to resolved names (John Doe)
        label_map = {} 
        
        for label in unique_speakers:
            # Check if speaker already exists for this recording (idempotency)
            existing_speaker = session.exec(
                select(RecordingSpeaker)
                .where(RecordingSpeaker.recording_id == recording.id)
                .where(RecordingSpeaker.diarization_label == label)
            ).first()
            
            embedding = speaker_embeddings.get(label)
            resolved_name = label
            global_speaker_id = None
            
            # Try to identify speaker using embedding
            if embedding:
                # Fetch all global speakers with embeddings
                global_speakers = session.exec(select(GlobalSpeaker).where(GlobalSpeaker.embedding != None)).all()
                
                best_match = None
                best_score = 0.0
                SIMILARITY_THRESHOLD = 0.65 # Adjust based on model (wespeaker usually needs ~0.5-0.7)
                
                for gs in global_speakers:
                    score = cosine_similarity(embedding, gs.embedding)
                    if score > best_score:
                        best_score = score
                        best_match = gs
                
                if best_match and best_score > SIMILARITY_THRESHOLD:
                    logger.info(f"Identified {label} as {best_match.name} (Score: {best_score:.2f})")
                    resolved_name = best_match.name
                    global_speaker_id = best_match.id
                    
                    # Active Learning: Update Global Speaker embedding with new data
                    # This keeps the profile up-to-date with latest voice samples
                    try:
                        new_emb = merge_embeddings(best_match.embedding, embedding)
                        best_match.embedding = new_emb
                        session.add(best_match)
                    except Exception as e:
                        logger.warning(f"Failed to update embedding for {best_match.name}: {e}")
                else:
                    logger.info(f"No match found for {label} (Best score: {best_score:.2f}). Keeping as new/unknown.")
                    # Optional: Auto-create Global Speaker? 
                    # For now, we just store the embedding in RecordingSpeaker so we can identify them later.

            label_map[label] = resolved_name

            if existing_speaker:
                existing_speaker.embedding = embedding
                existing_speaker.name = resolved_name
                existing_speaker.global_speaker_id = global_speaker_id
                session.add(existing_speaker)
            else:
                rec_speaker = RecordingSpeaker(
                    recording_id=recording.id,
                    diarization_label=label,
                    name=resolved_name,
                    embedding=embedding,
                    global_speaker_id=global_speaker_id
                )
                session.add(rec_speaker)
        
        # Update Transcript Segments with Resolved Names
        # We need to update the 'speaker' field in the JSON segments
        updated_segments = []
        for seg in final_segments:
            original_label = seg['speaker']
            seg['speaker'] = label_map.get(original_label, original_label)
            updated_segments.append(seg)
            
        transcript.segments = updated_segments
        session.add(transcript)

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
