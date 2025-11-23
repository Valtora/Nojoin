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
    
    Automatically detects if word-level timestamps are available:
    - If YES: Uses precise word-level alignment (better for fast turn-taking).
    - If NO: Uses segment-level dominant speaker logic (fallback for Windows/No-Triton).

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
        
        # Check if we have word timestamps
        has_word_timestamps = len(segments) > 0 and 'words' in segments[0]
        
        if has_word_timestamps:
            logger.info("Combining using WORD-LEVEL timestamps (High Precision)")
            return _combine_word_level(segments, speaker_turns)
        else:
            logger.info("Combining using SEGMENT-LEVEL timestamps (Standard Precision)")
            return _combine_segment_level(segments, speaker_turns)

    except Exception as e:
        logger.error(f"Error combining transcription and diarization: {e}", exc_info=True)
        return None

def _combine_segment_level(segments, speaker_turns):
    """Fallback logic: Assign speaker based on max overlap with the whole segment."""
    from pyannote.core import Segment
    final_segments = []
    
    for seg in segments:
        start = seg['start']
        end = seg['end']
        text = seg['text'].strip()
        
        if not text:
            continue
            
        whisper_segment = Segment(start, end)
        max_overlap = 0.0
        dominant_speaker = "UNKNOWN"
        
        for turn, _, label in speaker_turns.itertracks(yield_label=True):
            intersection = whisper_segment & turn
            if intersection:
                overlap_duration = intersection.duration
                if overlap_duration > max_overlap:
                    max_overlap = overlap_duration
                    dominant_speaker = label
        
        final_segments.append({
            "start": start,
            "end": end,
            "speaker": dominant_speaker,
            "text": text
        })
    return final_segments

def _combine_word_level(segments, speaker_turns):
    """High-precision logic: Align individual words to speakers."""
    from pyannote.core import Segment
    
    # Flatten all words
    all_words = []
    for seg in segments:
        if 'words' in seg:
            all_words.extend(seg['words'])
            
    final_segments = []
    current_segment = {
        "start": 0.0,
        "end": 0.0,
        "speaker": "UNKNOWN",
        "text": "",
        "words": []
    }
    
    def get_speaker_for_range(start, end):
        max_overlap = 0.0
        active_speaker = "UNKNOWN"
        word_seg = Segment(start, end)
        for turn, _, label in speaker_turns.itertracks(yield_label=True):
            overlap_seg = word_seg & turn
            if overlap_seg:
                overlap_dur = overlap_seg.duration
                if overlap_dur > max_overlap:
                    max_overlap = overlap_dur
                    active_speaker = label
        return active_speaker

    for i, word_data in enumerate(all_words):
        w_start = word_data['start']
        w_end = word_data['end']
        w_text = word_data['word']
        
        speaker = get_speaker_for_range(w_start, w_end)
        
        if i == 0:
            current_segment["start"] = w_start
            current_segment["speaker"] = speaker
            current_segment["text"] = w_text
            current_segment["end"] = w_end
            continue
        
        gap = w_start - current_segment["end"]
        
        # Split if speaker changes OR gap > 1.0s
        if speaker != current_segment["speaker"] or gap > 1.0:
            final_segments.append(current_segment)
            current_segment = {
                "start": w_start,
                "end": w_end,
                "speaker": speaker,
                "text": w_text,
                "words": []
            }
        else:
            current_segment["text"] += "" + w_text
            current_segment["end"] = w_end
            
    final_segments.append(current_segment)
    return final_segments 

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