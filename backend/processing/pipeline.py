# nojoin/processing/pipeline.py
# Note: recording_id is always a string in the format YYYYMMDDHHMMSS (see db/schema.py)

import logging
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pyannote.core import Annotation
# All heavy imports are moved inside functions for faster application startup

logger = logging.getLogger(__name__)

def process_recording(recording_id: str, audio_path: str, whisper_progress_callback=None, diarization_progress_callback=None, stage_update_callback=None, stage_callback=None, cancel_check=None):
    """Runs the full transcription and diarization pipeline for a recording, with optional progress callbacks and cancellation."""
    # Local imports for performance: only load heavy dependencies when actually processing
    import os
    import json
    from .transcribe import transcribe_audio, transcribe_audio_with_progress
    from .diarize import diarize_audio, diarize_audio_with_progress
    # from ..db import database # TODO: Replace with new DB layer
    from backend.utils.config_manager import config_manager, from_project_relative_path, to_project_relative_path, is_llm_available
    from .audio_preprocessing import preprocess_audio_for_diarization, cleanup_temp_file, preprocess_audio_for_vad, convert_wav_to_mp3
    from pyannote.core import Segment
    import requests
    from .vad import mute_non_speech_segments
    from backend.processing.LLM_Services import get_llm_backend
    from backend.utils.speaker_label_manager import SpeakerLabelManager
    from backend.utils.audio import get_audio_duration

    recording_id = str(recording_id)  # Defensive: always use string
    # Always resolve audio_path to absolute for file access
    abs_audio_path = from_project_relative_path(audio_path)
    logger.info(f"Starting processing pipeline for recording ID: {recording_id}, path: {audio_path} (abs: {abs_audio_path})")

    # Update DB status to 'Processing'
    try:
        database.update_recording_status(recording_id, 'Processing')
        logger.info(f"Set status to 'Processing' for recording_id={recording_id}")
    except Exception as e:
        logger.error(f"Failed to update recording {recording_id} status to Processing: {e}", exc_info=True)
        # Decide whether to proceed or fail early
        # return False 

    temp_files = []
    try:
        # VAD Stage
        if stage_callback:
            stage_callback('vad')
        if stage_update_callback:
            stage_update_callback("Pre-processing audio...")
            
        # Simple VAD progress callback
        def vad_progress_callback(percent):
            if whisper_progress_callback:
                whisper_progress_callback(percent)
            
        # Step 1: Preprocess for VAD (MP3 to mono, 16kHz WAV)
        if cancel_check and cancel_check():
            logger.info(f"Processing cancelled before VAD preprocessing for recording_id={recording_id}")
            return False
        vad_progress_callback(20)  # 20% - starting preprocessing
        vad_wav_path = preprocess_audio_for_vad(abs_audio_path)
        if not vad_wav_path:
            raise RuntimeError("Audio preprocessing for VAD failed.")
        temp_files.append(vad_wav_path)
        vad_progress_callback(40)  # 40% - preprocessing done

        # Step 2: Run Silero VAD (mute non-speech, output new WAV)
        if cancel_check and cancel_check():
            logger.info(f"Processing cancelled before VAD for recording_id={recording_id}")
            return False
        if stage_update_callback:
            stage_update_callback("Detecting voice activity...")
        vad_progress_callback(60)  # 60% - starting VAD
        vad_processed_wav = vad_wav_path.replace("_vad.wav", "_vad_processed.wav")
        vad_success = mute_non_speech_segments(vad_wav_path, vad_processed_wav)
        if not vad_success:
            raise RuntimeError("Silero VAD processing failed.")
        temp_files.append(vad_processed_wav)
        vad_progress_callback(80)  # 80% - VAD done

        # Step 3: Convert VAD-processed WAV to MP3
        if cancel_check and cancel_check():
            logger.info(f"Processing cancelled before VAD->MP3 for recording_id={recording_id}")
            return False
        vad_processed_mp3 = vad_processed_wav.replace(".wav", ".mp3")
        mp3_success = convert_wav_to_mp3(vad_processed_wav, vad_processed_mp3)
        if not mp3_success:
            raise RuntimeError("Failed to convert VAD-processed WAV to MP3.")
        temp_files.append(vad_processed_mp3)
        vad_progress_callback(100)  # 100% - VAD stage complete

        processed_audio_path = vad_processed_mp3
        logger.info(f"Using VAD-processed audio for pipeline: {processed_audio_path}")

        # Get audio duration for clamping
        try:
            audio_duration = get_audio_duration(processed_audio_path)
            logger.info(f"Audio duration: {audio_duration:.2f}s")
        except Exception as e:
            logger.warning(f"Could not get audio duration: {e}")
            audio_duration = None

        # 1. Transcription
        if stage_callback:
            stage_callback('transcription')
        transcription_result = None
        try:
            if cancel_check and cancel_check():
                logger.info(f"Processing cancelled before transcription for recording_id={recording_id}")
                return False
            if stage_update_callback:
                stage_update_callback("Transcribing...")
            transcription_result = transcribe_audio_with_progress(processed_audio_path, whisper_progress_callback, cancel_check=cancel_check)
            if transcription_result is None:
                raise ValueError("Transcription failed.")
            logger.info(f"Transcription successful for recording ID: {recording_id}")
            
            # Save raw transcript if configured (now stored in database)
            if config_manager.get("save_raw_transcript", True):
                try:
                    raw_transcript_text = json.dumps(transcription_result, indent=4)
                    database.update_recording_transcript_text(recording_id, raw_transcript_text=raw_transcript_text)
                    logger.info(f"Raw transcript saved to database for recording {recording_id}")
                except Exception as e:
                    logger.error(f"Failed to save raw transcript for {recording_id}: {e}", exc_info=True)
                    # Continue processing, but log the error

        except Exception as e:
            logger.error(f"Transcription step failed for recording ID: {recording_id}: {e}", exc_info=True)
            database.update_recording_status(recording_id, 'Error')
            logger.info(f"Set status to 'Error' for recording_id={recording_id}")
            return False

        # Before LLM-based steps
        if is_llm_available():
            llm_provider = config_manager.get("llm_provider", "gemini")
            api_key = config_manager.get(f"{llm_provider}_api_key")
            model = config_manager.get(f"{llm_provider}_model")
            backend = get_llm_backend(llm_provider, api_key=api_key, model=model)
        # 2. Diarization
        if stage_callback:
            stage_callback('diarization')
        diarization_result = None
        try:
            if cancel_check and cancel_check():
                logger.info(f"Processing cancelled before diarization for recording_id={recording_id}")
                return False
            if stage_update_callback:
                stage_update_callback("Diarizing...")
            diarization_result = diarize_audio_with_progress(processed_audio_path, diarization_progress_callback, cancel_check=cancel_check)
            if diarization_result is None:
                raise ValueError("Diarization failed.")
            logger.info(f"Diarization successful for recording ID: {recording_id}")
            # Diagnostic logging: print diarization result and labels
            logger.info(f"Diarization result: {diarization_result}")
            logger.info(f"Diarization labels: {getattr(diarization_result, 'labels', lambda: [])()}")

            # --- NEW: After diarization, extract clearest segment for each speaker and store in DB ---
            speaker_labels = list(diarization_result.labels())
            snippet_segments = {}
            for label in speaker_labels:
                # Find all segments for this label
                segments = [ (turn.start, turn.end) for turn, _, seg_label in diarization_result.itertracks(yield_label=True) if seg_label == label ]
                logger.info(f"Segments for label {label}: {segments}")
                # Use the same 'clearest' logic as before
                if not segments:
                    logger.warning(f"No segments found for label {label}")
                    continue
                # Convert to list of dicts for compatibility with clearest logic
                seg_dicts = [{'start_time': float(s), 'end_time': float(e)} for s, e in segments]
                def select_clearest_segment(segments, min_length=4.0):
                    long_enough = [s for s in segments if (s['end_time'] - s['start_time']) >= min_length]
                    if long_enough:
                        mid = sum([(s['start_time'] + s['end_time'])/2 for s in long_enough]) / len(long_enough)
                        long_enough.sort(key=lambda s: abs(((s['start_time'] + s['end_time'])/2) - mid))
                        return long_enough[0]
                    segments.sort(key=lambda s: (s['end_time'] - s['start_time']), reverse=True)
                    return segments[0]
                clearest = select_clearest_segment(seg_dicts, min_length=4.0)
                logger.info(f"Clearest segment for label {label}: {clearest}")
                snippet_segments[label] = (clearest['start_time'], clearest['end_time'])
            logger.info(f"Final snippet_segments dict: {snippet_segments}")
            # Add/update diarization labels and snippet segments in DB
            from ..db import database as db_ops
            db_ops.add_diarization_labels(recording_id, speaker_labels, snippet_segments)

            # --- NEW: Speaker Embedding & Identification ---
            try:
                if stage_update_callback:
                    stage_update_callback("Identifying speakers...")
                
                from .embedding import extract_embeddings, cosine_similarity, merge_embeddings
                from backend.core.db import get_sync_session
                from backend.models.speaker import GlobalSpeaker, RecordingSpeaker
                from sqlmodel import select
                import uuid

                device_str = config_manager.get("processing_device", "cpu")
                embeddings = extract_embeddings(processed_audio_path, diarization_result, device_str=device_str)
                
                if embeddings:
                    with get_sync_session() as session:
                        # Get RecordingSpeakers for this recording
                        statement = select(RecordingSpeaker).where(RecordingSpeaker.recording_id == int(recording_id))
                        rec_speakers = session.exec(statement).all()
                        rec_speaker_map = {rs.diarization_label: rs for rs in rec_speakers}
                        
                        # Get all GlobalSpeakers with embeddings
                        global_speakers = session.exec(select(GlobalSpeaker)).all()
                        
                        for label, embedding in embeddings.items():
                            if label in rec_speaker_map:
                                rs = rec_speaker_map[label]
                                rs.embedding = embedding
                                session.add(rs)
                                
                                # Match against Global Speakers
                                best_match = None
                                best_score = 0.0
                                threshold = 0.75 
                                
                                for gs in global_speakers:
                                    if gs.embedding:
                                        score = cosine_similarity(embedding, gs.embedding)
                                        if score > best_score:
                                            best_score = score
                                            best_match = gs
                                
                                if best_match and best_score >= threshold:
                                    logger.info(f"Matched {label} to Global Speaker {best_match.name} (Score: {best_score:.2f})")
                                    rs.global_speaker_id = best_match.id
                                    
                                    # Active Learning: Update Global Speaker embedding
                                    # We use a small alpha (0.1) for automatic updates to avoid drift from single bad samples
                                    if best_match.embedding:
                                        best_match.embedding = merge_embeddings(best_match.embedding, embedding, alpha=0.1)
                                        session.add(best_match)
                                    
                                    session.add(rs)
                                else:
                                    # No match found - Auto-create new Global Speaker
                                    short_id = str(uuid.uuid4())[:8]
                                    new_name = f"New Voice {short_id}"
                                    logger.info(f"No match for {label} (Best score: {best_score:.2f}). Creating new Global Speaker: {new_name}")
                                    
                                    new_gs = GlobalSpeaker(name=new_name, embedding=embedding)
                                    session.add(new_gs)
                                    # We need to commit here to get the ID for the new global speaker
                                    # and to make it available for other speakers in this loop if needed (though unlikely)
                                    session.commit()
                                    session.refresh(new_gs)
                                    
                                    # Add to local list so subsequent iterations can match against it if needed
                                    global_speakers.append(new_gs)
                                    
                                    rs.global_speaker_id = new_gs.id
                                    session.add(rs)
                        
                        session.commit()
                        logger.info(f"Saved embeddings and updated speaker links for recording {recording_id}")

            except Exception as e:
                logger.error(f"Speaker identification failed: {e}", exc_info=True)
                # Continue processing

        except Exception as e:
            logger.error(f"Diarization step failed for recording ID: {recording_id}: {e}", exc_info=True)
            database.update_recording_status(recording_id, 'Error')
            logger.info(f"Set status to 'Error' for recording_id={recording_id}")
            return False

        # 3. Combine Results and Save Diarized Transcript
        try:
            if cancel_check and cancel_check():
                logger.info(f"Processing cancelled before combining/saving for recording_id={recording_id}")
                return False
            # --- Diagnostics: Log transcription and diarization results ---
            logger.info(f"Transcription result type: {type(transcription_result)}, keys: {list(transcription_result.keys()) if isinstance(transcription_result, dict) else 'N/A'}")
            if isinstance(transcription_result, dict):
                logger.info(f"Transcription segments count: {len(transcription_result.get('segments', []))}")
            logger.info(f"Diarization result type: {type(diarization_result)}")
            if diarization_result is not None:
                try:
                    labels = list(diarization_result.labels())
                    logger.info(f"Diarization labels: {labels}, count: {len(labels)}")
                except Exception as e:
                    logger.warning(f"Could not extract labels from diarization result: {e}")

            # --- Check for empty or missing results ---
            transcription_segments = transcription_result.get('segments') if isinstance(transcription_result, dict) else None
            if not transcription_segments:
                logger.error(f"Transcription result is missing segments or segments is empty. transcription_result: {transcription_result}")
                raise ValueError("Transcription result is missing segments or segments is empty.")
            if not diarization_result:
                logger.warning("Diarization result is None or invalid. Proceeding to save transcript with all speakers as 'UNKNOWN'.")
                # Fallback: Save transcript with all speakers as UNKNOWN
                combined_transcript = [
                    {
                        "start": seg["start"],
                        "end": seg["end"],
                        "speaker": "UNKNOWN",
                        "text": seg["text"].strip()
                    }
                    for seg in transcription_segments if seg.get("text", "").strip()
                ]
            else:
                combined_transcript = combine_transcription_diarization(
                    transcription_result, diarization_result, audio_duration=audio_duration
                )
                if combined_transcript is None:
                    logger.error("Combining transcription and diarization failed. Saving transcript with all speakers as 'UNKNOWN'.")
                    combined_transcript = [
                        {
                            "start": seg["start"],
                            "end": seg["end"],
                            "speaker": "UNKNOWN",
                            "text": seg["text"].strip()
                        }
                        for seg in transcription_segments if seg.get("text", "").strip()
                    ]

            # Consolidate transcript for output
            consolidated_transcript = consolidate_diarized_transcript(combined_transcript)

            # --- LLM Speaker Name Inference (First Pass) ---
            if is_llm_available():
                try:
                    if stage_update_callback:
                        stage_update_callback("Inferring speakers...")
                    transcript_for_llm = ""
                    for entry in consolidated_transcript:
                        start = entry['start']
                        end = entry['end']
                        def fmt(ts):
                            h = int(ts // 3600)
                            m = int((ts % 3600) // 60)
                            s = ts % 60
                            return f"{h:02}.{m:02}.{s:05.2f}s"
                        diarization_label = entry['speaker']
                        transcript_for_llm += f"[{fmt(start)} - {fmt(end)}] - {diarization_label} - {entry['text']}\n"
                    inferred_mapping = backend.infer_speakers(transcript_for_llm)
                    # Update DB with inferred names (first pass)
                    from ..db import database as db_ops
                    speakers = db_ops.get_speakers_for_recording(recording_id)
                    for s in speakers:
                        label = s['diarization_label']
                        inferred_name = inferred_mapping.get(label)
                        if inferred_name and inferred_name != s['name']:
                            db_ops.update_speaker_name(s['id'], inferred_name)
                    # Refresh label_to_name for transcript saving
                    speakers = db_ops.get_speakers_for_recording(recording_id)
                    label_to_name = {s['diarization_label']: s['name'] for s in speakers}
                    speaker_label_manager = SpeakerLabelManager()
                    speaker_label_manager.set_mapping(label_to_name)
                except Exception as e:
                    logger.error(f"LLM speaker inference failed: {e}")
                    # Fallback: use existing label_to_name
                    speakers = db_ops.get_speakers_for_recording(recording_id)
                    label_to_name = {s['diarization_label']: s['name'] for s in speakers}
                    speaker_label_manager = SpeakerLabelManager()
                    speaker_label_manager.set_mapping(label_to_name)
            else:
                # No LLM available: skip inference, allow manual relabel
                from ..db import database as db_ops
                speakers = db_ops.get_speakers_for_recording(recording_id)
                label_to_name = {s['diarization_label']: s['name'] for s in speakers}
                speaker_label_manager = SpeakerLabelManager()
                speaker_label_manager.set_mapping(label_to_name)

            # Save diarized transcript if configured (now stored in database)
            if config_manager.get("save_diarized_transcript", True):
                try:
                    # Generate diarized transcript text
                    diarized_lines = []
                    for entry in consolidated_transcript:
                        start = entry['start']
                        end = entry['end']
                        def fmt(ts):
                            h = int(ts // 3600)
                            m = int((ts % 3600) // 60)
                            s = ts % 60
                            return f"{h:02}.{m:02}.{s:05.2f}s"
                        diarization_label = entry['speaker']
                        # Always write the diarization label (e.g., SPEAKER_00), not the display name
                        # This allows dynamic relabeling, merging, and deletion in the UI and DB
                        diarized_lines.append(f"[{fmt(start)} - {fmt(end)}] - {diarization_label} - {entry['text']}")
                    
                    diarized_lines.append("END")
                    diarized_transcript_text = "\n\n".join(diarized_lines)
                    
                    # Save to database
                    database.update_recording_transcript_text(recording_id, diarized_transcript_text=diarized_transcript_text)
                    logger.info(f"Diarized transcript saved to database for recording {recording_id}")

                    # --- New: Infer meeting title using LLM ---
                    if is_llm_available() and config_manager.get("infer_meeting_title", True):
                        try:
                            if stage_update_callback:
                                stage_update_callback("Inferring meeting title...")
                            from backend.utils.transcript_store import TranscriptStore
                            if is_llm_available():
                                transcript_for_title = diarized_transcript_text or TranscriptStore.get(recording_id, "raw")
                                if transcript_for_title:
                                    inferred_title = backend.infer_meeting_title(transcript_for_title)
                                    if inferred_title:
                                        # Trim very long titles to DB-friendly length
                                        inferred_title = inferred_title[:255]
                                        database.update_recording_name(recording_id, inferred_title)
                                        logger.info(f"Inferred meeting title '{inferred_title}' for recording {recording_id}")
                        except Exception as e:
                            logger.error(f"Failed to infer meeting title for recording {recording_id}: {e}", exc_info=True)

                except Exception as e:
                    logger.error(f"Failed to save diarized transcript for {recording_id}: {e}", exc_info=True)
                    # Continue, but log the error

        except Exception as e:
            logger.error(f"Combining/Saving step failed for recording ID: {recording_id}: {e}", exc_info=True)
            database.update_recording_status(recording_id, 'Error')
            logger.info(f"Set status to 'Error' for recording_id={recording_id}")
            return False

        # 4. Final DB Update
        try:
            if cancel_check and cancel_check():
                logger.info(f"Processing cancelled before final DB update for recording_id={recording_id}")
                return False
            processed_at = database.get_current_timestamp_str() # Need a utility in database.py
            database.update_recording_status(recording_id, 'Processed')
            logger.info(f"Set status to 'Processed' for recording_id={recording_id}")
            logger.info(f"Successfully processed recording ID: {recording_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to update recording {recording_id} status to Processed: {e}", exc_info=True)
            logger.info(f"Set status to 'Error' for recording_id={recording_id}")
            return False # For now, fail if final update fails
    finally:
        # Clean up all temp files
        for f in temp_files:
            try:
                if f and os.path.exists(f):
                    os.remove(f)
                    logger.info(f"Deleted temp file: {f}")
            except Exception as e:
                logger.warning(f"Failed to delete temp file {f}: {e}", exc_info=True)


def combine_transcription_diarization(transcription: dict, diarization: 'Annotation', audio_duration: float = None) -> list | None:
    """Combines Whisper segments with Pyannote diarization.

    Args:
        transcription: The result dictionary from whisper.transcribe().
        diarization: The pyannote.core.Annotation object from diarization.
        audio_duration: Optional total duration of the audio in seconds. Used for clamping.

    Returns:
        A list of dictionaries, each representing a segment with start, end,
        speaker label, and text. Returns None on failure.
    """
    # Local import for pyannote.core.Segment for performance
    from pyannote.core import Segment
    if not transcription or 'segments' not in transcription or not diarization:
        logger.error("Invalid input for combining transcription and diarization.")
        return None
    
    try:
        segments = transcription['segments']
        speaker_turns = diarization
        combined = []
        # Keep track of the last known speaker to attribute short, unassigned segments
        last_known_speaker = "UNKNOWN"
        last_segment_end_time = 0.0

        for segment in segments:
            start_time = segment['start']
            end_time = segment['end']
            text = segment['text'].strip()

            # --- Clamping and Hallucination Filtering ---
            if audio_duration is not None:
                # 1. Skip segments that start after the audio ends
                if start_time >= audio_duration:
                    logger.warning(f"Skipping segment starting after audio end: {start_time:.2f}s >= {audio_duration:.2f}s. Text: '{text}'")
                    continue
                
                # 2. Clamp end time to audio duration
                if end_time > audio_duration:
                    logger.info(f"Clamping segment end from {end_time:.2f}s to {audio_duration:.2f}s")
                    end_time = audio_duration
                
                # 3. Filter specific hallucinations near the end
                # "Thank you" is a very common Whisper hallucination at the end of files
                if start_time > (audio_duration - 10.0): # Check last 10 seconds
                    cleaned_text = text.lower().strip(".,!? ")
                    hallucinations = ["thank you", "thanks", "bye", "you", "thank you.", "thank you very much", "t and e"]
                    if any(h in cleaned_text for h in hallucinations):
                        logger.warning(f"Removing likely hallucination at end: '{text}' at {start_time:.2f}s")
                        continue

            if start_time >= end_time:
                continue
            # --------------------------------------------

            active_speaker = "UNKNOWN"
            max_overlap = 0.0
            speaker_overlap = {}
            transcript_seg = Segment(start_time, end_time)
            try:
                # Iterate over all diarization segments and compute overlap
                for turn, _, label in speaker_turns.itertracks(yield_label=True):
                    overlap_seg = transcript_seg & turn  # intersection segment
                    overlap = overlap_seg.duration if overlap_seg else 0.0
                    if overlap > 0:
                        if label not in speaker_overlap:
                            speaker_overlap[label] = 0.0
                        speaker_overlap[label] += overlap
                        if speaker_overlap[label] > max_overlap:
                            max_overlap = speaker_overlap[label]
                            active_speaker = label

                if max_overlap == 0.0:
                    time_since_last_segment = start_time - last_segment_end_time
                    # If no overlap, try to assign to the last known speaker if the gap is small
                    if last_known_speaker != "UNKNOWN" and time_since_last_segment < 2.0:
                        active_speaker = last_known_speaker
                        logger.info(
                            f"Segment [{start_time:.2f}s - {end_time:.2f}s] had no direct overlap. "
                            f"Attributed to recent speaker '{active_speaker}' (gap: {time_since_last_segment:.2f}s)."
                        )
                    else:
                        logger.warning(
                            f"No speaker turn found overlapping with segment [{start_time:.2f}s - {end_time:.2f}s]. "
                            f"Assigning UNKNOWN. (Time since last speaker: {time_since_last_segment:.2f}s)"
                        )

            except Exception as e:
                logger.error(f"Error assigning speaker for segment [{start_time:.2f}s - {end_time:.2f}s]: {e}", exc_info=True)
            
            if text: # Only add segments with actual text
                combined.append({
                    "start": start_time,
                    "end": end_time,
                    "speaker": active_speaker,
                    "text": text
                })
                # Update the last known speaker if we found one
                if active_speaker != "UNKNOWN":
                    last_known_speaker = active_speaker
                last_segment_end_time = end_time

        logger.info(f"Successfully combined {len(segments)} transcription segments with speaker turns.")
        
        # --- Final Safety Check: Remove trailing UNKNOWN segments that look like hallucinations ---
        # This catches cases where audio_duration might have been missing or slightly off
        if combined:
            last_seg = combined[-1]
            if last_seg['speaker'] == "UNKNOWN":
                cleaned_text = last_seg['text'].lower().strip(".,!? ")
                hallucinations = ["thank you", "thanks", "bye", "you", "thank you.", "thank you very much", "t and e"]
                # Also check if it's very short (< 2s) and isolated
                duration = last_seg['end'] - last_seg['start']
                is_hallucination = any(h in cleaned_text for h in hallucinations)
                
                if is_hallucination or (duration < 2.0 and len(cleaned_text) < 10):
                    logger.warning(f"Removing trailing UNKNOWN segment (safety check): '{last_seg['text']}'")
                    combined.pop()
        
        return combined
    except Exception as e:
        logger.error(f"Error combining transcription and diarization: {e}", exc_info=True)
        return None 

def consolidate_diarized_transcript(segments, min_duration_s: float = 1.0):
    """
    Consolidate diarized transcript segments by speaker, merging consecutive segments by the same speaker,
    and handling overlapping speakers. Returns a list of dicts with start, end, speaker, and text.
    Filters out final consolidated segments that are shorter than min_duration_s.
    """
    if not segments:
        return []
    consolidated = []
    i = 0
    n = len(segments)
    while i < n:
        curr = segments[i]
        curr_start = curr['start']
        curr_end = curr['end']
        curr_speaker = curr['speaker']
        curr_text = curr['text']
        overlap_speakers = set([curr_speaker])
        j = i + 1
        while j < n:
            next_seg = segments[j]
            # Check for overlap
            if next_seg['start'] < curr_end:
                # Overlapping segment
                overlap_speakers.add(next_seg['speaker'])
                curr_end = max(curr_end, next_seg['end'])
                curr_text += ' ' + next_seg['text']
                j += 1
            elif next_seg['speaker'] == curr_speaker and abs(next_seg['start'] - curr_end) < 0.01:
                # Consecutive, same speaker, no gap
                curr_end = next_seg['end']
                curr_text += ' ' + next_seg['text']
                j += 1
            else:
                break
        # Format speaker label
        if len(overlap_speakers) > 1:
            speaker_label = ' and '.join(sorted(overlap_speakers)) + ' (Overlap)'
        else:
            speaker_label = curr_speaker

        # Add the consolidated segment only if its duration is long enough
        if (curr_end - curr_start) >= min_duration_s:
            consolidated.append({
                'start': curr_start,
                'end': curr_end,
                'speaker': speaker_label,
                'text': curr_text.strip()
            })
        else:
            logger.info(f"Filtering out short consolidated segment: "
                        f"[{curr_start:.2f}s - {curr_end:.2f}s] Speaker {speaker_label} "
                        f"duration {(curr_end - curr_start):.2f}s < {min_duration_s}s")
            
        i = j
    return consolidated 