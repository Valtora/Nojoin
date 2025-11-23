import os
import re
import html as html_converter
import logging

logger = logging.getLogger(__name__)

def render_transcript(transcript_path, label_to_name, output_format="plain"):
    """
    Render a diarized transcript with mapped speaker names/roles.
    Args:
        transcript_path (str): Path to the diarized transcript file.
        label_to_name (dict): Mapping from diarization label to name/role.
        output_format (str): 'plain' for plaintext, 'html' for HTML output.
    Returns:
        str: Rendered transcript in the requested format.
    """
    if not os.path.exists(transcript_path):
        return "Transcript file not found."
    display_lines = []
    with open(transcript_path, 'r', encoding='utf-8') as f:
        for line in f:
            m = re.match(r"(\[.*?\]\s*-\s*)(.+?)(\s*-\s*)(.*)", line)
            if m:
                prefix = m.group(1)
                diarization_label = m.group(2).strip()
                sep = m.group(3)
                text_content = m.group(4)
                speaker_name = label_to_name.get(diarization_label, label_to_name.get('Unknown', 'Unknown'))
                if output_format == "html":
                    escaped_text_content = html_converter.escape(text_content)
                    html_line = (f'<span style="color:#888;font-size:12px;">{prefix}</span> '
                                 f'<b style="color:#ff9800;">{speaker_name}</b>'
                                 f'<span style="color:#888;font-size:12px;">{sep}</span>'
                                 f'<span style="color:#eaeaea;">{escaped_text_content}</span>')
                    display_lines.append(html_line)
                else:
                    display_lines.append(f"{prefix}{speaker_name}{sep}{text_content.strip()}")
            else:
                if output_format == "html":
                    escaped_line = html_converter.escape(line.rstrip('\n'))
                    display_lines.append(f'<span style="color:#eaeaea;">{escaped_line}</span>')
                else:
                    display_lines.append(line.rstrip('\n'))
    if output_format == "html":
        return "<br>".join(display_lines)
    else:
        return "\n".join(display_lines)

from typing import TYPE_CHECKING, List, Dict, Optional, Any

if TYPE_CHECKING:
    from pyannote.core import Annotation

def combine_transcription_diarization(transcription: dict, diarization: Any) -> Optional[List[Dict]]:
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
        # Keep track of the last known speaker to attribute short, unassigned segments
        last_known_speaker = "UNKNOWN"
        last_segment_end_time = 0.0

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