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
    
    logger.info(f"_combine_word_level: Processing {len(all_words)} words")
            
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
            # Strip leading space from first word if present
            current_segment["text"] = w_text.lstrip()
            current_segment["end"] = w_end
            current_segment["words"].append(word_data)
            continue
        
        gap = w_start - current_segment["end"]
        
        # Split if speaker changes OR gap > 1.0s
        if speaker != current_segment["speaker"] or gap > 1.0:
            final_segments.append(current_segment)
            current_segment = {
                "start": w_start,
                "end": w_end,
                "speaker": speaker,
                # Strip leading space from first word of new segment
                "text": w_text.lstrip(),
                "words": [word_data]
            }
        else:
            # Whisper word tokens include leading space (e.g., " hello")
            # Add space if the word doesn't already have one
            if w_text.startswith(' '):
                current_segment["text"] += w_text
            else:
                current_segment["text"] += ' ' + w_text
            current_segment["end"] = w_end
            current_segment["words"].append(word_data)
            
    final_segments.append(current_segment)
    
    # Log speaker distribution
    speaker_counts = {}
    for seg in final_segments:
        spk = seg['speaker']
        speaker_counts[spk] = speaker_counts.get(spk, 0) + 1
    logger.info(f"_combine_word_level: Created {len(final_segments)} segments with speaker distribution: {speaker_counts}")
    
    return final_segments 

def consolidate_diarized_transcript(segments, min_duration_s: float = 0.5, max_duration_s: float = 10.0):
    """
    Consolidate diarized transcript segments by speaker, merging consecutive segments by the same speaker,
    and handling overlapping speakers. Returns a list of dicts with start, end, speaker, and text.
    Filters out final consolidated segments that are shorter than min_duration_s.
    Forces a split if the segment duration exceeds max_duration_s.
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
        curr_words = curr.get('words', [])
        
        # --- Fix for Giant Single Segments ---
        # Splits segment if longer than max duration.
        # This handles cases where Whisper returned a 30s block that needs breaking.
        if (curr_end - curr_start) > max_duration_s:
            
            # Smart Word-Level Splitting (Soft Limit Strategy)
            if curr_words:
                # Target splitting window: 8s to 12s (centered around 10s default)
                # But hard cap is usually max_duration_s (10s) passed in.
                # If the user wants a soft limit up to 12s, they should ideally pass max_duration_s=12.0
                # However, the requirement says "limit to 10s... soft limit 8-12s".
                # Let's interpret max_duration_s as the "ideal target" (10s).
                
                # Logic:
                # 1. Look for a sentence break (.!?) between curr_start + 8s and curr_start + 12s.
                # 2. If found, split there.
                # 3. If not, split at the word boundary closest to curr_start + 10s (but not exceeding 12s).
                
                soft_min = curr_start + 8.0
                soft_max = curr_start + 12.0
                hard_target = curr_start + 10.0
                
                # split_idx = -1 # Not used directly in loop
                
                # Track best sentence break vs best word break separately
                best_sentence_idx = -1
                min_sentence_dist = float('inf')
                
                best_word_idx = -1
                min_word_dist = float('inf')

                for idx, w in enumerate(curr_words):
                    w_end_time = w['end']
                    
                    if w_end_time < soft_min:
                        continue
                    if w_end_time > soft_max:
                        break # Exceeded strict search window
                    
                    dist = abs(w_end_time - hard_target)
                    
                    # Check for sentence punctuation
                    is_sentence_end = w['word'].strip().endswith(('.', '!', '?'))
                    
                    if is_sentence_end:
                         if dist < min_sentence_dist:
                             best_sentence_idx = idx + 1
                             min_sentence_dist = dist
                    
                    # Always track best fallback word
                    if dist < min_word_dist:
                        best_word_idx = idx + 1
                        min_word_dist = dist
                
                # Decision time: Prefer sentence break if found
                if best_sentence_idx != -1:
                    split_idx = best_sentence_idx
                elif best_word_idx != -1:
                    split_idx = best_word_idx
                else:
                    # Fallback: Just slice at 10s if we couldn't find anything in 8-12s window
                    # (e.g. maybe the first word ends at 13s? rare)
                    # Find first word ending after 10s
                    split_idx = -1
                    for idx, w in enumerate(curr_words):
                        if w['end'] >= hard_target:
                            split_idx = idx + 1
                            break
                    if split_idx == -1:
                        split_idx = len(curr_words)
                
                # Perform the split
                first_chunk_words = curr_words[:split_idx]
                remainder_words = curr_words[split_idx:]
                
                if not first_chunk_words:
                     # Edge case: first word is already super long? Force split at 1 word.
                     first_chunk_words = curr_words[:1]
                     remainder_words = curr_words[1:]
                
                # Reconstruct Text
                # Whisper words have leading spaces often. Join carefully.
                def reconstruct_text(words):
                    return "".join([w['word'] for w in words]).strip()

                chunk_text = reconstruct_text(first_chunk_words)
                remainder_text = reconstruct_text(remainder_words)
                
                split_end_time = first_chunk_words[-1]['end']
                
                consolidated.append({
                    'start': curr_start,
                    'end': split_end_time,
                    'speaker': curr_speaker,
                    'text': chunk_text,
                    'words': first_chunk_words
                })
                
                segments[i] = {
                    'start': split_end_time,
                    'end': curr_end, 
                    'speaker': curr_speaker,
                    'text': remainder_text,
                    'words': remainder_words
                }
                logger.debug(f"Smart split at {split_end_time:.2f}s (Target: 10s). Sentence break: {best_sentence_idx != -1}")
                continue
            
            # --- Fallback: Naive Character Split (No Words) ---
            else:
                # Calculates number of full chunks.
                duration = curr_end - curr_start
                
                # Split point
                split_end = curr_start + max_duration_s
                
                # Uses naive text split due to missing word timestamps.
                # If we had word timestamps, they are lost in this dict structure usually (unless passed through).
                # We'll just do a rough ratio split for text.
                ratio = max_duration_s / duration
                split_idx = int(len(curr_text) * ratio)
                
                # Try to split at a space near the ratio to avoid cutting words
                # Search within a window
                search_window = 10
                found_space = False
                for offset in range(search_window):
                    # Check forward
                    if split_idx + offset < len(curr_text) and curr_text[split_idx + offset] == ' ':
                        split_idx += offset
                        found_space = True
                        break
                    # Check backward
                    if split_idx - offset > 0 and curr_text[split_idx - offset] == ' ':
                        split_idx -= offset
                        found_space = True
                        break
                
                chunk_text = curr_text[:split_idx].strip()
                remainder_text = curr_text[split_idx:].strip()
                
                consolidated.append({
                    'start': curr_start,
                    'end': split_end,
                    'speaker': curr_speaker,
                    'text': chunk_text
                })
                
                # Now we set up the remainder as the new 'curr' for the next iteration of the loop
                # But we can't easily modify `segments` list in place safely or insert.
                # Easiest way: Update `segments[i]` to be the remainder and STAY on `i` (don't increment).
                # BUT we need to avoid infinite loops if we don't make progress.
                # Since `split_end` > `curr_start` (guaranteed by max_duration_s > 0), the remainder is shorter.
                # Eventually it will be < max_duration_s.
                
                segments[i] = {
                    'start': split_end,
                    'end': curr_end, # Original end
                    'speaker': curr_speaker,
                    'text': remainder_text
                }
                # Continue loop WITHOUT incrementing `i` to process the remainder
                continue

        overlap_speakers = set([curr_speaker])
        j = i + 1
        
        while j < n:
            next_seg = segments[j]
            next_words = next_seg.get('words', [])
            
            # Predictive check: Would merging this segment exceed the max duration?
            # Checks if merging exceeds max duration.
            # If so, we STOP merging here, unless it's a single segment that is already too long (which we can't help without word split)
            # But the logic here merges consecutive small segments.
            
            potential_end = max(curr_end, next_seg['end'])
            if (potential_end - curr_start) > max_duration_s:
                 # Break the merge loop to enforce the split
                 break

            # Check for overlap
            if next_seg['start'] < curr_end:
                # Overlapping segment
                overlap_speakers.add(next_seg['speaker'])
                curr_end = potential_end
                curr_text += ' ' + next_seg['text']
                curr_words.extend(next_words)
                j += 1
            elif next_seg['speaker'] == curr_speaker and abs(next_seg['start'] - curr_end) < 0.01:
                # Consecutive, same speaker, no gap
                curr_end = next_seg['end'] # potential_end is next_seg['end'] here
                curr_text += ' ' + next_seg['text']
                curr_words.extend(next_words)
                j += 1
            else:
                break
                
        # Format speaker label
        if len(overlap_speakers) > 1:
            speaker_label = ' and '.join(sorted(overlap_speakers)) + ' (Overlap)'
        else:
            speaker_label = curr_speaker

        # Add the consolidated segment only if its duration is long enough
        # OR if it's the only segment we have (to avoid dropping data for short recordings)
        # ... OR (end of stream AND no prepared segments).
        if (curr_end - curr_start) >= min_duration_s or (len(consolidated) == 0 and j == n):
            consolidated.append({
                'start': curr_start,
                'end': curr_end,
                'speaker': speaker_label,
                'text': curr_text.strip(),
                'words': curr_words
            })
        else:
            logger.info(f"Filtering out short consolidated segment: "
                        f"[{curr_start:.2f}s - {curr_end:.2f}s] Speaker {speaker_label} "
                        f"duration {(curr_end - curr_start):.2f}s < {min_duration_s}s")
            
        i = j
    
    # Log final consolidation results
    speaker_counts = {}
    for seg in consolidated:
        spk = seg['speaker']
        speaker_counts[spk] = speaker_counts.get(spk, 0) + 1
    logger.info(f"consolidate_diarized_transcript: Consolidated {len(segments)} segments into {len(consolidated)} segments")
    logger.info(f"consolidate_diarized_transcript: Final speaker distribution: {speaker_counts}")
    
    return consolidated