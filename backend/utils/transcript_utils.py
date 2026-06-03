import os
import re
import html as html_converter
import logging

logger = logging.getLogger(__name__)

WORD_OVERLAP_MIN_RATIO = 0.35
WORD_OVERLAP_MIN_DURATION_S = 0.05
SEGMENT_OVERLAP_MIN_RATIO = 0.25
SEGMENT_OVERLAP_MIN_DURATION_S = 0.25
ISOLATED_WORD_FLIP_MAX_DURATION_S = 0.45
ISOLATED_WORD_FLIP_MAX_GAP_S = 0.25
_PUNCTUATION_ONLY_RE = re.compile(r"^[\.,!?;:%)\]\}]+$")
_CONTRACTION_SUFFIXES = {
    "'d",
    "'ll",
    "'m",
    "'re",
    "'s",
    "'ve",
    "n't",
    "’d",
    "’ll",
    "’m",
    "’re",
    "’s",
    "’ve",
}


def _reconstruct_text_from_words(words: list[dict]) -> str:
    text_parts: list[str] = []
    for index, word in enumerate(words):
        token = str(word.get("word") or "")
        if not token:
            continue
        if index == 0:
            text_parts.append(token.lstrip())
            continue
        if token[:1].isspace():
            text_parts.append(token)
            continue

        stripped_token = token.strip()
        if not stripped_token:
            continue
        if stripped_token in _CONTRACTION_SUFFIXES or stripped_token.startswith(("'", "’")):
            text_parts.append(stripped_token)
            continue
        if _PUNCTUATION_ONLY_RE.match(stripped_token):
            text_parts.append(stripped_token)
            continue
        text_parts.append(f" {stripped_token}")

    return "".join(text_parts).strip()

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
        speaker_overlaps = {}
        
        for turn, _, label in speaker_turns.itertracks(yield_label=True):
            intersection = whisper_segment & turn
            if intersection:
                overlap_dur = intersection.duration
                if overlap_dur > 0:
                    speaker_overlaps[label] = speaker_overlaps.get(label, 0.0) + overlap_dur
        
        sorted_speakers = sorted(speaker_overlaps.items(), key=lambda x: x[1], reverse=True)
        
        segment_duration = max(0.0, end - start)
        if sorted_speakers:
            dominant_speaker = sorted_speakers[0][0]
            overlapping_speakers = [
                spk
                for spk, duration in sorted_speakers[1:]
                if _speaker_overlap_is_significant(
                    duration,
                    segment_duration,
                    min_duration_s=SEGMENT_OVERLAP_MIN_DURATION_S,
                    min_ratio=SEGMENT_OVERLAP_MIN_RATIO,
                )
            ]
        else:
            dominant_speaker = "UNKNOWN"
            overlapping_speakers = []
        
        final_segment = {
            "start": start,
            "end": end,
            "speaker": dominant_speaker,
            "overlapping_speakers": overlapping_speakers,
            "text": text
        }
        if seg.get("id"):
            final_segment["id"] = seg["id"]
        final_segments.append(final_segment)
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
        "overlapping_speakers": [],
        "text": "",
        "words": []
    }
    
    def get_speakers_for_range(start, end):
        speaker_overlaps = {}
        word_seg = Segment(start, end)
        for turn, _, label in speaker_turns.itertracks(yield_label=True):
            overlap_seg = word_seg & turn
            if overlap_seg:
                overlap_dur = overlap_seg.duration
                if overlap_dur > 0:
                    speaker_overlaps[label] = speaker_overlaps.get(label, 0.0) + overlap_dur
        sorted_speakers = sorted(speaker_overlaps.items(), key=lambda x: x[1], reverse=True)
        if not sorted_speakers:
            return ["UNKNOWN"]
        word_duration = max(0.0, end - start)
        primary_speaker = sorted_speakers[0][0]
        overlapping_speakers = [
            spk
            for spk, duration in sorted_speakers[1:]
            if _speaker_overlap_is_significant(
                duration,
                word_duration,
                min_duration_s=WORD_OVERLAP_MIN_DURATION_S,
                min_ratio=WORD_OVERLAP_MIN_RATIO,
            )
        ]
        return [primary_speaker, *overlapping_speakers]

    word_assignments = []
    for word_data in all_words:
        w_start = word_data['start']
        w_end = word_data['end']
        speakers = get_speakers_for_range(w_start, w_end)
        word_assignments.append(
            {
                "word": word_data,
                "speaker": speakers[0],
                "overlapping_speakers": speakers[1:],
            }
        )

    _smooth_isolated_word_speaker_flips(word_assignments)

    for i, assignment in enumerate(word_assignments):
        word_data = assignment["word"]
        w_start = word_data['start']
        w_end = word_data['end']
        w_text = word_data['word']
        speaker = assignment["speaker"]
        
        if i == 0:
            current_segment["start"] = w_start
            current_segment["speaker"] = speaker
            current_segment["overlapping_speakers"] = []
            # Strip leading space from first word if present
            current_segment["text"] = w_text.lstrip()
            current_segment["end"] = w_end
            current_segment["words"].append(word_data)
            continue
        
        gap = w_start - current_segment["end"]
        
        # Split if speaker changes OR gap > 1.0s
        if speaker != current_segment["speaker"] or gap > 1.0:
            final_segments.append(_with_word_source_public_ids(current_segment))
            current_segment = {
                "start": w_start,
                "end": w_end,
                "speaker": speaker,
                "overlapping_speakers": [],
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
            
    final_segments.append(_with_word_source_public_ids(current_segment))
    
    # Pass 2: Re-evaluate overlapping speakers for each consolidated segment using segment-level thresholds
    for segment in final_segments:
        start = segment["start"]
        end = segment["end"]
        segment_duration = max(0.0, end - start)
        seg_obj = Segment(start, end)
        
        speaker_overlaps = {}
        for turn, _, label in speaker_turns.itertracks(yield_label=True):
            intersection = seg_obj & turn
            if intersection:
                overlap_dur = intersection.duration
                if overlap_dur > 0:
                    speaker_overlaps[label] = speaker_overlaps.get(label, 0.0) + overlap_dur
                    
        sorted_speakers = sorted(speaker_overlaps.items(), key=lambda x: x[1], reverse=True)
        if sorted_speakers:
            segment["overlapping_speakers"] = [
                spk
                for spk, duration in sorted_speakers
                if spk != segment["speaker"] and _speaker_overlap_is_significant(
                    duration,
                    segment_duration,
                    min_duration_s=SEGMENT_OVERLAP_MIN_DURATION_S,
                    min_ratio=SEGMENT_OVERLAP_MIN_RATIO,
                )
            ]
        else:
            segment["overlapping_speakers"] = []
            
    # Log speaker distribution
    speaker_counts = {}
    for seg in final_segments:
        spk = seg['speaker']
        speaker_counts[spk] = speaker_counts.get(spk, 0) + 1
    logger.info(f"_combine_word_level: Created {len(final_segments)} segments with speaker distribution: {speaker_counts}")
    
    return final_segments 


def _with_word_source_public_ids(segment: dict) -> dict:
    next_segment = dict(segment)
    source_ids = sorted(
        {
            str(word.get("source_public_id") or "").strip()
            for word in next_segment.get("words", [])
            if str(word.get("source_public_id") or "").strip()
        }
    )
    if len(source_ids) == 1:
        next_segment["id"] = source_ids[0]
    elif len(source_ids) > 1:
        next_segment["live_utterance_ids"] = source_ids
    return next_segment


def _copy_consolidation_metadata(source_segment: dict, target_segment: dict) -> dict:
    next_segment = dict(target_segment)
    next_segment.update(_merge_consolidation_metadata([source_segment]))
    return next_segment


def _merge_consolidation_metadata(source_segments: list[dict]) -> dict:
    metadata: dict = {}
    if not source_segments:
        return metadata

    for key in ("speaker_manually_edited", "text_manually_edited"):
        if any(segment.get(key) is True for segment in source_segments):
            metadata[key] = True

    for key in ("speaker_state", "speaker_state_source", "live_source_speaker"):
        values = _ordered_metadata_values(segment.get(key) for segment in source_segments)
        if len(values) == 1:
            metadata[key] = values[0]
        elif key == "live_source_speaker" and values:
            metadata["live_source_speakers"] = values

    public_ids = _ordered_metadata_values(segment.get("id") for segment in source_segments)
    for segment in source_segments:
        for public_id in segment.get("live_utterance_ids") or []:
            public_id_value = str(public_id or "").strip()
            if public_id_value and public_id_value not in public_ids:
                public_ids.append(public_id_value)
    if len(public_ids) == 1:
        metadata["id"] = public_ids[0]
    elif public_ids:
        metadata["source_public_ids"] = public_ids

    alignments = [
        segment.get("live_reuse_alignment")
        for segment in source_segments
        if isinstance(segment.get("live_reuse_alignment"), dict)
    ]
    if len(alignments) == 1:
        metadata["live_reuse_alignment"] = alignments[0]
    elif alignments:
        matched_ids: list[str] = []
        rejection_reasons: list[str] = []
        for alignment in alignments:
            for public_id in alignment.get("matched_live_utterance_ids") or []:
                public_id_value = str(public_id or "").strip()
                if public_id_value and public_id_value not in matched_ids:
                    matched_ids.append(public_id_value)
            if alignment.get("status") == "rejected" and alignment.get("reason"):
                reason = str(alignment["reason"])
                if reason not in rejection_reasons:
                    rejection_reasons.append(reason)
        metadata["live_reuse_alignment"] = {
            "status": "merged",
            "matched_live_utterance_ids": matched_ids,
            "rejection_reasons": rejection_reasons,
            "source_alignments": alignments,
        }
    return metadata


def _ordered_metadata_values(values) -> list[str]:
    ordered_values: list[str] = []
    for value in values:
        value_text = str(value or "").strip()
        if value_text and value_text not in ordered_values:
            ordered_values.append(value_text)
    return ordered_values


def _speaker_overlap_is_significant(
    overlap_duration_s: float,
    target_duration_s: float,
    *,
    min_duration_s: float,
    min_ratio: float,
) -> bool:
    if overlap_duration_s < min_duration_s:
        return False
    if target_duration_s <= 0:
        return False
    return (overlap_duration_s / target_duration_s) >= min_ratio


def _smooth_isolated_word_speaker_flips(word_assignments: list[dict]) -> None:
    for index in range(1, len(word_assignments) - 1):
        previous_assignment = word_assignments[index - 1]
        assignment = word_assignments[index]
        next_assignment = word_assignments[index + 1]

        previous_speaker = previous_assignment["speaker"]
        current_speaker = assignment["speaker"]
        next_speaker = next_assignment["speaker"]

        if current_speaker in {previous_speaker, next_speaker, "UNKNOWN"}:
            continue
        if previous_speaker != next_speaker or previous_speaker == "UNKNOWN":
            continue
        if assignment.get("overlapping_speakers"):
            continue

        current_word = assignment["word"]
        previous_word = previous_assignment["word"]
        next_word = next_assignment["word"]
        current_duration = float(current_word["end"]) - float(current_word["start"])
        previous_gap = float(current_word["start"]) - float(previous_word["end"])
        next_gap = float(next_word["start"]) - float(current_word["end"])

        if current_duration > ISOLATED_WORD_FLIP_MAX_DURATION_S:
            continue
        if previous_gap > ISOLATED_WORD_FLIP_MAX_GAP_S or next_gap > ISOLATED_WORD_FLIP_MAX_GAP_S:
            continue

        assignment["speaker"] = previous_speaker

def consolidate_diarized_transcript(segments, min_duration_s: float = 0.1, max_duration_s: float = 10.0):
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
        curr_overlapping = set(curr.get('overlapping_speakers', []))
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
                # Treats max_duration_s as the ideal target (10s).
                
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
                    # Fallback: split at 10s if no candidate found in the 8-12s window
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

                chunk_text = _reconstruct_text_from_words(first_chunk_words)
                remainder_text = _reconstruct_text_from_words(remainder_words)
                
                split_end_time = first_chunk_words[-1]['end']
                
                split_segment = {
                    'start': curr_start,
                    'end': split_end_time,
                    'speaker': curr_speaker,
                    'overlapping_speakers': list(curr_overlapping),
                    'text': chunk_text,
                    'words': first_chunk_words
                }
                consolidated.append(_copy_consolidation_metadata(curr, split_segment))
                
                remainder_segment = {
                    'start': split_end_time,
                    'end': curr_end, 
                    'speaker': curr_speaker,
                    'overlapping_speakers': list(curr_overlapping),
                    'text': remainder_text,
                    'words': remainder_words
                }
                segments[i] = _copy_consolidation_metadata(curr, remainder_segment)
                logger.debug(f"Smart split at {split_end_time:.2f}s (Target: {max_duration_s}s). Sentence break: {best_sentence_idx != -1}")
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
                
                split_segment = {
                    'start': curr_start,
                    'end': split_end,
                    'speaker': curr_speaker,
                    'overlapping_speakers': list(curr_overlapping),
                    'text': chunk_text
                }
                consolidated.append(_copy_consolidation_metadata(curr, split_segment))
                
                # Now we set up the remainder as the new 'curr' for the next iteration of the loop
                # But we can't easily modify `segments` list in place safely or insert.
                # Easiest way: Update `segments[i]` to be the remainder and STAY on `i` (don't increment).
                # Infinite-loop safety: split_end > curr_start is guaranteed by max_duration_s > 0,
                # so the remainder shrinks on each iteration.
                
                remainder_segment = {
                    'start': split_end,
                    'end': curr_end, # Original end
                    'speaker': curr_speaker,
                    'overlapping_speakers': list(curr_overlapping),
                    'text': remainder_text
                }
                segments[i] = _copy_consolidation_metadata(curr, remainder_segment)
                # Continue loop WITHOUT incrementing `i` to process the remainder
                continue

        j = i + 1
        
        while j < n:
            next_seg = segments[j]
            next_words = next_seg.get('words', [])
            next_speaker = next_seg['speaker']
            next_overlapping = set(next_seg.get('overlapping_speakers', []))
            
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
                if next_speaker != curr_speaker:
                    curr_overlapping.add(next_speaker)
                curr_overlapping.update(next_overlapping)
                curr_end = potential_end
                curr_text += ' ' + next_seg['text']
                curr_words.extend(next_words)
                j += 1
            elif next_speaker == curr_speaker and abs(next_seg['start'] - curr_end) < 0.01 and next_overlapping == curr_overlapping:
                # Consecutive, same speaker, same overlapping set, no gap
                curr_end = next_seg['end'] # potential_end is next_seg['end'] here
                curr_text += ' ' + next_seg['text']
                curr_words.extend(next_words)
                j += 1
            else:
                break
                
        # Sort overlapping speakers for deterministic output and remove 'UNKNOWN'
        overlapping_list = sorted(list({spk for spk in curr_overlapping if spk != curr_speaker and spk != "UNKNOWN"}))

        # Add the consolidated segment only if its duration is long enough
        # OR if it's the only segment we have (to avoid dropping data for short recordings)
        # ... OR (end of stream AND no prepared segments).
        if (curr_end - curr_start) >= min_duration_s or (len(consolidated) == 0 and j == n):
            consolidated_segment = {
                'start': curr_start,
                'end': curr_end,
                'speaker': curr_speaker,
                'overlapping_speakers': overlapping_list,
                'text': curr_text.strip(),
                'words': curr_words
            }
            consolidated_segment.update(_merge_consolidation_metadata(segments[i:j]))
            consolidated.append(consolidated_segment)
        else:
            ov_str = f" (Overlap: {', '.join(overlapping_list)})" if overlapping_list else ""
            logger.info(f"Filtering out short consolidated segment: "
                        f"[{curr_start:.2f}s - {curr_end:.2f}s] Speaker {curr_speaker}{ov_str} "
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