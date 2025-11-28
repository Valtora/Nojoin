import os
import logging
import time
import warnings
from celery import Task
from celery.signals import worker_ready
from sqlmodel import select
from backend.celery_app import celery_app
from backend.core.db import get_sync_session
from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.models.speaker import RecordingSpeaker, GlobalSpeaker
from backend.models.tag import RecordingTag  # Import this to resolve the relationship
from backend.models.user import User
# Heavy processing imports moved inside tasks to avoid loading torch in API
from backend.processing.embedding import cosine_similarity, merge_embeddings
from backend.utils.transcript_utils import combine_transcription_diarization, consolidate_diarized_transcript
from backend.utils.audio import get_audio_duration
from backend.utils.config_manager import config_manager

logger = logging.getLogger(__name__)

# Suppress specific warnings in the worker process
warnings.filterwarnings("ignore", message=r".*std\(\): degrees of freedom is <= 0.*")

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
    # Local imports to avoid loading torch in API
    from backend.processing.vad import mute_non_speech_segments
    from backend.processing.audio_preprocessing import convert_wav_to_mp3, preprocess_audio_for_vad
    from backend.processing.transcribe import transcribe_audio
    from backend.processing.diarize import diarize_audio
    from backend.processing.embedding_core import extract_embeddings

    # Reload config to pick up any changes made via the API
    config_manager.reload()
    
    start_time = time.time()
    session = self.session
    
    # 1. Fetch Recording
    recording = session.get(Recording, recording_id)
    if not recording:
        logger.error(f"Recording {recording_id} not found.")
        return
    
    # Fetch User Settings & Merge with System Config
    user_settings = {}
    if recording.user_id:
        user = session.get(User, recording.user_id)
        if user and user.settings:
            user_settings = user.settings
            logger.info(f"Loaded settings for user {user.username}: {list(user_settings.keys())}")
            
    system_config = config_manager.get_all()
    merged_config = system_config.copy()
    merged_config.update(user_settings)
    
    # Update status to PROCESSING
    recording.status = RecordingStatus.PROCESSING
    session.add(recording)
    session.commit()
    session.refresh(recording)
    
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
        recording.processing_step = "Filtering silence and noise..."
        session.add(recording)
        session.commit()
        
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
        recording.processing_step = "Transcribing audio..."
        session.add(recording)
        session.commit()
        
        # Run Whisper
        transcription_result = transcribe_audio(processed_audio_path, config=merged_config)
        
        # --- Diarization Stage ---
        self.update_state(state='PROCESSING', meta={'progress': 60, 'stage': 'Diarization'})
        recording.processing_step = "Determining who said what..."
        session.add(recording)
        session.commit()
        
        # Run Pyannote
        diarization_result = diarize_audio(processed_audio_path, config=merged_config)
        
        # --- Merge & Save ---
        self.update_state(state='PROCESSING', meta={'progress': 80, 'stage': 'Saving'})
        
        # Combine Transcription and Diarization
        combined_segments = []
        if transcription_result:
            combined_segments = combine_transcription_diarization(transcription_result, diarization_result)
        
        logger.info(f"Combined segments count: {len(combined_segments) if combined_segments else 0}")
        
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
        logger.info(f"Final segments after consolidation: {len(final_segments)}")
        
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
        # We want to process them in order of appearance to assign "Speaker 1", "Speaker 2", etc.
        ordered_speakers = []
        seen_speakers = set()
        for seg in final_segments:
            spk = seg['speaker']
            if spk not in seen_speakers:
                ordered_speakers.append(spk)
                seen_speakers.add(spk)
        
        logger.info(f"Extracted {len(ordered_speakers)} unique speakers from segments: {ordered_speakers}")
        
        # Extract embeddings for all speakers in the diarization result (if enabled)
        # Voiceprint extraction can be disabled to speed up processing
        enable_auto_voiceprints = merged_config.get("enable_auto_voiceprints", True)
        speaker_embeddings = {}
        
        if enable_auto_voiceprints and diarization_result:
            self.update_state(state='PROCESSING', meta={'progress': 85, 'stage': 'Voiceprints'})
            recording.processing_step = "Learning voiceprints..."
            session.add(recording)
            session.commit()
            logger.info("Extracting speaker voiceprints (enable_auto_voiceprints=True)")
            speaker_embeddings = extract_embeddings(processed_audio_path, diarization_result, device_str=merged_config.get("processing_device", "cpu"), config=merged_config)
        elif not enable_auto_voiceprints:
            logger.info("Skipping voiceprint extraction (enable_auto_voiceprints=False)")
        
        # Map local labels (SPEAKER_00) to resolved names (John Doe or Speaker 1)
        label_map = {} 
        speaker_counter = 1
        
        for label in ordered_speakers:
            # Check if speaker already exists for this recording (idempotency)
            existing_speaker = session.exec(
                select(RecordingSpeaker)
                .where(RecordingSpeaker.recording_id == recording.id)
                .where(RecordingSpeaker.diarization_label == label)
            ).first()
            
            embedding = speaker_embeddings.get(label)
            resolved_name = label # Default fallback
            global_speaker_id = None
            is_identified = False
            
            # Try to identify speaker using embedding
            if embedding:
                # Fetch all global speakers with embeddings
                # Filter out any potential placeholder names from the global list to prevent bad linking
                all_global_speakers = session.exec(select(GlobalSpeaker).where(GlobalSpeaker.embedding != None)).all()
                
                import re
                placeholder_pattern = re.compile(r"^(SPEAKER_\d+|Speaker \d+|Unknown)$", re.IGNORECASE)
                
                global_speakers = [
                    gs for gs in all_global_speakers 
                    if not placeholder_pattern.match(gs.name)
                ]
                
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
                    is_identified = True
                    
                    # Active Learning: Update Global Speaker embedding with new data
                    # This keeps the profile up-to-date with latest voice samples
                    try:
                        new_emb = merge_embeddings(best_match.embedding, embedding)
                        best_match.embedding = new_emb
                        session.add(best_match)
                    except Exception as e:
                        logger.warning(f"Failed to update embedding for {best_match.name}: {e}")
                else:
                    logger.info(f"No match found for {label} (Best score: {best_score:.2f}).")

            # If not identified as a global speaker, assign a friendly sequential name
            if not is_identified:
                resolved_name = f"Speaker {speaker_counter}"
                speaker_counter += 1

            label_map[label] = resolved_name
            logger.info(f"Mapped {label} -> {resolved_name}")

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
        
        # Log final speaker distribution in updated segments
        final_speaker_counts = {}
        for seg in updated_segments:
            spk = seg['speaker']
            final_speaker_counts[spk] = final_speaker_counts.get(spk, 0) + 1
        logger.info(f"Final transcript speaker distribution: {final_speaker_counts}")
            
        transcript.segments = updated_segments
        session.add(transcript)

        # Update Recording Status
        recording.status = RecordingStatus.PROCESSED
        recording.processing_step = "Completed"
        session.add(recording)
        session.commit()
        
        # Cleanup temp files
        try:
            if os.path.exists(vad_input_path): os.remove(vad_input_path)
            if os.path.exists(vad_output_path): os.remove(vad_output_path)
        except Exception as cleanup_error:
            logger.warning(f"Failed to cleanup temp files: {cleanup_error}")
        
        elapsed_time = time.time() - start_time
        logger.info(f"Recording: [{recording_id}] processing succeeded in {elapsed_time:.2f} seconds")
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

@celery_app.task(base=DatabaseTask, bind=True)
def update_speaker_embedding_task(self, recording_id: int, start: float, end: float, recording_speaker_id: int):
    """
    Update the speaker embedding for a specific segment (Active Learning).
    """
    from backend.processing.embedding_core import extract_embedding_for_segments
    session = self.session
    try:
        recording = session.get(Recording, recording_id)
        if not recording or not recording.audio_path or not os.path.exists(recording.audio_path):
            logger.warning(f"Recording {recording_id} not found or audio missing.")
            return

        target_recording_speaker = session.get(RecordingSpeaker, recording_speaker_id)
        if not target_recording_speaker:
            logger.warning(f"RecordingSpeaker {recording_speaker_id} not found.")
            return

        device = "cuda" if config_manager.get("use_gpu", True) else "cpu"
        
        # Extract embedding for this segment
        # We pass a list of segments [(start, end)]
        new_embedding = extract_embedding_for_segments(
            recording.audio_path, 
            [(start, end)], 
            device_str=device
        )

        if new_embedding:
            # Merge into RecordingSpeaker
            current_emb = target_recording_speaker.embedding if target_recording_speaker.embedding is not None else []
            
            target_recording_speaker.embedding = merge_embeddings(
                current_emb, 
                new_embedding, 
                alpha=0.5
            )
            session.add(target_recording_speaker)
            
            # Merge into GlobalSpeaker
            if target_recording_speaker.global_speaker_id:
                gs = session.get(GlobalSpeaker, target_recording_speaker.global_speaker_id)
                if gs:
                    gs_emb = gs.embedding if gs.embedding is not None else []
                    gs.embedding = merge_embeddings(
                        gs_emb,
                        new_embedding,
                        alpha=0.5
                    )
                    session.add(gs)
            
            session.commit()
            logger.info(f"Updated embedding for speaker {target_recording_speaker.diarization_label}")
        else:
            logger.warning("Failed to extract embedding for update.")

    except Exception as e:
        logger.error(f"Failed to update speaker embedding: {e}", exc_info=True)
        session.rollback()

@celery_app.task(bind=True)
def extract_embedding_task(self, audio_path: str, segments: list, device_str: str = "cpu"):
    """
    Extract embedding from segments. Used by API for synchronous-like operations.
    """
    from backend.processing.embedding_core import extract_embedding_for_segments
    try:
        return extract_embedding_for_segments(audio_path, segments, device_str)
    except Exception as e:
        logger.error(f"Failed to extract embedding task: {e}", exc_info=True)
        return None

@worker_ready.connect
def check_queued_recordings(sender, **kwargs):
    """
    On worker startup, check for any recordings that are stuck in QUEUED state
    and re-queue them.
    """
    logger.info("Checking for pending QUEUED recordings...")
    session = get_sync_session()
    try:
        statement = select(Recording).where(Recording.status == RecordingStatus.QUEUED)
        recordings = session.exec(statement).all()
        
        if not recordings:
            logger.info("No pending recordings found.")
            return

        logger.info(f"Found {len(recordings)} pending recordings. Re-queueing...")
        
        for recording in recordings:
            logger.info(f"Re-queueing recording {recording.id}: {recording.name}")
            process_recording_task.delay(recording.id)
            
    except Exception as e:
        logger.error(f"Failed to check pending recordings: {e}", exc_info=True)
    finally:
        session.close()

@celery_app.task(bind=True)
def get_worker_device_status(self):
    """
    Check the worker's available processing device (CUDA/CPU).
    """
    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        gpu_name = torch.cuda.get_device_name(0) if device == "cuda" else None
        return {
            "device": device,
            "gpu_name": gpu_name,
            "torch_version": torch.__version__
        }
    except ImportError:
        return {"device": "cpu", "error": "torch not installed"}
    except Exception as e:
        return {"device": "unknown", "error": str(e)}
