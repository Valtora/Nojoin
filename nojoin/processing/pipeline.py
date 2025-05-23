# nojoin/processing/pipeline.py
# Note: recording_id is always a string in the format YYYYMMDDHHMMSS (see db/schema.py)

import logging
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pyannote.core import Annotation
# All heavy imports are moved inside functions for faster application startup

logger = logging.getLogger(__name__)

def process_recording(recording_id: str, audio_path: str, whisper_progress_callback=None, diarization_progress_callback=None, stage_update_callback=None):
    """Runs the full transcription and diarization pipeline for a recording, with optional progress callbacks."""
    # Local imports for performance: only load heavy dependencies when actually processing
    import os
    import json
    from .transcribe import transcribe_audio, transcribe_audio_with_progress
    from .diarize import diarize_audio, diarize_audio_with_progress
    from ..db import database # Assuming db functions will be added here
    from ..utils.config_manager import config_manager, from_project_relative_path, to_project_relative_path, get_transcripts_dir, is_llm_available
    from .audio_preprocessing import preprocess_audio_for_diarization, cleanup_temp_file, preprocess_audio_for_vad, convert_wav_to_mp3
    from pyannote.core import Segment
    import requests
    from .vad import mute_non_speech_segments
    from nojoin.processing.LLM_Services import get_llm_backend
    from nojoin.utils.speaker_label_manager import SpeakerLabelManager

    recording_id = str(recording_id)  # Defensive: always use string
    # Always resolve audio_path to absolute for file access
    abs_audio_path = from_project_relative_path(audio_path)
    transcripts_dir = get_transcripts_dir()
    os.makedirs(transcripts_dir, exist_ok=True)
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
        # Step 1: Preprocess for VAD (MP3 to mono, 16kHz WAV)
        vad_wav_path = preprocess_audio_for_vad(abs_audio_path)
        if not vad_wav_path:
            raise RuntimeError("Audio preprocessing for VAD failed.")
        temp_files.append(vad_wav_path)

        # Step 2: Run Silero VAD (mute non-speech, output new WAV)
        vad_processed_wav = vad_wav_path.replace("_vad.wav", "_vad_processed.wav")
        vad_success = mute_non_speech_segments(vad_wav_path, vad_processed_wav)
        if not vad_success:
            raise RuntimeError("Silero VAD processing failed.")
        temp_files.append(vad_processed_wav)

        # Step 3: Convert VAD-processed WAV to MP3
        vad_processed_mp3 = vad_processed_wav.replace(".wav", ".mp3")
        mp3_success = convert_wav_to_mp3(vad_processed_wav, vad_processed_mp3)
        if not mp3_success:
            raise RuntimeError("Failed to convert VAD-processed WAV to MP3.")
        temp_files.append(vad_processed_mp3)

        processed_audio_path = vad_processed_mp3
        logger.info(f"Using VAD-processed audio for pipeline: {processed_audio_path}")

        # 1. Transcription
        transcription_result = None
        try:
            if stage_update_callback:
                stage_update_callback("Transcribing...")
            transcription_result = transcribe_audio_with_progress(processed_audio_path, whisper_progress_callback)
            if transcription_result is None:
                raise ValueError("Transcription failed.")
            logger.info(f"Transcription successful for recording ID: {recording_id}")
            
            # Save raw transcript if configured
            if config_manager.get("save_raw_transcript", True):
                base_name = os.path.splitext(os.path.basename(abs_audio_path))[0]
                raw_transcript_path = os.path.join(transcripts_dir, f"{base_name}_raw_transcript.json")
                try:
                    with open(raw_transcript_path, 'w', encoding='utf-8') as f:
                        json.dump(transcription_result, f, indent=4)
                    logger.info(f"Raw transcript saved to {raw_transcript_path}")
                    # Update DB with raw transcript path (store as relative)
                    rel_raw_transcript_path = to_project_relative_path(raw_transcript_path)
                    database.update_recording_paths(recording_id, raw_transcript_path=rel_raw_transcript_path)
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
        diarization_result = None
        try:
            if stage_update_callback:
                stage_update_callback("Diarizing...")
            diarization_result = diarize_audio_with_progress(processed_audio_path, diarization_progress_callback)
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

        except Exception as e:
            logger.error(f"Diarization step failed for recording ID: {recording_id}: {e}", exc_info=True)
            database.update_recording_status(recording_id, 'Error')
            logger.info(f"Set status to 'Error' for recording_id={recording_id}")
            return False

        # 3. Combine Results and Save Diarized Transcript
        try:
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
                    transcription_result, diarization_result
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

            # Save diarized transcript if configured
            if config_manager.get("save_diarized_transcript", True):
                base_name = os.path.splitext(os.path.basename(abs_audio_path))[0]
                diarized_transcript_path = os.path.join(transcripts_dir, f"{base_name}_diarized_transcript.txt")
                try:
                    with open(diarized_transcript_path, 'w', encoding='utf-8') as f:
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
                            f.write(f"[{fmt(start)} - {fmt(end)}] - {diarization_label} - {entry['text']}\n\n")
                        f.write("END\n")
                    logger.info(f"Diarized transcript saved to {diarized_transcript_path}")
                    rel_diarized_transcript_path = to_project_relative_path(diarized_transcript_path)
                    database.update_recording_paths(recording_id, diarized_transcript_path=rel_diarized_transcript_path)
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


def combine_transcription_diarization(transcription: dict, diarization: 'Annotation') -> list | None:
    """Combines Whisper segments with Pyannote diarization.

    Args:
        transcription: The result dictionary from whisper.transcribe().
        diarization: The pyannote.core.Annotation object from diarization.

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
        for segment in segments:
            start_time = segment['start']
            end_time = segment['end']
            text = segment['text'].strip()

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
                    logger.warning(
                        f"No speaker turn found overlapping with segment [{start_time:.2f}s - {end_time:.2f}s]. Assigning UNKNOWN."
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
        logger.info(f"Successfully combined {len(segments)} transcription segments with speaker turns.")
        return combined
    except Exception as e:
        logger.error(f"Error combining transcription and diarization: {e}", exc_info=True)
        return None 

def consolidate_diarized_transcript(segments):
    """
    Consolidate diarized transcript segments by speaker, merging consecutive segments by the same speaker,
    and handling overlapping speakers. Returns a list of dicts with start, end, speaker, and text.
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
        consolidated.append({
            'start': curr_start,
            'end': curr_end,
            'speaker': speaker_label,
            'text': curr_text.strip()
        })
        i = j
    return consolidated 