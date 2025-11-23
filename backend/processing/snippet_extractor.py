import logging
from typing import List, Dict
from ..db import database

logger = logging.getLogger(__name__)

def select_clearest_segment(segments: List[Dict], min_length: float = 4.0) -> Dict:
    """
    Select the 'clearest' segment for a speaker: prefer non-overlapping, mid-recording, >= min_length seconds.
    If none found, use the longest available segment.
    """
    if not segments:
        return None
    # Prefer segments >= min_length, sort by duration descending
    long_enough = [s for s in segments if (s['end_time'] - s['start_time']) >= min_length]
    if long_enough:
        # Prefer the one closest to the middle of the recording
        mid = sum([(s['start_time'] + s['end_time'])/2 for s in long_enough]) / len(long_enough)
        long_enough.sort(key=lambda s: abs(((s['start_time'] + s['end_time'])/2) - mid))
        return long_enough[0]
    # Fallback: use the longest segment
    segments.sort(key=lambda s: (s['end_time'] - s['start_time']), reverse=True)
    return segments[0] 