import os
import shutil
import logging
import time
from datetime import datetime, timedelta
import warnings
import urllib.error
import requests.exceptions

from typing import TYPE_CHECKING
from celery import Task
from celery.signals import worker_ready
from sqlmodel import select

from backend.celery_app import celery_app
from backend.core.db import get_sync_session
from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.models.speaker import RecordingSpeaker, GlobalSpeaker
from backend.models.tag import RecordingTag
from backend.models.user import User
from backend.models.invitation import Invitation
from backend.models.chat import ChatMessage
from backend.core.exceptions import AudioProcessingError, AudioFormatError, VADNoSpeechError

# Heavy processing imports moved inside tasks to avoid loading torch in API
from backend.models.document import Document, DocumentStatus
from backend.models.context_chunk import ContextChunk
from backend.utils.config_manager import config_manager, is_llm_available
from backend.utils.status_manager import update_recording_status
from backend.processing.text_embedding import get_text_embedding_service

if TYPE_CHECKING:
    from backend.processing.embedding import cosine_similarity, merge_embeddings
    from backend.utils.transcript_utils import combine_transcription_diarization, consolidate_diarized_transcript
    from backend.utils.audio import get_audio_duration, convert_to_mp3, convert_to_proxy_mp3
    from backend.processing.llm_services import get_llm_backend
    import torch

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

@celery_app.task(base=DatabaseTask, bind=True, autoretry_for=(ConnectionError, urllib.error.URLError, requests.exceptions.RequestException), retry_backoff=True, max_retries=3)
def process_recording_task(self, recording_id: int):
    """
    Full processing pipeline: VAD -> Transcribe -> Diarize -> Save
    """
    from backend.processing.vad import mute_non_speech_segments
    from backend.processing.audio_preprocessing import convert_wav_to_mp3, preprocess_audio_for_vad, validate_audio_file, cleanup_temp_file, repair_audio_file
    from backend.processing.transcribe import transcribe_audio
    from backend.processing.diarize import diarize_audio
    from backend.processing.embedding_core import extract_embeddings
    from backend.processing.embedding import cosine_similarity, merge_embeddings
    from backend.utils.transcript_utils import combine_transcription_diarization, consolidate_diarized_transcript
    from backend.utils.audio import get_audio_duration, convert_to_mp3, convert_to_proxy_mp3
    from backend.processing.llm_services import get_llm_backend

    config_manager.reload()
    
    start_time = time.time()
    session = self.session
    temp_files = []
    
    recording = session.get(Recording, recording_id)
    if not recording:
        logger.error(f"Recording {recording_id} not found.")
        return
    
    # Check if cancelled
    if recording.status == RecordingStatus.CANCELLED:
         logger.info(f"Recording {recording_id} was cancelled. Aborting task.")
         return

    user_settings = {}
    if recording.user_id:
        user = session.get(User, recording.user_id)
        if user and user.settings:
            user_settings = user.settings
            logger.info(f"Loaded settings for user {user.username}: {list(user_settings.keys())}")
            
    system_config = config_manager.get_all()
    merged_config = system_config.copy()
    merged_config.update(user_settings)
    
    # Platform/Device detection for UX
    import torch
    device_type = "cpu"
    if config_manager.get("use_gpu", True) and torch.cuda.is_available():
        device_type = "cuda"
    
    # "Gentle" warning suffix
    device_suffix = " (GPU)" if device_type == "cuda" else " (CPU, may take a while)"

    try:
        recording.status = RecordingStatus.PROCESSING
        recording.processing_progress = 20
        session.add(recording)
        session.commit()
        session.refresh(recording)
        
        audio_path = recording.audio_path
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        try:
            validate_audio_file(audio_path)
        except AudioFormatError as e:
            logger.warning(f"Invalid audio file detected: {e}. Attempting repair...")
            repaired_path = repair_audio_file(audio_path)
            
            if repaired_path:
                logger.info(f"Using repaired audio file: {repaired_path}")
                audio_path = repaired_path
                temp_files.append(repaired_path) # Ensure cleanup
            else:
                logger.error(f"Audio repair failed for {audio_path}")
                recording.status = RecordingStatus.ERROR
                recording.processing_step = f"Invalid audio (Repair failed): {str(e)}"
                session.add(recording)
                session.commit()
                return

        # Fix missing duration if needed
        if (not recording.duration_seconds or recording.duration_seconds == 0):
            try:
                duration = get_audio_duration(audio_path)
                recording.duration_seconds = duration
                session.add(recording)
                session.commit()
                session.refresh(recording)
            except Exception as e:
                logger.warning(f"Could not determine duration for recording {recording_id}: {e}")
    
        # --- VAD Stage ---
        enable_vad = merged_config.get("enable_vad", True)
        
        if enable_vad:
            self.update_state(state='PROCESSING', meta={'progress': 30, 'stage': 'VAD'})
            recording.processing_step = f"Filtering silence and noise...{device_suffix}"
            recording.processing_progress = 30
            session.add(recording)
            session.commit()
            
            # Preprocess for VAD (resample to 16k mono)
            vad_input_path = preprocess_audio_for_vad(audio_path)
            if not vad_input_path:
                raise RuntimeError("VAD preprocessing failed")
            temp_files.append(vad_input_path)
                
            # Run VAD (mute silence)
            vad_output_path = vad_input_path.replace("_vad.wav", "_vad_processed.wav")
            vad_success, speech_duration = mute_non_speech_segments(vad_input_path, vad_output_path)
            
            if not vad_success:
                 raise RuntimeError("VAD execution failed")
            temp_files.append(vad_output_path)

            # Check for silence
            if speech_duration < 1.0:
                logger.warning(f"No speech detected in recording {recording_id} (speech duration: {speech_duration}s)")
                recording.status = RecordingStatus.PROCESSED
                recording.processing_step = "Completed (No speech detected)"
                
                # Create empty transcript
                transcript = session.exec(select(Transcript).where(Transcript.recording_id == recording.id)).first()
                if not transcript:
                    transcript = Transcript(recording_id=recording.id)
                
                transcript.text = ""  # Empty string to prevent hallucinations
                transcript.segments = []
                transcript.transcript_status = "completed"
                
                session.add(transcript)
                session.add(recording)
                session.commit()
                return

            # Use WAV for processing to avoid sample count mismatches in Pyannote
            processed_audio_path = vad_output_path
        else:
            logger.info("VAD disabled, skipping silence filtering.")
            # Still need to preprocess to ensure 16k mono wav for Whisper/Pyannote
            vad_input_path = preprocess_audio_for_vad(audio_path)
            if not vad_input_path:
                raise RuntimeError("Audio preprocessing failed")
            temp_files.append(vad_input_path)
            processed_audio_path = vad_input_path
            pass

        logger.info(f"Using processed audio for transcription/diarization: {processed_audio_path}")
        if not os.path.exists(processed_audio_path):
             raise FileNotFoundError(f"Processed audio file missing: {processed_audio_path}")
        
        # --- Transcription Stage ---
        self.update_state(state='PROCESSING', meta={'progress': 50, 'stage': 'Transcription'})
        recording.processing_step = f"Transcribing audio...{device_suffix}"
        recording.processing_progress = 50
        session.add(recording)
        session.commit()
        
        # Run Whisper
        transcription_result = transcribe_audio(processed_audio_path, config=merged_config)
        
        # --- Diarization Stage ---
        enable_diarization = merged_config.get("enable_diarization", True)
        diarization_result = None
        
        if enable_diarization:
            self.update_state(state='PROCESSING', meta={'progress': 70, 'stage': 'Diarization'})
            recording.processing_step = f"Determining who said what...{device_suffix}"
            recording.processing_progress = 70
            session.add(recording)
            session.commit()
            
            # Run Pyannote
            diarization_result = diarize_audio(processed_audio_path, config=merged_config)
            
            if diarization_result is None:
                 msg = "Diarization failed (check HF token), falling back to single speaker."
                 logger.warning(msg)
                 recording.processing_step = msg
                 session.add(recording)
                 session.commit()
        else:
            logger.info("Diarization disabled, skipping speaker separation.")
        
        # --- Merge & Save ---
        self.update_state(state='PROCESSING', meta={'progress': 85, 'stage': 'Saving'})
        recording.processing_step = f"Saving transcript...{device_suffix}"
        recording.processing_progress = 85
        session.add(recording)
        session.commit()
        
        # Combine Transcription and Diarization
        combined_segments = []
        if transcription_result:
            # Only attempt combination if we have both results
            if diarization_result:
                combined_segments = combine_transcription_diarization(transcription_result, diarization_result)
            else:
                logger.info("Diarization result missing or disabled. Skipping combination.")
        
        logger.info(f"Combined segments count: {len(combined_segments) if combined_segments else 0}")
        
        if not combined_segments:
            # Fallback if combination fails or was skipped
            if enable_diarization and diarization_result:
                 logger.warning("Combination failed despite having diarization result. Using raw transcription segments with UNKNOWN speaker.")
            else:
                 logger.info("Using raw transcription segments (Diarization disabled or failed).")
            
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
        
        # --- LLM Speaker Name Inference (First Pass) ---
        inferred_mapping = {}
        
        # Check availability using merged_config (user settings + system config)
        llm_provider = merged_config.get("llm_provider", "gemini")
        llm_api_key = merged_config.get(f"{llm_provider}_api_key")
        llm_model = merged_config.get(f"{llm_provider}_model")
        auto_infer_speakers = merged_config.get("auto_infer_speakers", True)
        
        if llm_api_key and llm_model and auto_infer_speakers:
            try:
                self.update_state(state='PROCESSING', meta={'progress': 88, 'stage': 'Inferring Speakers'})
                recording.processing_step = f"Inferring speaker names...{device_suffix}"
                recording.processing_progress = 88
                session.add(recording)
                session.commit()
                logger.info("Running LLM speaker inference...")
                
                # Prepare transcript for LLM
                transcript_for_llm = ""
                for entry in final_segments:
                    start = entry['start']
                    end = entry['end']
                    def fmt(ts):
                        h = int(ts // 3600)
                        m = int((ts % 3600) // 60)
                        s = ts % 60
                        return f"{h:02}.{m:02}.{s:05.2f}s"
                    diarization_label = entry['speaker']
                    transcript_for_llm += f"[{fmt(start)} - {fmt(end)}] - {diarization_label} - {entry['text']}\n"
                
                # Get backend and run inference
                backend = get_llm_backend(llm_provider, api_key=llm_api_key, model=llm_model)
                inferred_mapping = backend.infer_speakers(transcript_for_llm, timeout=300)
                logger.info(f"LLM Inferred Mapping: {inferred_mapping}")
                
            except Exception as e:
                logger.error(f"LLM speaker inference failed: {e}")
        else:
            if not auto_infer_speakers:
                logger.info("Skipping speaker inference (auto_infer_speakers=False)")
            else:
                logger.info("LLM not available (missing key or model in merged config), skipping speaker inference.")

        # Create or Update Transcript Record
        transcript = session.exec(select(Transcript).where(Transcript.recording_id == recording.id)).first()
        
        # Handle case where transcription_result is None (e.g. due to error)
        full_text = transcription_result.get('text', '') if transcription_result else ''
        
        if transcript:
            transcript.text = full_text
            transcript.segments = final_segments
            transcript.transcript_status = "completed"
            session.add(transcript)
        else:
            transcript = Transcript(
                recording_id=recording.id,
                text=full_text,
                segments=final_segments,
                transcript_status="completed"
            )
            session.add(transcript)
        
        session.commit()
        # update_recording_status(session, recording.id) # Removed to prevent premature status update (flash)
        
        # Save Speakers & Embeddings
        # Extract unique speakers from the final segments
        # Processes speakers in order of appearance to assign "Speaker 1", "Speaker 2", etc.
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
            self.update_state(state='PROCESSING', meta={'progress': 90, 'stage': 'Voiceprints'})
            recording.processing_step = f"Learning voiceprints...{device_suffix}"
            recording.processing_progress = 90
            session.add(recording)
            session.commit()
            logger.info("Extracting speaker voiceprints (enable_auto_voiceprints=True)")
            speaker_embeddings = extract_embeddings(processed_audio_path, diarization_result, device_str=merged_config.get("processing_device", "cpu"), config=merged_config)
        elif not enable_auto_voiceprints:
            logger.info("Skipping voiceprint extraction (enable_auto_voiceprints=False)")
        
        # Map local labels (SPEAKER_00) to resolved names (John Doe or Speaker 1)
        label_map = {} 
        speaker_counter = 1
        
        # Track which names have been assigned to which speaker ID/Label to detect duplicates
        # Format: name -> {'id': recording_speaker_id, 'label': diarization_label}
        resolved_names_map = {}
        
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
            
            # --- LOGIC UPDATE: Check for Manual Names & Merges ---
            if existing_speaker:
                # 1. Check if this speaker was merged into another
                if existing_speaker.merged_into_id:
                    logger.info(f"Speaker {label} is merged. Resolving target...")
                    current_spk = existing_speaker
                    visited_ids = {current_spk.id}
                    
                    # Follow the merge chain (prevent infinite loops)
                    while current_spk.merged_into_id:
                        next_spk = session.get(RecordingSpeaker, current_spk.merged_into_id)
                        if not next_spk:
                            logger.warning(f"Merge chain broken for speaker {label} at ID {current_spk.merged_into_id}")
                            break
                        if next_spk.id in visited_ids:
                            logger.warning(f"Circular merge detected for speaker {label}")
                            break
                        visited_ids.add(next_spk.id)
                        current_spk = next_spk
                    
                    # Use the target speaker's name
                    resolved_name = current_spk.name or current_spk.local_name or current_spk.diarization_label
                    logger.info(f"Resolved {label} (Merged) -> {resolved_name}")
                    if current_spk.global_speaker_id:
                        global_speaker_id = current_spk.global_speaker_id
                        is_identified = True # Don't re-identify
                    else:
                        # It's a local merge, so we trust the local name
                        is_identified = True 
                
                # 2. Check for manual rename (if not merged)
                elif existing_speaker.local_name:
                    resolved_name = existing_speaker.local_name
                    logger.info(f"Preserving manual name for {label}: {existing_speaker.local_name}")
                    is_identified = True # Skip inference
                    
                    if existing_speaker.global_speaker_id:
                         global_speaker_id = existing_speaker.global_speaker_id

            # Try to identify speaker using embedding (ONLY if not manually named/merged)
            if not is_identified and embedding:
                # Fetch all global speakers with embeddings belonging to this user
                # Filter out any potential placeholder names from the global list to prevent bad linking
                all_global_speakers = session.exec(
                    select(GlobalSpeaker)
                    .where(GlobalSpeaker.embedding != None)
                    .where(GlobalSpeaker.user_id == recording.user_id)
                ).all()
                
                import re
                placeholder_pattern = re.compile(r"^(SPEAKER_\d+|Speaker \d+|Unknown)$", re.IGNORECASE)
                
                global_speakers = [
                    gs for gs in all_global_speakers 
                    if not placeholder_pattern.match(gs.name) and gs.embedding and len(gs.embedding) > 0 and not any(x is None for x in gs.embedding)
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
                    if not best_match.is_voiceprint_locked:
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
                # Check if we have an inferred name from LLM
                if label in inferred_mapping:
                    resolved_name = inferred_mapping[label]
                    logger.info(f"Using inferred name for {label}: {resolved_name}")
                else:
                    resolved_name = f"Speaker {speaker_counter}"
                    speaker_counter += 1

            # Auto-promotion logic removed. Speakers must be manually promoted.

            # Auto-merge duplicate name detection: if this resolved name was already
            # assigned to a previous speaker in this loop, merge into the existing one.
            if resolved_name and resolved_name in resolved_names_map:
                target_info = resolved_names_map[resolved_name]
                target_label = target_info['label']
                target_id = target_info['id']
                
                if target_label != label:
                    logger.info(f"Auto-Merge: '{resolved_name}' already assigned to {target_label}. Merging {label} into {target_label}.")
                    
                    if existing_speaker:
                        existing_speaker.merged_into_id = target_id
                        existing_speaker.name = resolved_name # Keep consistent name
                        existing_speaker.local_name = None 
                        session.add(existing_speaker)
                        session.flush() # Ensure it's saved
                    else:
                        # Create the record but immediately merge it
                        rec_speaker = RecordingSpeaker(
                            recording_id=recording.id,
                            diarization_label=label,
                            name=resolved_name,
                            embedding=embedding,
                            global_speaker_id=global_speaker_id,
                            merged_into_id=target_id
                        )
                        session.add(rec_speaker)
                        session.flush() 
                    
                    # rewrite segments in memory to point to the target label
                    # This ensures the transcript assumes they are the same speaker
                    for seg in final_segments:
                        if seg['speaker'] == label:
                            seg['speaker'] = target_label
                            
                    # No addition to resolved_names_map needed; the canonical entry already exists.
                    label_map[label] = resolved_name
                    continue


            label_map[label] = resolved_name
            logger.info(f"Mapped {label} -> {resolved_name}")

            current_speaker_id = None
            if existing_speaker:
                existing_speaker.embedding = embedding
                existing_speaker.name = resolved_name
                existing_speaker.global_speaker_id = global_speaker_id
                session.add(existing_speaker)
                session.flush()
                current_speaker_id = existing_speaker.id
            else:
                rec_speaker = RecordingSpeaker(
                    recording_id=recording.id,
                    diarization_label=label,
                    name=resolved_name,
                    embedding=embedding,
                    global_speaker_id=global_speaker_id
                )
                session.add(rec_speaker)
                session.flush()
                current_speaker_id = rec_speaker.id
            
            # Register this name as taken
            if resolved_name and current_speaker_id:
                resolved_names_map[resolved_name] = {'id': current_speaker_id, 'label': label}
        
        # Keep the diarization_label in the segments to maintain the link to RecordingSpeaker
        # The frontend will resolve the display name using the speaker map
        updated_segments = []
        for seg in final_segments:
            updated_segments.append(seg)
        
        # Log final speaker distribution in updated segments
        final_speaker_counts = {}
        for seg in updated_segments:
            spk = seg['speaker']
            final_speaker_counts[spk] = final_speaker_counts.get(spk, 0) + 1
        logger.info(f"Final transcript speaker distribution: {final_speaker_counts}")
            
        transcript.segments = updated_segments
        session.add(transcript)

        # Construct transcript text with resolved names for LLM usage
        transcript_text = ""
        for seg in updated_segments:
            seg_start_time = time.strftime('%H:%M:%S', time.gmtime(seg['start']))
            seg_end_time = time.strftime('%H:%M:%S', time.gmtime(seg['end']))
            speaker_label = seg['speaker']
            speaker_name = label_map.get(speaker_label, speaker_label)
            transcript_text += f"[{seg_start_time} - {seg_end_time}] {speaker_name}: {seg['text']}\n"

        # Auto-generate Meeting Title
        auto_generate_title = merged_config.get("auto_generate_title", True)
        if auto_generate_title:
            try:
                self.update_state(state='PROCESSING', meta={'progress': 94, 'stage': 'Inferring Title'})
                recording.processing_step = f"Inferring meeting title...{device_suffix}"
                recording.processing_progress = 94
                session.add(recording)
                session.commit()

                provider = merged_config.get("llm_provider", "gemini")
                api_key = merged_config.get(f"{provider}_api_key")
                model = merged_config.get(f"{provider}_model")
                prefer_short_titles = merged_config.get("prefer_short_titles", True)

                if api_key:
                    llm = get_llm_backend(provider, api_key=api_key, model=model)
                    
                    # Construct prompt based on preference
                    if prefer_short_titles:
                        prompt_template = (
                            "You are an expert meeting assistant. Given the full meeting transcript below, "
                            "provide a very short, punchy title (3-5 words) that captures the core essence of the meeting. "
                            "Output ONLY the title with no additional commentary, punctuation, or formatting.\n\n"
                            "# Transcript\n\n{transcript}\n"
                        )
                    else:
                        # Use default prompt (longer/descriptive)
                        prompt_template = None 

                    title = llm.infer_meeting_title(transcript_text, prompt_template=prompt_template)
                    recording.name = title
                    session.add(recording)
                    session.commit()
                    logger.info(f"Inferred title for recording {recording_id}: {title}")
                else:
                    logger.warning(f"Skipping title inference: No API key for {provider}")

            except Exception as e:
                logger.error(f"Failed to infer meeting title: {e}")
                # Don't fail the whole process

        # Auto-generate Meeting Notes
        auto_generate_notes = merged_config.get("auto_generate_notes", True)
        if auto_generate_notes:
            try:
                self.update_state(state='PROCESSING', meta={'progress': 97, 'stage': 'Generating Notes'})
                recording.processing_step = f"Generating meeting notes...{device_suffix}"
                recording.processing_progress = 97
                session.add(recording)
                session.commit()
                
                provider = merged_config.get("llm_provider", "gemini")
                api_key = merged_config.get(f"{provider}_api_key")
                model = merged_config.get(f"{provider}_model")
                
                if api_key:
                    transcript.notes_status = "generating"
                    session.add(transcript)
                    session.commit()
                    update_recording_status(session, recording.id)
                    
                    llm = get_llm_backend(provider, api_key=api_key, model=model)
                    # Passes an empty mapping because names are already resolved in transcript_text.
                    # Use a generous timeout (300s) for meeting notes generation as it can be slow
                    notes = llm.generate_meeting_notes(transcript_text, {}, timeout=300)
                    transcript.notes = notes
                    transcript.notes_status = "completed"
                    session.add(transcript)
                    session.commit()
                    update_recording_status(session, recording.id)
                    logger.info(f"Generated meeting notes for recording {recording_id}")
                else:
                    logger.warning(f"Skipping note generation: No API key for {provider}")
                    transcript.notes_status = "error" # Or pending?
                    session.add(transcript)

            except Exception as e:
                logger.error(f"Failed to generate meeting notes: {e}")
                transcript.notes_status = "error"
                session.add(transcript)
                # Don't fail the whole process

        # Update Recording Status
        recording.processing_step = "Completed"
        recording.processing_progress = 100
        session.add(recording)
        session.commit()
        update_recording_status(session, recording.id)
        
        elapsed_time = time.time() - float(start_time)
        logger.info(f"Recording: [{recording_id}] processing succeeded in {elapsed_time:.2f} seconds")
        
        # Trigger Transcript Indexing for RAG
        # Triggers transcript indexing after all data is committed.
        from backend.worker.tasks import index_transcript_task
        index_transcript_task.delay(recording_id)
        
        return {"status": "success", "recording_id": recording_id}

    except AudioProcessingError as e:
        logger.error(f"Audio processing error for {recording_id}: {e}", exc_info=True)
        recording = session.get(Recording, recording_id)
        if recording:
            recording.status = RecordingStatus.ERROR
            recording.processing_step = f"Error: {str(e)}"
            session.add(recording)
            session.commit()
            update_recording_status(session, recording.id)
            
    except Exception as e:
        logger.error(f"Processing failed for {recording_id}: {e}", exc_info=True)
        recording = session.get(Recording, recording_id)
        if recording:
            recording.status = RecordingStatus.ERROR
            recording.processing_step = f"System Error: {str(e)}"
            session.add(recording)
            session.commit()
            update_recording_status(session, recording.id)
            
    finally:
        # Robust cleanup of all temporary files
        for temp_file in temp_files:
            cleanup_temp_file(temp_file)
            
        # --- VRAM Management ---
        # Explicitly release models if configured to do so (default behavior for shared hosts)
        keep_loaded = config_manager.get("keep_models_loaded", False)
        
        if not keep_loaded:
            try:
                logger.info("Releasing VRAM (keep_models_loaded=False)...")
                
                # 1. Whisper
                from backend.processing.transcribe import release_model_cache
                release_model_cache()
                
                # 2. Pyannote
                from backend.processing.diarize import release_pipeline_cache
                release_pipeline_cache()
                
                # 3. Text Embeddings
                from backend.processing.text_embedding import release_embedding_model
                release_embedding_model()
                
                # 4. Garbage Collection
                import gc
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                    
                logger.info("VRAM released successfully.")
            except Exception as e:
                logger.error(f"Error releasing VRAM: {e}")

@celery_app.task(base=DatabaseTask, bind=True)
def update_speaker_embedding_task(self, recording_id: int, start: float, end: float, recording_speaker_id: int):
    """
    Update the speaker embedding for a specific segment (Active Learning).
    """
    from backend.processing.embedding_core import extract_embedding_for_segments
    from backend.processing.embedding import merge_embeddings
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
        # Passes a list of segments [(start, end)] for embedding extraction.
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
def extract_embedding_task(self, audio_path: str, segments: list, device_str: str = "cpu", hf_token: str = None):
    """
    Extract embedding from segments. Used by API for synchronous-like operations.
    """
    from backend.processing.embedding_core import extract_embedding_for_segments
    try:
        # If token not passed, try to get from config in worker
        if not hf_token:
            from backend.utils.config_manager import config_manager
            hf_token = config_manager.get("hf_token")
            
        return extract_embedding_for_segments(audio_path, segments, device_str, hf_token)
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
            process_recording_task.delay(recording.id) # type: ignore
            
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

@celery_app.task(bind=True)
def download_models_task(self, hf_token: str | None = None, whisper_model_size: str | None = None):
    """
    Task to download models in the background.
    Checks for existing downloads from preload_models.py and forwards that progress.
    """
    from backend.preload_models import download_models, check_model_status
    from backend.utils.download_progress import (
        get_download_progress,
        is_download_in_progress,
        is_download_complete,
        set_download_progress
    )
    
    # Reload config to ensure we have the latest settings
    config_manager.reload()
    
    # Check if models are already fully downloaded
    model_status = check_model_status(whisper_model_size=whisper_model_size or "turbo")
    all_downloaded = (
        model_status.get("whisper", {}).get("downloaded", False) and
        model_status.get("pyannote", {}).get("downloaded", False) and
        model_status.get("embedding", {}).get("downloaded", False)
    )
    
    if all_downloaded:
        logger.info("All models already downloaded, skipping download.")
        self.update_state(state='PROCESSING', meta={
            'progress': 100,
            'message': 'All models already downloaded!'
        })
        set_download_progress(100, "All models already downloaded!", status="complete")
        return {"status": "success", "message": "All models already downloaded."}
    
    # Check if there's an active download from preload_models.py
    # If so, poll and forward that progress until it completes
    if is_download_in_progress():
        logger.info("Download already in progress (from preload_models.py), forwarding progress...")
        while True:
            progress = get_download_progress()
            if progress is None:
                break
            
            status = progress.get("status", "downloading")
            if status == "complete":
                self.update_state(state='PROCESSING', meta={
                    'progress': 100,
                    'message': 'All models downloaded!'
                })
                return {"status": "success", "message": "All models downloaded successfully."}
            elif status == "error":
                error_msg = progress.get("message", "Download failed")
                raise Exception(error_msg)
            else:
                # Forward the progress to the Celery task state
                meta = {
                    'progress': progress.get('progress', 0),
                    'message': progress.get('message', 'Downloading...'),
                    'stage': progress.get('stage')
                }
                if progress.get('speed'):
                    meta['speed'] = progress['speed']
                if progress.get('eta'):
                    meta['eta'] = progress['eta']
                self.update_state(state='PROCESSING', meta=meta)
            
            time.sleep(1)
    
    # No active download, proceed with our own download
    def progress_callback(msg, percent, speed=None, eta=None, stage=None):
        meta = {'progress': percent, 'message': msg, 'stage': stage}
        if speed:
            meta['speed'] = speed
        if eta:
            meta['eta'] = eta
        self.update_state(state='PROCESSING', meta=meta)
    
    try:
        download_models(progress_callback=progress_callback, hf_token=hf_token, whisper_model_size=whisper_model_size)
        return {"status": "success", "message": "All models downloaded successfully."}
    except Exception as e:
        logger.error(f"Model download failed: {e}", exc_info=True)
        raise e

@celery_app.task(base=DatabaseTask, bind=True)
def generate_notes_task(self, recording_id: int):
    """
    Generate meeting notes for a recording.
    """
    from backend.processing.llm_services import get_llm_backend
    
    session = self.session
    recording = None
    try:
        recording = session.get(Recording, recording_id)
        if not recording:
            logger.error(f"Recording {recording_id} not found.")
            return

        transcript = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
        if not transcript:
            logger.error(f"Transcript for recording {recording_id} not found.")
            return

        # Update status
        transcript.notes_status = "generating"
        recording.processing_step = "Generating meeting notes..."
        session.add(transcript)
        session.add(recording)
        session.commit()
        update_recording_status(session, recording.id)

        # Get User Settings
        user_settings = {}
        if recording.user_id:
            user = session.get(User, recording.user_id)
            if user and user.settings:
                user_settings = user.settings
        
        system_config = config_manager.get_all()
        merged_config = system_config.copy()
        merged_config.update(user_settings)

        provider = merged_config.get("llm_provider", "gemini")
        api_key = merged_config.get(f"{provider}_api_key")
        # Fix: Use provider-specific model key (e.g. gemini_model) instead of generic llm_model
        model = merged_config.get(f"{provider}_model")

        if not api_key and provider != "ollama":
            logger.warning(f"No API key configured for {provider}. Cannot generate notes.")
            transcript.notes_status = "error"
            transcript.error_message = f"No API key configured for {provider}"
            session.add(transcript)
            session.commit()
            return
            
        if not model:
            logger.warning(f"No model selected for {provider}. Cannot generate notes.")
            transcript.notes_status = "error"
            transcript.error_message = f"No model selected for {provider}"
            session.add(transcript)
            session.commit()
            return

        # Build Speaker Map and Transcript Text
        speakers = session.exec(select(RecordingSpeaker).where(RecordingSpeaker.recording_id == recording_id)).all()
        speaker_map = {s.diarization_label: s.name for s in speakers}

        # Render transcript text for LLM
        lines = []
        for seg in transcript.segments:
            speaker_label = seg.get('speaker', 'Unknown')
            speaker_name = speaker_map.get(speaker_label, speaker_label)
            text = seg.get('text', '')
            lines.append(f"{speaker_name}: {text}")
        transcript_text = "\n".join(lines)

        # Call LLM Service
        llm = get_llm_backend(provider, api_key=api_key, model=model)
        notes = llm.generate_meeting_notes(transcript_text, speaker_map)

        # Save Notes
        transcript.notes = notes
        transcript.notes_status = "completed"
        recording.processing_step = "Completed"
        session.add(transcript)
        session.add(recording)
        session.commit()
        update_recording_status(session, recording.id)
        logger.info(f"Generated meeting notes for recording {recording_id}")

        # --- Index Notes for RAG ---
        try:
            # Clean up existing note chunks
            existing_chunks = session.exec(
                select(ContextChunk)
                .where(ContextChunk.recording_id == recording_id)
                .where(ContextChunk.document_id == None)
            ).all()
            
            for chunk in existing_chunks:
                if chunk.meta and chunk.meta.get('source') == 'notes':
                    session.delete(chunk)
            
            # Chunking
            from backend.processing.text_embedding import get_text_embedding_service
            
            note_chunks = []
            CHUNK_SIZE = 1000
            OVERLAP = 100
            
            if notes:
                start = 0
                while start < len(notes):
                    end = start + CHUNK_SIZE
                    note_chunks.append(notes[start:end])
                    start += (CHUNK_SIZE - OVERLAP)
            
            if note_chunks:
                embedding_service = get_text_embedding_service()
                vectors = embedding_service.embed(note_chunks)
                
                for i, (text_chunk, vector) in enumerate(zip(note_chunks, vectors)):
                    db_chunk = ContextChunk(
                        recording_id=recording_id,
                        content=text_chunk,
                        embedding=vector,
                        meta={"chunk_index": i, "source": "notes"}
                    )
                    session.add(db_chunk)
                session.commit()
                logger.info(f"Indexed {len(note_chunks)} note chunks for recording {recording_id}")

        except Exception as e:
            logger.error(f"Failed to index meeting notes for RAG: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"Failed to generate meeting notes: {e}", exc_info=True)
        transcript = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
        if transcript:
            transcript.notes_status = "error"
            transcript.error_message = str(e)
            session.add(transcript)
        
        if recording:
            recording.processing_step = "Error generating notes"
            session.add(recording)
            
        session.commit()
        if recording:
            update_recording_status(session, recording_id)

@celery_app.task(base=DatabaseTask, bind=True)
def infer_speakers_task(self, recording_id: int):
    """
    Independent task to re-run speaker inference using LLM.
    """
    from backend.processing.llm_services import get_llm_backend
    # Reload config
    config_manager.reload()
    
    session = self.session
    try:
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
        
        system_config = config_manager.get_all()
        merged_config = system_config.copy()
        merged_config.update(user_settings)

        provider = merged_config.get("llm_provider", "gemini")
        api_key = merged_config.get(f"{provider}_api_key")
        model = merged_config.get(f"{provider}_model")

        if not api_key and provider != "ollama":
            logger.warning(f"No API key configured for {provider}. Skipping inference.")
            return

        # Fetch transcript
        transcript = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
        if not transcript or not transcript.segments:
            logger.error(f"No transcript found for recording {recording_id}.")
            return

        # Update status (optional, but good for UI feedback if we had a specific status for this)
        # For now, we just log it.
        logger.info(f"Starting independent speaker inference for recording {recording_id}")

        # Prepare transcript for LLM
        transcript_for_llm = ""
        for seg in transcript.segments:
            start = seg.get('start', 0)
            end = seg.get('end', 0)
            def fmt(ts):
                h = int(ts // 3600)
                m = int((ts % 3600) // 60)
                s = ts % 60
                return f"{h:02}.{m:02}.{s:05.2f}s"
            
            diarization_label = seg.get('speaker', 'Unknown')
            text = seg.get('text', '')
            transcript_for_llm += f"[{fmt(start)} - {fmt(end)}] - {diarization_label} - {text}\n"

        # Run inference
        backend = get_llm_backend(provider, api_key=api_key, model=model)
        inferred_mapping = backend.infer_speakers(transcript_for_llm)
        logger.info(f"LLM Inferred Mapping: {inferred_mapping}")

        # Update speakers in DB
        speakers = session.exec(select(RecordingSpeaker).where(RecordingSpeaker.recording_id == recording_id)).all()
        
        updated_count = 0
        for s in speakers:
            label = s.diarization_label
            inferred_name = inferred_mapping.get(label)
            if inferred_name and inferred_name != s.name:
                s.name = inferred_name
                session.add(s)
                updated_count += 1
        
        session.commit()
        logger.info(f"Updated {updated_count} speakers for recording {recording_id}")

        # Update status back to PROCESSED
        recording.status = RecordingStatus.PROCESSED
        recording.processing_step = "Completed"
        session.add(recording)
        session.commit()

    except Exception as e:
        logger.error(f"Speaker inference task failed: {e}", exc_info=True)
        # Revert status to PROCESSED on error so spinner stops
        try:
            recording = session.get(Recording, recording_id)
            if recording:
                recording.status = RecordingStatus.PROCESSED
                session.add(recording)
                session.commit()
        except Exception as db_err:
            logger.error(f"Failed to revert recording status: {db_err}")

@celery_app.task
def cleanup_temp_recordings():
    """
    Periodic task to clean up old temporary files and failed uploads.
    Runs every 24 hours.
    """
    logger.info("Starting cleanup of temp recordings...")
    
    # Define paths (matching api/v1/endpoints/recordings.py)
    recordings_dir = os.getenv("RECORDINGS_DIR", "data/recordings")
    temp_dir = os.path.join(recordings_dir, "temp")
    failed_dir = os.path.join(recordings_dir, "failed")
    
    # 24 hours ago
    cutoff_time = time.time() - (24 * 60 * 60)
    
    cleaned_count = 0
    
    # Clean temp dir
    if os.path.exists(temp_dir):
        for item in os.listdir(temp_dir):
            item_path = os.path.join(temp_dir, item)
            try:
                # Check modification time
                if os.path.getmtime(item_path) < cutoff_time:
                    if os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                    else:
                        os.remove(item_path)
                    cleaned_count += 1
                    logger.info(f"Cleaned up old temp item: {item}")
            except Exception as e:
                logger.error(f"Error cleaning temp item {item}: {e}")

    # Clean failed dir
    if os.path.exists(failed_dir):
        for item in os.listdir(failed_dir):
            item_path = os.path.join(failed_dir, item)
            try:
                if os.path.getmtime(item_path) < cutoff_time:
                    if os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                    else:
                        os.remove(item_path)
                    cleaned_count += 1
                    logger.info(f"Cleaned up old failed item: {item}")
            except Exception as e:
                logger.error(f"Error cleaning failed item {item}: {e}")
                
    logger.info(f"Cleanup complete. Removed {cleaned_count} items.")

@celery_app.task(base=DatabaseTask, bind=True)
def generate_proxy_task(self, recording_id: int):
    """
    Generate a lightweight MP3 proxy file for frontend playback.
    """
    from backend.utils.audio import convert_to_proxy_mp3
    session = self.session
    try:
        recording = session.get(Recording, recording_id)
        if not recording:
            logger.error(f"Recording {recording_id} not found for proxy generation")
            return

        if not recording.audio_path or not os.path.exists(recording.audio_path):
            logger.error(f"Audio file not found for recording {recording_id}")
            return

        # Define proxy path (same dir, .mp3 extension)
        base_path, _ = os.path.splitext(recording.audio_path)
        proxy_path = f"{base_path}.mp3"

        logger.info(f"Generating proxy for recording {recording_id} at {proxy_path}")
        
        if convert_to_proxy_mp3(recording.audio_path, proxy_path):
            recording.proxy_path = proxy_path
            session.add(recording)
            session.commit()
            logger.info(f"Proxy generated successfully for recording {recording_id}")
        else:
            logger.error(f"Failed to generate proxy for recording {recording_id}")

    except Exception as e:
        logger.error(f"Error in generate_proxy_task for recording {recording_id}: {e}")
        # Not re-raised because proxy generation is optional/secondary.

@celery_app.task(base=DatabaseTask, bind=True)
def process_document_task(self, document_id: int):
    """
    Process an uploaded document: chunk text, embed, and store context chunks.
    """
    session = self.session
    document = session.get(Document, document_id)
    if not document:
        logger.error(f"Document {document_id} not found.")
        return

    try:
        document.status = DocumentStatus.PROCESSING
        session.add(document)
        session.commit()

        # Read file content
        content = ""
        if document.file_path.endswith(".txt") or document.file_path.endswith(".md"):
            with open(document.file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        elif document.file_path.endswith(".pdf"):
            pass
        
        if not content:
            logger.warning(f"File content empty or unsupported type: {document.file_path}")
            pass

        # Chunking Strategy (Simple overlapping sliding window)
        CHUNK_SIZE = 500 # characters
        OVERLAP = 50
        
        chunks = []
        if content:
            start = 0
            while start < len(content):
                end = start + CHUNK_SIZE
                chunk_text = content[start:end]
                chunks.append(chunk_text)
                start += (CHUNK_SIZE - OVERLAP)
        
        if not chunks:
             logger.warning(f"No chunks generated for document {document_id}")
             document.status = DocumentStatus.READY
             session.add(document)
             session.commit()
             return

        # Embed chunks
        embedding_service = get_text_embedding_service()
        vectors = embedding_service.embed(chunks)
        
        # Store Chunks
        for i, (text_chunk, vector) in enumerate(zip(chunks, vectors)):
            db_chunk = ContextChunk(
                recording_id=document.recording_id,
                document_id=document.id,
                content=text_chunk,
                embedding=vector,
                meta={"chunk_index": i, "source": "document"}
            )
            session.add(db_chunk)
        
        document.status = DocumentStatus.READY
        session.add(document)
        session.commit()
        logger.info(f"Processed document {document_id}: {len(chunks)} chunks created.")

    except Exception as e:
        logger.error(f"Failed to process document {document_id}: {e}", exc_info=True)
        document.status = DocumentStatus.ERROR
        document.error_message = str(e)
        session.add(document)
        session.commit()

@celery_app.task(base=DatabaseTask, bind=True)
def index_transcript_task(self, recording_id: int):
    """
    Index the transcript of a completed recording for RAG.
    """
    session = self.session
    recording = session.get(Recording, recording_id)
    if not recording:
        return

    transcript = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
    if not transcript or not transcript.segments:
        return

    try:
        # Clear existing transcript chunks for this recording
        # The 'source' metadata field identifies these chunks.
        
        # Selects then deletes context chunks.
        existing_chunks = session.exec(
            select(ContextChunk)
            .where(ContextChunk.recording_id == recording_id)
            .where(ContextChunk.document_id == None).where(ContextChunk.meta['source'].as_string() == '"transcript"') 
            # Using document_id == None serves as a proxy for non-document chunks.
        ).all()
        
        for chunk in existing_chunks:
            if chunk.meta.get('source') == 'transcript':
                session.delete(chunk)
        
        # Chunks the transcript segments.
        # Grouping small segments improves embedding quality.
        
        segments = transcript.segments
        
        temp_chunk_text = ""
        temp_chunk_start = 0
        temp_chunk_end = 0
        temp_meta_speakers = set()
        
        chunks_to_embed = []
        metas = []
        
        current_length = 0
        TARGET_LENGTH = 1000 # chars
        
        for seg in segments:
            text = seg['text']
            start = seg['start']
            end = seg['end']
            speaker = seg['speaker']
            
            if current_length == 0:
                temp_chunk_start = start
            
            temp_chunk_text += f"{speaker}: {text}\n"
            current_length += len(text)
            temp_meta_speakers.add(speaker)
            temp_chunk_end = end
            
            if current_length >= TARGET_LENGTH:
                chunks_to_embed.append(temp_chunk_text)
                metas.append({
                    "start": temp_chunk_start,
                    "end": temp_chunk_end,
                    "speakers": list(temp_meta_speakers),
                    "source": "transcript"
                })
                
                # Reset
                temp_chunk_text = ""
                current_length = 0
                temp_meta_speakers = set()
                
        # Add remaining
        if temp_chunk_text:
             chunks_to_embed.append(temp_chunk_text)
             metas.append({
                "start": temp_chunk_start,
                "end": temp_chunk_end,
                "speakers": list(temp_meta_speakers),
                "source": "transcript"
            })
            
        if not chunks_to_embed:
            return

        embedding_service = get_text_embedding_service()
        vectors = embedding_service.embed(chunks_to_embed)
        
        for text, meta, vector in zip(chunks_to_embed, metas, vectors):
            db_chunk = ContextChunk(
                recording_id=recording_id,
                content=text,
                embedding=vector,
                meta=meta
            )
            session.add(db_chunk)
            
        session.commit()
        logger.info(f"Indexed transcript for recording {recording_id}: {len(chunks_to_embed)} chunks.")

    except Exception as e:
        logger.error(f"Failed to index transcript {recording_id}: {e}", exc_info=True)


@celery_app.task(bind=True)
def create_backup_task(self, include_audio: bool = True):
    """
    Background task to create a backup zip file.
    Returns the path to the backup file.
    """
    from backend.core.backup_manager import BackupManager
    import asyncio
    
    try:
        logger.info(f"Starting backup task (include_audio={include_audio})")
        self.update_state(state='PROCESSING', meta={'status': 'Creating backup...'})
        
        # BackupManager.create_backup is async; run it in a new event loop
        # within this synchronous Celery worker process.
        
        zip_path = asyncio.run(BackupManager.create_backup(include_audio=include_audio))
        
        logger.info(f"Backup created successfully at {zip_path}")
        return {"status": "success", "zip_path": zip_path}
        
    except Exception as e:
        logger.error(f"Backup creation failed: {e}", exc_info=True)
        self.update_state(state='FAILURE', meta={'error': str(e)})
        # Re-raises so Celery marks the task as failed.
        raise e
