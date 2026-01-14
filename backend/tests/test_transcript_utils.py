import pytest
from backend.utils.transcript_utils import consolidate_diarized_transcript

def test_consolidate_preserves_short_segments_if_only_one():
    """Test that a single short segment is NOT filtered out."""
    segments = [
        {'start': 0.0, 'end': 0.5, 'speaker': 'SPEAKER_00', 'text': 'Hello'}
    ]
    result = consolidate_diarized_transcript(segments, min_duration_s=1.0)
    assert len(result) == 1
    assert result[0]['text'] == 'Hello'

def test_consolidate_merges_consecutive_speakers():
    """Test that segments from the same speaker are merged."""
    segments = [
        {'start': 0.0, 'end': 2.0, 'speaker': 'SPEAKER_00', 'text': 'Hello'},
        {'start': 2.0, 'end': 4.0, 'speaker': 'SPEAKER_00', 'text': 'World'}
    ]
    result = consolidate_diarized_transcript(segments)
    assert len(result) == 1
    assert result[0]['text'] == 'Hello World'
    assert result[0]['end'] == 4.0

def test_consolidate_splits_long_segments():
    """Test that segments are split if they exceed max_duration_s."""
    # Assume max_duration_s=10.0 (default)
    
    # Create two segments that would normally merge to 12s
    segments = [
        {'start': 0.0, 'end': 8.0, 'speaker': 'SPEAKER_00', 'text': 'Part 1'},
        {'start': 8.0, 'end': 12.0, 'speaker': 'SPEAKER_00', 'text': 'Part 2'}
    ]
    
    result = consolidate_diarized_transcript(segments)
    
    # Needs 2 segments because 12s > 10s
    assert len(result) == 2
    
    # First segment: 0-8s
    assert result[0]['start'] == 0.0
    assert result[0]['end'] == 8.0
    assert result[0]['text'] == 'Part 1'
    
    # Second segment: 8-12s
    assert result[1]['start'] == 8.0
    assert result[1]['end'] == 12.0
    assert result[1]['text'] == 'Part 2'

def test_consolidate_splits_at_segment_boundary():
    """Test split behavior when multiple segments accumulate."""
    segments = [
        {'start': 0.0, 'end': 4.0, 'speaker': 'A', 'text': '1'},
        {'start': 4.0, 'end': 8.0, 'speaker': 'A', 'text': '2'},
        {'start': 8.0, 'end': 12.0, 'speaker': 'A', 'text': '3'}, # This should cause split (End would match 12 > 10)
        {'start': 12.0, 'end': 15.0, 'speaker': 'A', 'text': '4'}
    ]
    
    result = consolidate_diarized_transcript(segments)
    
    # Expected:
    # Seg 1: 0-8 (1+2) -> 8s duration. Adding 3 (end=12) would make 12s > 10s. So split.
    # Seg 2: 8-12 (3) -> 4s duration. Adding 4 (end=15) -> 8-15 = 7s. Merge.
    
    assert len(result) == 2
    
    assert result[0]['start'] == 0.0
    assert result[0]['end'] == 8.0
    assert result[0]['text'] == '1 2'
    
    assert result[1]['start'] == 8.0
    assert result[1]['end'] == 15.0
    assert result[1]['text'] == '3 4'

def test_regression_orphan_drop():
    """
    Test that a segment tail < 1.0s is NOT dropped after a forced split,
    preventing data loss and misalignment.
    """
    segments = [
        {'start': 0.0, 'end': 9.5, 'speaker': 'A', 'text': 'Long speech part 1'},
        {'start': 9.5, 'end': 10.2, 'speaker': 'A', 'text': 'end.'}
    ]
    
    # Use defaults (min=0.5, max=10.0)
    result = consolidate_diarized_transcript(segments)
    
    # Should have 2 segments:
    # 1. 0.0 - 9.5 "Long speech part 1"
    # 2. 9.5 - 10.2 "end." (0.7s duration, which is > 0.5 default)
    assert len(result) == 2
    assert result[1]['text'] == 'end.'
    assert result[1]['end'] == 10.2

def test_regression_giant_segment_split():
    """
    Test that a single pre-existing large segment is split by the logic
    before processing key logic.
    """
    # 35 second segment
    segments = [
        {'start': 0.0, 'end': 35.0, 'speaker': 'A', 'text': 'This is a very long segment ' * 10}
    ]
    
    # Should be split into 10s chunks: 0-10, 10-20, 20-30, 30-35
    result = consolidate_diarized_transcript(segments)
    
    assert len(result) >= 4
    
    # Check first chunk
    assert result[0]['end'] - result[0]['start'] == 10.0
    assert result[0]['start'] == 0.0
    assert result[0]['end'] == 10.0
    
    # Check last chunk
    last = result[-1]
    assert last['start'] == 30.0
    assert last['end'] == 35.0
