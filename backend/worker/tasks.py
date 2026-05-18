import os
import shutil
import logging
import time
from datetime import datetime, timedelta
import warnings
import urllib.error
import requests.exceptions

from typing import TYPE_CHECKING, Sequence
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
from backend.utils.config_manager import config_manager
from backend.utils.llm_config import ResolvedLLMConfig, resolve_llm_config
from backend.utils.meeting_intelligence import (
    AutomaticMeetingIntelligenceRequest,
    AutomaticMeetingIntelligenceResult,
    get_speakers_eligible_for_llm_renaming,
)
from backend.utils.meeting_notes import build_recording_speaker_map, format_segments_for_llm
from backend.utils.recording_storage import cleanup_stale_recording_artifacts
from backend.utils.status_manager import update_recording_status
from backend.utils.time import utc_now
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

AUTOMATIC_MEETING_INTELLIGENCE_TIMEOUT_SECONDS = 300
AUTOMATIC_MEETING_INTELLIGENCE_PROGRESS = 97
AUTOMATIC_MEETING_INTELLIGENCE_STAGE = "Generating Notes"
AUTOMATIC_MEETING_INTELLIGENCE_STEP = "Generating meeting notes..."


def _paths_point_to_same_media(path_a: str | None, path_b: str | None) -> bool:
    if not path_a or not path_b:
        return False

    try:
        if os.path.exists(path_a) and os.path.exists(path_b):
            return os.path.samefile(path_a, path_b)
    except OSError:
        pass

    return os.path.normcase(os.path.abspath(path_a)) == os.path.normcase(os.path.abspath(path_b))


def _can_delete_source_audio(recording: Recording) -> bool:
    if not recording.audio_path or not recording.proxy_path:
        return False
    if not os.path.exists(recording.audio_path) or not os.path.exists(recording.proxy_path):
        return False

    return not _paths_point_to_same_media(recording.audio_path, recording.proxy_path)


def _format_notes_generation_error(error: Exception | str) -> str:
    message = str(error).strip() or "Meeting notes could not be generated."
    if len(message) > 500:
        message = f"{message[:497]}..."
    return message


def _mark_notes_generation_error(
    session,
    recording: Recording | None,
    transcript: Transcript | None,
    error: Exception | str,
) -> None:
    if not transcript:
        return

    transcript.notes_status = "error"
    transcript.error_message = _format_notes_generation_error(error)
    session.add(transcript)

    if recording:
        recording.processing_step = "Error generating notes"
        session.add(recording)

    session.commit()

    if recording:
        update_recording_status(session, recording.id)


def _complete_speaker_inference_task(
    session,
    recording: Recording | None,
) -> None:
    if not recording:
        return

    recording.status = RecordingStatus.PROCESSED
    recording.processing_step = "Completed"
    session.add(recording)
    session.commit()


def _llm_backend_from_config(llm_config: ResolvedLLMConfig):
    from backend.processing.llm_services import get_llm_backend

    return get_llm_backend(
        llm_config.provider,
        api_key=llm_config.api_key,
        model=llm_config.model,
        api_url=llm_config.api_url,
    )


def _format_recording_timestamp(seconds: float) -> str:
    return time.strftime("%H:%M:%S", time.gmtime(max(float(seconds), 0.0)))


def _build_automatic_meeting_intelligence_transcript(
    segments: Sequence[dict],
    speaker_map: dict[str, str],
    unresolved_speakers: Sequence[str],
) -> str:
    unresolved_labels = set(unresolved_speakers)
    lines: list[str] = []

    for seg in segments:
        speaker_label = str(seg.get("speaker", "Unknown"))
        display_name = (
            speaker_label
            if speaker_label in unresolved_labels
            else speaker_map.get(speaker_label, speaker_label)
        )

        overlapping_names = []
        for overlapping_label in seg.get("overlapping_speakers", []):
            normalized_label = str(overlapping_label)
            if normalized_label in unresolved_labels:
                overlapping_names.append(normalized_label)
            else:
                overlapping_names.append(
                    speaker_map.get(normalized_label, normalized_label)
                )

        overlapping_suffix = (
            f" (with {', '.join(overlapping_names)})" if overlapping_names else ""
        )
        text = str(seg.get("text", "")).strip()
        lines.append(
            f"[{_format_recording_timestamp(seg.get('start', 0))} - "
            f"{_format_recording_timestamp(seg.get('end', seg.get('start', 0)))}] "
            f"{display_name}{overlapping_suffix}: {text}"
        )

    return "\n".join(lines)


def _apply_automatic_meeting_intelligence_result(
    session,
    recording: Recording,
    transcript: Transcript,
    speakers: Sequence[RecordingSpeaker],
    result: AutomaticMeetingIntelligenceResult,
) -> None:
    speakers_by_label = {speaker.diarization_label: speaker for speaker in speakers}

    for label, inferred_name in result.speaker_mapping.items():
        speaker = speakers_by_label.get(label)
        if speaker is None:
            continue
        if speaker.merged_into_id or speaker.local_name or speaker.global_speaker_id:
            logger.info(
                "Skipping automatic speaker rename for trusted or merged label %s",
                label,
            )
            continue

        if speaker.name != inferred_name:
            speaker.name = inferred_name
            session.add(speaker)

    recording.name = result.title
    transcript.notes = result.notes_markdown
    transcript.notes_status = "completed"
    transcript.error_message = None
    session.add(recording)
    session.add(transcript)
    session.commit()
    update_recording_status(session, recording.id)


def _run_automatic_meeting_intelligence_stage(
    *,
    session,
    task: Task | None,
    recording: Recording,
    transcript: Transcript,
    speakers: Sequence[RecordingSpeaker],
    transcript_text: str,
    unresolved_speakers: Sequence[str],
    llm_config: ResolvedLLMConfig,
    prefer_short_titles: bool,
    device_suffix: str,
) -> AutomaticMeetingIntelligenceResult | None:
    cleaned_transcript = transcript_text.strip()
    if not cleaned_transcript:
        logger.info(
            "Skipping automatic meeting intelligence for recording %s: transcript is empty",
            recording.id,
        )
        return None

    missing_llm_config = llm_config.missing_configuration_message()
    if missing_llm_config:
        logger.warning(
            "Skipping automatic meeting intelligence for recording %s: %s",
            recording.id,
            missing_llm_config,
        )
        return None

    request = AutomaticMeetingIntelligenceRequest(
        resolved_transcript=cleaned_transcript,
        unresolved_speakers=tuple(unresolved_speakers),
        user_notes=transcript.user_notes,
        prefer_short_titles=prefer_short_titles,
    )

    if task is not None:
        task.update_state(
            state="PROCESSING",
            meta={
                "progress": AUTOMATIC_MEETING_INTELLIGENCE_PROGRESS,
                "stage": AUTOMATIC_MEETING_INTELLIGENCE_STAGE,
            },
        )

    recording.processing_step = f"{AUTOMATIC_MEETING_INTELLIGENCE_STEP}{device_suffix}"
    recording.processing_progress = AUTOMATIC_MEETING_INTELLIGENCE_PROGRESS
    transcript.notes_status = "generating"
    transcript.error_message = None
    session.add(recording)
    session.add(transcript)
    session.commit()
    update_recording_status(session, recording.id)

    try:
        llm = _llm_backend_from_config(llm_config)
        result = llm.generate_meeting_intelligence(
            request,
            timeout=AUTOMATIC_MEETING_INTELLIGENCE_TIMEOUT_SECONDS,
        )
        _apply_automatic_meeting_intelligence_result(
            session,
            recording,
            transcript,
            speakers,
            result,
        )
        logger.info(
            "Generated unified meeting intelligence for recording %s",
            recording.id,
        )
        return result
    except Exception as exc:
        logger.error(
            "Failed to generate automatic meeting intelligence for recording %s: %s",
            recording.id,
            exc,
        )
        _mark_notes_generation_error(session, recording, transcript, exc)
        return None

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
def process_recording_task(self, recording_id: int, force_title_regeneration: bool = False, engine_override: dict | None = None):
    """
    Full processing pipeline: VAD -> Transcribe -> Diarize -> Save
    """
    from backend.processing.vad import mute_non_speech_segments
    from backend.processing.audio_preprocessing import convert_wav_to_mp3, preprocess_audio_for_vad, validate_audio_file, cleanup_temp_file, repair_audio_file
    from backend.processing.transcribe import transcribe_audio
    from backend.processing.diarize import diarize_audio
    from backend.processing.embedding_core import extract_embeddings
    from backend.processing.embedding import cosine_similarity, merge_embeddings, find_matching_global_speaker, AUTO_UPDATE_THRESHOLD
    from backend.utils.transcript_utils import combine_transcription_diarization, consolidate_diarized_transcript
    from backend.utils.audio import get_audio_duration, convert_to_mp3, convert_to_proxy_mp3

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
            
    llm_config = resolve_llm_config(session, user_settings)
    merged_config = llm_config.merged_config
    
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
        if recording.processing_started_at is None or recording.processing_completed_at is not None:
            recording.processing_started_at = utc_now()
        recording.processing_completed_at = None
        session.add(recording)
        session.commit()
        session.refresh(recording)
        
        audio_path = recording.audio_path
        if not audio_path or not os.path.exists(audio_path):
            if recording.proxy_path and os.path.exists(recording.proxy_path):
                logger.info("Source audio missing, but proxy exists. Restoring from proxy...")
                from backend.utils.audio import convert_to_wav
                
                if not audio_path:
                    base_path, _ = os.path.splitext(recording.proxy_path)
                    audio_path = f"{base_path}.wav"
                    recording.audio_path = audio_path
                
                recording.processing_step = f"Restoring audio from proxy...{device_suffix}"
                session.add(recording)
                session.commit()
                
                if convert_to_wav(recording.proxy_path, audio_path):
                    logger.info("Successfully restored source audio from proxy.")
                else:
                    raise FileNotFoundError(f"Source audio missing and failed to restore from proxy.")
            else:
                raise FileNotFoundError(f"Audio file not found: {audio_path} and no proxy available.")

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
                recording.processing_completed_at = utc_now()
                
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
        
        # Apply per-reprocess transcription-engine override, if provided.
        if engine_override:
            merged_config.update(engine_override)
            logger.info("Reprocess: engine override applied: %s", engine_override)

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
                # Post-diarization phantom speaker filter
                from backend.processing.phantom_filter import filter_phantom_speakers
                try:
                    diarization_result = filter_phantom_speakers(
                        diarization_result, processed_audio_path, config=merged_config
                    )
                except Exception as e:
                    logger.warning(f"Phantom speaker filter failed, continuing with unfiltered result: {e}")
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
        
        transcript = session.exec(select(Transcript).where(Transcript.recording_id == recording.id)).first()

        # Create or Update Transcript Record
        # Handle case where transcription_result is None (e.g. due to error)
        full_text = transcription_result.get('text', '') if transcription_result else ''
        
        if transcript:
            transcript.text = full_text
            transcript.segments = final_segments
            transcript.transcript_status = "completed"
            transcript.error_message = None
            if transcript.notes_status == "error":
                transcript.notes_status = "pending"
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
        # Processes speakers in order of appearance to assign "Speaker 1", "Speaker 2", etc.
        ordered_speakers = []
        seen_speakers = set()
        for seg in final_segments:
            spk = seg['speaker']
            if spk not in seen_speakers:
                ordered_speakers.append(spk)
                seen_speakers.add(spk)
            for overlapping_spk in seg.get('overlapping_speakers', []):
                if overlapping_spk not in seen_speakers:
                    ordered_speakers.append(overlapping_spk)
                    seen_speakers.add(overlapping_spk)
        
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
                
                # Use centralized matching logic with 0.75 threshold and margin of victory
                best_match, best_score = find_matching_global_speaker(
                    embedding, 
                    global_speakers,
                    threshold=0.75,
                    margin=0.05
                )
                
                if best_match:
                    logger.info(f"Identified {label} as {best_match.name} (Score: {best_score:.2f})")
                    resolved_name = best_match.name
                    global_speaker_id = best_match.id
                    is_identified = True

                    # Active Learning: only update the global embedding when the
                    # match confidence is high enough to avoid polluting it with
                    # borderline or false-positive identifications.
                    if not best_match.is_voiceprint_locked and best_score >= AUTO_UPDATE_THRESHOLD:
                        try:
                            new_emb = merge_embeddings(best_match.embedding, embedding)
                            best_match.embedding = new_emb
                            session.add(best_match)
                        except Exception as e:
                            logger.warning(f"Failed to update embedding for {best_match.name}: {e}")
                    elif not best_match.is_voiceprint_locked:
                        logger.info(
                            f"Skipping auto-update for {best_match.name} "
                            f"(score {best_score:.2f} < auto-update threshold {AUTO_UPDATE_THRESHOLD})"
                        )
                else:
                    logger.info(f"No match found for {label} (Best score: {best_score:.2f}).")

            # If not identified as a global speaker, assign a friendly sequential name
            if not is_identified:
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
                        
                        if 'overlapping_speakers' in seg:
                            for idx, ov_spk in enumerate(seg['overlapping_speakers']):
                                if ov_spk == label:
                                    seg['overlapping_speakers'][idx] = target_label
                            
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
            for ov_spk in seg.get('overlapping_speakers', []):
                final_speaker_counts[ov_spk] = final_speaker_counts.get(ov_spk, 0) + 1
        logger.info(f"Final transcript speaker distribution: {final_speaker_counts}")
            
        transcript.segments = updated_segments
        session.add(transcript)

        recording_speakers = session.exec(
            select(RecordingSpeaker).where(RecordingSpeaker.recording_id == recording.id)
        ).all()
        unresolved_speakers = get_speakers_eligible_for_llm_renaming(recording_speakers)
        transcript_text = _build_automatic_meeting_intelligence_transcript(
            updated_segments,
            label_map,
            unresolved_speakers,
        )

        _run_automatic_meeting_intelligence_stage(
            session=session,
            task=self,
            recording=recording,
            transcript=transcript,
            speakers=recording_speakers,
            transcript_text=transcript_text,
            unresolved_speakers=unresolved_speakers,
            llm_config=llm_config,
            prefer_short_titles=merged_config.get("prefer_short_titles", True),
            device_suffix=device_suffix,
        )

        # Update Recording Status
        recording.processing_step = "Completed"
        recording.processing_progress = 100
        recording.processing_completed_at = utc_now()
        session.add(recording)
        session.commit()
        update_recording_status(session, recording.id)
        
        # Delete source wav if proxy exists to save storage
        session.refresh(recording)
        if _can_delete_source_audio(recording):
            try:
                logger.info(f"Storage optimization: Proxy audio exists, deleting source audio {recording.audio_path}")
                os.remove(recording.audio_path)
            except Exception as e:
                logger.error(f"Failed to delete source audio {recording.audio_path}: {e}")
        
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
            recording.processing_completed_at = None
            session.add(recording)
            session.commit()
            update_recording_status(session, recording.id)
            
    except Exception as e:
        logger.error(f"Processing failed for {recording_id}: {e}", exc_info=True)
        recording = session.get(Recording, recording_id)
        if recording:
            recording.status = RecordingStatus.ERROR
            recording.processing_step = f"System Error: {str(e)}"
            recording.processing_completed_at = None
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
        
        target_audio = recording.audio_path
        if not target_audio or not os.path.exists(target_audio):
            if recording.proxy_path and os.path.exists(recording.proxy_path):
                target_audio = recording.proxy_path
            else:
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
            target_audio, 
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
    session = self.session
    recording = None
    transcript = None
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
        transcript.error_message = None
        recording.processing_step = "Generating meeting notes..."
        recording.processing_progress = 97
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

        llm_config = resolve_llm_config(session, user_settings)
        missing_llm_config = llm_config.missing_configuration_message()
        if missing_llm_config:
            logger.warning("Cannot generate notes: %s", missing_llm_config)
            _mark_notes_generation_error(session, recording, transcript, missing_llm_config)
            return

        if not transcript.segments:
            _mark_notes_generation_error(session, recording, transcript, "Transcript is empty")
            return

        # Build Speaker Map and Transcript Text
        speakers = session.exec(select(RecordingSpeaker).where(RecordingSpeaker.recording_id == recording_id)).all()
        speaker_map = build_recording_speaker_map(speakers)
        transcript_text = format_segments_for_llm(transcript.segments, speaker_map)

        # Call LLM Service
        llm = _llm_backend_from_config(llm_config)
        notes = llm.generate_meeting_notes(
            transcript_text,
            speaker_map,
            timeout=300,
            user_notes=transcript.user_notes,
        )

        # Save Notes
        transcript.notes = notes
        transcript.notes_status = "completed"
        transcript.error_message = None
        recording.processing_step = "Completed"
        recording.processing_progress = 100
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
        session.rollback()
        if transcript is None:
            transcript = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
        _mark_notes_generation_error(session, recording, transcript, e)

@celery_app.task(base=DatabaseTask, bind=True)
def infer_speakers_task(self, recording_id: int):
    """
    Independent task to re-run speaker inference using LLM.
    """
    # Reload config
    config_manager.reload()
    
    session = self.session
    try:
        recording = session.get(Recording, recording_id)
        if not recording:
            logger.error(f"Recording {recording_id} not found.")
            return

        # Fetch user settings for provider resolution.
        user_settings = {}
        if recording.user_id:
            user = session.get(User, recording.user_id)
            if user and user.settings:
                user_settings = user.settings
        llm_config = resolve_llm_config(session, user_settings)
        missing_llm_config = llm_config.missing_configuration_message()
        if missing_llm_config:
            logger.warning(
                "Cannot infer speakers for recording %s: %s",
                recording_id,
                missing_llm_config,
            )
            _complete_speaker_inference_task(session, recording)
            return

        # Fetch transcript
        transcript = session.exec(select(Transcript).where(Transcript.recording_id == recording_id)).first()
        if not transcript or not transcript.segments:
            logger.error(f"No transcript found for recording {recording_id}.")
            _complete_speaker_inference_task(session, recording)
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
        backend = _llm_backend_from_config(llm_config)
        inferred_mapping = backend.infer_speakers(
            transcript_for_llm,
            user_notes=transcript.user_notes,
        )
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

        _complete_speaker_inference_task(session, recording)

    except Exception as e:
        logger.error(f"Speaker inference task failed: {e}", exc_info=True)
        # Revert status to PROCESSED on error so spinner stops
        try:
            recording = session.get(Recording, recording_id)
            _complete_speaker_inference_task(session, recording)
        except Exception as db_err:
            logger.error(f"Failed to revert recording status: {db_err}")

@celery_app.task
def cleanup_temp_recordings():
    """
    Periodic task to clean up old temporary files and failed uploads.
    Runs every 24 hours.
    """
    logger.info("Starting cleanup of temp recordings...")
    
    cleaned_count = cleanup_stale_recording_artifacts(max_age_hours=24, logger=logger)

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

        if _paths_point_to_same_media(recording.audio_path, proxy_path):
            logger.info(
                "Recording %s already uses an MP3 source; reusing it as proxy audio.",
                recording_id,
            )
            recording.proxy_path = recording.audio_path
            session.add(recording)
            session.commit()
            return

        logger.info(f"Generating proxy for recording {recording_id} at {proxy_path}")
        
        if convert_to_proxy_mp3(recording.audio_path, proxy_path):
            recording.proxy_path = proxy_path
            session.add(recording)
            session.commit()
            logger.info(f"Proxy generated successfully for recording {recording_id}")
            
            # If processing is already finished, delete source audio
            if recording.status in [RecordingStatus.PROCESSED, RecordingStatus.ERROR] and _can_delete_source_audio(recording):
                try:
                    logger.info(f"Storage optimization: Proxy generated after processing, deleting source audio {recording.audio_path}")
                    os.remove(recording.audio_path)
                except Exception as e:
                    logger.error(f"Failed to delete source audio {recording.audio_path}: {e}")
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
            import fitz # PyMuPDF
            try:
                doc = fitz.open(document.file_path)
                for page in doc:
                    content += page.get_text() + "\n\n"
            except Exception as e:
                logger.error(f"Failed to extract text from PDF {document.file_path}: {e}")
                raise Exception(f"PDF extraction failed: {str(e)}")
        
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

@celery_app.task(bind=True)
def sync_calendar_connection_task(self, connection_id: int):
    """
    Refresh a single connected calendar account.
    """
    import asyncio

    from backend.services.calendar_service import sync_connection_by_id

    asyncio.run(sync_connection_by_id(connection_id))
    return {"status": "success", "connection_id": connection_id}

@celery_app.task(bind=True)
def sync_calendar_connections_task(self):
    """
    Periodic sync for all selected calendar connections.
    """
    import asyncio

    from backend.services.calendar_service import sync_all_connections

    synced_connections = asyncio.run(sync_all_connections())
    return {"status": "success", "connections_synced": synced_connections}

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
    
    try:
        logger.info(f"Starting backup task (include_audio={include_audio})")
        self.update_state(state='PROCESSING', meta={'status': 'Creating backup...'})
        
        zip_path = BackupManager.create_backup_blocking(include_audio=include_audio)
        
        logger.info(f"Backup created successfully at {zip_path}")
        return {"status": "success", "zip_path": zip_path}
        
    except Exception as e:
        logger.error(f"Backup creation failed: {e}", exc_info=True)
        raise e
