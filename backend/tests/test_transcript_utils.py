import os
import subprocess
import sys
from pathlib import Path

import pytest
from pyannote.core import Segment

from backend.utils.transcript_utils import (
    combine_transcription_diarization,
    consolidate_diarized_transcript,
)


class FakeDiarization:
    def __init__(self, turns):
        self.turns = turns

    def __bool__(self):
        return True

    def itertracks(self, yield_label=False):
        for start, end, label in self.turns:
            if yield_label:
                yield Segment(start, end), None, label
            else:
                yield Segment(start, end), None


def test_consolidate_preserves_short_segments_if_only_one():
    """Test that a single short segment is NOT filtered out."""
    segments = [{"start": 0.0, "end": 0.5, "speaker": "SPEAKER_00", "text": "Hello"}]
    result = consolidate_diarized_transcript(segments, min_duration_s=1.0)
    assert len(result) == 1
    assert result[0]["text"] == "Hello"


def test_consolidate_merges_consecutive_speakers():
    """Test that segments from the same speaker are merged."""
    segments = [
        {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00", "text": "Hello"},
        {"start": 2.0, "end": 4.0, "speaker": "SPEAKER_00", "text": "World"},
    ]
    result = consolidate_diarized_transcript(segments)
    assert len(result) == 1
    assert result[0]["text"] == "Hello World"
    assert result[0]["end"] == 4.0


def test_consolidate_preserves_live_reuse_alignment_metadata():
    segments = [
        {
            "id": "live-1",
            "start": 0.0,
            "end": 1.0,
            "speaker": "LIVE_01",
            "text": "Hello",
            "live_reuse_alignment": {
                "status": "matched",
                "reason": "time_overlap",
                "matched_live_utterance_ids": ["live-1"],
            },
        },
        {
            "id": "live-2",
            "start": 1.0,
            "end": 2.0,
            "speaker": "LIVE_01",
            "text": "World",
            "text_manually_edited": True,
            "live_reuse_alignment": {
                "status": "matched",
                "reason": "time_overlap",
                "matched_live_utterance_ids": ["live-2"],
                "manual_override_reasons": ["manual_text_locked"],
            },
        },
    ]

    result = consolidate_diarized_transcript(segments)

    assert len(result) == 1
    assert result[0]["text"] == "Hello World"
    assert result[0]["text_manually_edited"] is True
    assert result[0]["source_public_ids"] == ["live-1", "live-2"]
    assert result[0]["live_reuse_alignment"]["status"] == "merged"
    assert result[0]["live_reuse_alignment"]["matched_live_utterance_ids"] == [
        "live-1",
        "live-2",
    ]


def test_consolidate_splits_long_segments():
    """Test that segments are split if they exceed max_duration_s."""
    # Assume max_duration_s=10.0 (default)

    # Create two segments that would normally merge to 12s
    segments = [
        {"start": 0.0, "end": 8.0, "speaker": "SPEAKER_00", "text": "Part 1"},
        {"start": 8.0, "end": 12.0, "speaker": "SPEAKER_00", "text": "Part 2"},
    ]

    result = consolidate_diarized_transcript(segments)

    # Needs 2 segments because 12s > 10s
    assert len(result) == 2

    # First segment: 0-8s
    assert result[0]["start"] == 0.0
    assert result[0]["end"] == 8.0
    assert result[0]["text"] == "Part 1"

    # Second segment: 8-12s
    assert result[1]["start"] == 8.0
    assert result[1]["end"] == 12.0
    assert result[1]["text"] == "Part 2"


def test_consolidate_splits_at_segment_boundary():
    """Test split behavior when multiple segments accumulate."""
    segments = [
        {"start": 0.0, "end": 4.0, "speaker": "A", "text": "1"},
        {"start": 4.0, "end": 8.0, "speaker": "A", "text": "2"},
        {
            "start": 8.0,
            "end": 12.0,
            "speaker": "A",
            "text": "3",
        },  # This should cause split (End would match 12 > 10)
        {"start": 12.0, "end": 15.0, "speaker": "A", "text": "4"},
    ]

    result = consolidate_diarized_transcript(segments)

    # Expected:
    # Seg 1: 0-8 (1+2) -> 8s duration. Adding 3 (end=12) would make 12s > 10s. So split.
    # Seg 2: 8-12 (3) -> 4s duration. Adding 4 (end=15) -> 8-15 = 7s. Merge.

    assert len(result) == 2

    assert result[0]["start"] == 0.0
    assert result[0]["end"] == 8.0
    assert result[0]["text"] == "1 2"

    assert result[1]["start"] == 8.0
    assert result[1]["end"] == 15.0
    assert result[1]["text"] == "3 4"


def test_regression_orphan_drop():
    """
    Test that a segment tail < 1.0s is NOT dropped after a forced split,
    preventing data loss and misalignment.
    """
    segments = [
        {"start": 0.0, "end": 9.5, "speaker": "A", "text": "Long speech part 1"},
        {"start": 9.5, "end": 10.2, "speaker": "A", "text": "end."},
    ]

    # Use defaults (min=0.5, max=10.0)
    result = consolidate_diarized_transcript(segments)

    # Should have 2 segments:
    # 1. 0.0 - 9.5 "Long speech part 1"
    # 2. 9.5 - 10.2 "end." (0.7s duration, which is > 0.5 default)
    assert len(result) == 2
    assert result[1]["text"] == "end."
    assert result[1]["end"] == 10.2


def test_regression_giant_segment_split():
    """
    Test that a single pre-existing large segment is split by the logic
    before processing key logic.
    """
    # 35 second segment
    segments = [
        {
            "start": 0.0,
            "end": 35.0,
            "speaker": "A",
            "text": "This is a very long segment " * 10,
        }
    ]

    # Should be split into 10s chunks: 0-10, 10-20, 20-30, 30-35
    result = consolidate_diarized_transcript(segments)

    assert len(result) >= 4

    # Check first chunk
    assert result[0]["end"] - result[0]["start"] == 10.0
    assert result[0]["start"] == 0.0
    assert result[0]["end"] == 10.0

    # Check last chunk
    last = result[-1]
    assert last["start"] == 30.0
    assert last["end"] == 35.0


def test_long_word_split_reconstructs_spaces_without_leading_space_tokens():
    segments = [
        {
            "start": 0.0,
            "end": 12.0,
            "speaker": "A",
            "text": "one two three four five six seven eight nine ten eleven twelve",
            "words": [
                {"start": 0.0, "end": 1.0, "word": "one"},
                {"start": 1.0, "end": 2.0, "word": "two"},
                {"start": 2.0, "end": 3.0, "word": "three"},
                {"start": 3.0, "end": 4.0, "word": "four"},
                {"start": 4.0, "end": 5.0, "word": "five"},
                {"start": 5.0, "end": 6.0, "word": "six"},
                {"start": 6.0, "end": 7.0, "word": "seven"},
                {"start": 7.0, "end": 8.0, "word": "eight"},
                {"start": 8.0, "end": 9.0, "word": "nine"},
                {"start": 9.0, "end": 10.0, "word": "ten"},
                {"start": 10.0, "end": 11.0, "word": "eleven"},
                {"start": 11.0, "end": 12.0, "word": "twelve"},
            ],
        }
    ]

    result = consolidate_diarized_transcript(segments)

    assert len(result) == 2
    assert result[0]["text"] == "one two three four five six seven eight nine ten"
    assert result[1]["text"] == "eleven twelve"


def test_word_level_combination_ignores_tiny_secondary_overlap():
    transcription = {
        "segments": [
            {
                "start": 0.0,
                "end": 0.5,
                "text": " hello",
                "words": [{"start": 0.0, "end": 0.5, "word": " hello"}],
            }
        ]
    }
    diarization = FakeDiarization(
        [
            (0.0, 0.5, "SPEAKER_00"),
            (0.48, 0.5, "SPEAKER_01"),
        ]
    )

    result = combine_transcription_diarization(transcription, diarization)

    assert result == [
        {
            "start": 0.0,
            "end": 0.5,
            "speaker": "SPEAKER_00",
            "overlapping_speakers": [],
            "text": "hello",
            "words": [{"start": 0.0, "end": 0.5, "word": " hello"}],
        }
    ]


def test_word_level_combination_smooths_isolated_speaker_flip():
    transcription = {
        "segments": [
            {
                "start": 0.0,
                "end": 0.9,
                "text": " one two three",
                "words": [
                    {"start": 0.0, "end": 0.3, "word": " one"},
                    {"start": 0.3, "end": 0.6, "word": " two"},
                    {"start": 0.6, "end": 0.9, "word": " three"},
                ],
            }
        ]
    }
    diarization = FakeDiarization(
        [
            (0.0, 0.3, "SPEAKER_00"),
            (0.3, 0.6, "SPEAKER_01"),
            (0.6, 0.9, "SPEAKER_00"),
        ]
    )

    result = combine_transcription_diarization(transcription, diarization)

    assert len(result) == 1
    assert result[0]["speaker"] == "SPEAKER_00"
    assert result[0]["text"] == "one two three"


def _flip_transcription(gap_s: float = 0.0) -> dict:
    """one (A), two (B, 0.3 s), three (A), ``gap_s`` apart."""
    two_start = 0.3 + gap_s
    three_start = two_start + 0.3 + gap_s
    return {
        "segments": [
            {
                "start": 0.0,
                "end": three_start + 0.3,
                "text": " one two three",
                "words": [
                    {"start": 0.0, "end": 0.3, "word": " one"},
                    {"start": two_start, "end": two_start + 0.3, "word": " two"},
                    {"start": three_start, "end": three_start + 0.3, "word": " three"},
                ],
            }
        ]
    }


def _flip_diarization(gap_s: float = 0.0) -> FakeDiarization:
    two_start = 0.3 + gap_s
    three_start = two_start + 0.3 + gap_s
    return FakeDiarization(
        [
            (0.0, 0.3, "SPEAKER_00"),
            (two_start, two_start + 0.3, "SPEAKER_01"),
            (three_start, three_start + 0.3, "SPEAKER_00"),
        ]
    )


@pytest.mark.parametrize(
    "config",
    [
        # Smoothing off.
        {"word_flip_max_duration_s": 0},
        # The 0.3 s word is longer than the limit.
        {"word_flip_max_duration_s": 0.2},
    ],
)
def test_word_flip_limits_from_config_keep_the_brief_speaker(config):
    result = combine_transcription_diarization(
        _flip_transcription(), _flip_diarization(), config=config
    )

    assert [seg["speaker"] for seg in result] == [
        "SPEAKER_00",
        "SPEAKER_01",
        "SPEAKER_00",
    ]


def test_word_flip_gap_limit_from_config():
    transcription = _flip_transcription(gap_s=0.1)
    diarization = _flip_diarization(gap_s=0.1)

    smoothed = combine_transcription_diarization(transcription, diarization)
    kept = combine_transcription_diarization(
        transcription, diarization, config={"word_flip_max_gap_s": 0.05}
    )

    assert [seg["speaker"] for seg in smoothed] == ["SPEAKER_00"]
    assert len(kept) == 3


def test_word_flip_unusable_limit_keeps_the_default():
    result = combine_transcription_diarization(
        _flip_transcription(),
        _flip_diarization(),
        config={"word_flip_max_duration_s": -1},
    )

    assert [seg["speaker"] for seg in result] == ["SPEAKER_00"]


@pytest.mark.parametrize(("max_duration_s", "expected"), [(0.45, "A"), (0, "B")])
def test_zero_word_flip_limit_leaves_even_a_zero_length_word(max_duration_s, expected):
    """A zero-length word is no longer than a limit of 0, so only the explicit
    off switch keeps it from being relabelled. combine_transcription_diarization
    labels such a word UNKNOWN, so this drives the smoothing step directly."""
    from backend.utils.transcript_utils import _smooth_isolated_word_speaker_flips

    def _assignment(start: float, end: float, speaker: str) -> dict:
        word = {"start": start, "end": end, "word": " w"}
        return {"word": word, "speaker": speaker, "overlapping_speakers": []}

    assignments = [
        _assignment(0.0, 0.3, "A"),
        _assignment(0.3, 0.3, "B"),
        _assignment(0.3, 0.6, "A"),
    ]

    _smooth_isolated_word_speaker_flips(assignments, max_duration_s=max_duration_s)

    assert assignments[1]["speaker"] == expected


def test_segment_level_combination_ignores_tiny_secondary_overlap():
    transcription = {
        "segments": [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "hello there",
            }
        ]
    }
    diarization = FakeDiarization(
        [
            (0.0, 2.0, "SPEAKER_00"),
            (1.9, 2.0, "SPEAKER_01"),
        ]
    )

    result = combine_transcription_diarization(transcription, diarization)

    assert result == [
        {
            "start": 0.0,
            "end": 2.0,
            "speaker": "SPEAKER_00",
            "overlapping_speakers": [],
            "text": "hello there",
        }
    ]


def _spans(segments):
    return [(seg["start"], seg["end"], seg["speaker"], seg["text"]) for seg in segments]


def whisper_transcription_with_a_wordless_segment() -> dict:
    """openai-whisper's shape: integer segment ids, and one segment whose words
    could not be aligned, so it carries an empty "words" list."""
    return {
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 1.0,
                "text": " Let's start.",
                "words": [
                    {"start": 0.0, "end": 0.5, "word": " Let's"},
                    {"start": 0.5, "end": 1.0, "word": " start."},
                ],
            },
            {
                "id": 1,
                "start": 1.5,
                "end": 3.0,
                "text": " Sorry, I was muted.",
                "words": [],
            },
            {
                "id": 2,
                "start": 3.5,
                "end": 4.5,
                "text": " No problem.",
                "words": [
                    {"start": 3.5, "end": 4.0, "word": " No"},
                    {"start": 4.0, "end": 4.5, "word": " problem."},
                ],
            },
        ]
    }


WORDLESS_SEGMENT_TURNS = [
    (0.0, 1.2, "SPEAKER_00"),
    (1.4, 3.1, "SPEAKER_01"),
    (3.4, 4.6, "SPEAKER_00"),
]


def test_combination_keeps_text_of_a_segment_without_words():
    result = combine_transcription_diarization(
        whisper_transcription_with_a_wordless_segment(),
        FakeDiarization(WORDLESS_SEGMENT_TURNS),
    )

    assert _spans(result) == [
        (0.0, 1.0, "SPEAKER_00", "Let's start."),
        (1.5, 3.0, "SPEAKER_01", "Sorry, I was muted."),
        (3.5, 4.5, "SPEAKER_00", "No problem."),
    ]
    assert "words" not in result[1]
    # Whisper's segment index is not an utterance id; finalize would persist
    # it as a public_id, which is unique across recordings.
    assert "id" not in result[1]


def test_segment_level_combination_keeps_only_string_ids():
    transcription = {
        "segments": [
            {"id": 3, "start": 0.0, "end": 1.0, "text": " Engine index."},
            {"id": "live-7", "start": 1.0, "end": 2.0, "text": " Live reuse."},
        ]
    }
    diarization = FakeDiarization([(0.0, 2.0, "SPEAKER_00")])

    result = combine_transcription_diarization(transcription, diarization)

    assert [seg.get("id") for seg in result] == [None, "live-7"]


def test_combination_keeps_every_segment_when_none_has_words():
    # Whisper asked for word timestamps and aligned none: on the old
    # first-segment check this collapsed to one empty UNKNOWN segment.
    transcription = {
        "segments": [
            {"id": 0, "start": 0.0, "end": 2.0, "text": " Hello there.", "words": []},
            {"id": 1, "start": 2.0, "end": 4.0, "text": " Over here.", "words": []},
        ]
    }
    diarization = FakeDiarization([(0.0, 2.0, "SPEAKER_00"), (2.0, 4.0, "SPEAKER_01")])

    result = combine_transcription_diarization(transcription, diarization)

    assert _spans(result) == [
        (0.0, 2.0, "SPEAKER_00", "Hello there."),
        (2.0, 4.0, "SPEAKER_01", "Over here."),
    ]


def test_consolidation_merges_a_wordless_segment_with_its_neighbours():
    transcription = {
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 1.0,
                "text": " Okay so",
                "words": [
                    {"start": 0.0, "end": 0.5, "word": " Okay"},
                    {"start": 0.5, "end": 1.0, "word": " so"},
                ],
            },
            {"id": 1, "start": 1.0, "end": 2.0, "text": " ...", "words": []},
            {
                "id": 2,
                "start": 2.0,
                "end": 3.0,
                "text": " moving on",
                "words": [
                    {"start": 2.0, "end": 2.5, "word": " moving"},
                    {"start": 2.5, "end": 3.0, "word": " on"},
                ],
            },
        ]
    }
    diarization = FakeDiarization([(0.0, 3.0, "SPEAKER_00")])

    result = consolidate_diarized_transcript(
        combine_transcription_diarization(transcription, diarization)
    )

    assert _spans(result) == [(0.0, 3.0, "SPEAKER_00", "Okay so ... moving on")]
    assert "id" not in result[0]


def test_combination_aligns_words_after_a_segment_without_them():
    # A chunked onnx-asr run can return one window without token timings;
    # later windows still carry words and must be aligned word by word.
    transcription = {
        "segments": [
            {"start": 0.0, "end": 2.0, "text": " Good morning everyone."},
            {
                "start": 2.5,
                "end": 4.0,
                "text": " Thanks. Sure, go ahead.",
                "words": [
                    {"start": 2.5, "end": 3.0, "word": " Thanks."},
                    {"start": 3.1, "end": 3.5, "word": " Sure,"},
                    {"start": 3.5, "end": 3.7, "word": " go"},
                    {"start": 3.7, "end": 4.0, "word": " ahead."},
                ],
            },
        ]
    }
    diarization = FakeDiarization(
        [(0.0, 3.05, "SPEAKER_00"), (3.05, 4.0, "SPEAKER_01")]
    )

    result = combine_transcription_diarization(transcription, diarization)

    assert _spans(result) == [
        (0.0, 2.0, "SPEAKER_00", "Good morning everyone."),
        (2.5, 3.0, "SPEAKER_00", "Thanks."),
        (3.1, 4.0, "SPEAKER_01", "Sure, go ahead."),
    ]


ZERO_LENGTH_TURNS = [(0.0, 1.0, "S0"), (1.0, 2.0, "S1"), (3.0, 4.0, "S2")]


@pytest.mark.parametrize(
    ("instant", "turns", "speaker"),
    [
        (0.5, ZERO_LENGTH_TURNS, "S0"),  # inside a turn
        (1.0, ZERO_LENGTH_TURNS, "S1"),  # shared edge: the turn starting there
        (2.0, ZERO_LENGTH_TURNS, "S1"),  # a turn's end, no turn starting there
        (2.5, ZERO_LENGTH_TURNS, "UNKNOWN"),  # gap between turns
        (5.0, ZERO_LENGTH_TURNS, "UNKNOWN"),  # outside every turn
        (1.5, [(0.0, 2.0, "S0"), (1.0, 3.0, "S1")], "S0"),  # overlap: first turn
    ],
)
def test_zero_length_word_takes_the_speaker_of_the_turn_containing_it(
    instant, turns, speaker
):
    word = {"start": instant, "end": instant, "word": " ship"}
    transcription = {
        "segments": [
            {"start": instant, "end": instant, "text": " ship", "words": [word]}
        ]
    }

    result = combine_transcription_diarization(transcription, FakeDiarization(turns))

    assert [(seg["speaker"], seg["text"]) for seg in result] == [(speaker, "ship")]


def test_zero_length_word_mid_sentence_survives_consolidation():
    # Whisper rounds word timings to 0.01 s, so a word can start and end at once.
    words = [(0.0, 0.5, " we"), (0.5, 1.0, " will"), (1.0, 1.0, " ship")]
    words += [(1.0, 2.0, " it"), (2.0, 3.0, " today")]
    transcription = {
        "segments": [
            {
                "start": 0.0,
                "end": 3.0,
                "text": " we will ship it today",
                "words": [{"start": s, "end": e, "word": w} for s, e, w in words],
            }
        ]
    }
    diarization = FakeDiarization([(0.0, 3.0, "SPEAKER_00")])

    result = consolidate_diarized_transcript(
        combine_transcription_diarization(transcription, diarization)
    )

    assert _spans(result) == [(0.0, 3.0, "SPEAKER_00", "we will ship it today")]


def _segment(start, end, speaker, text):
    return {"start": start, "end": end, "speaker": speaker, "text": text}


def test_consolidate_folds_a_short_segment_into_the_same_speaker_neighbour():
    segments = [
        _segment(0.0, 2.0, "S0", "First point."),
        _segment(2.0, 2.05, "S1", "Uh"),
        _segment(2.5, 4.0, "S1", "second point."),
    ]

    result = consolidate_diarized_transcript(segments)

    assert _spans(result) == [
        (0.0, 2.0, "S0", "First point."),
        (2.0, 4.0, "S1", "Uh second point."),
    ]


def test_consolidate_folds_a_short_segment_into_the_nearer_neighbour():
    segments = [
        _segment(0.0, 2.0, "S0", "First point"),
        _segment(2.0, 2.05, "UNKNOWN", "too."),
        _segment(3.0, 4.0, "S1", "Second point."),
    ]

    result = consolidate_diarized_transcript(segments)

    assert _spans(result) == [
        (0.0, 2.05, "S0", "First point too."),
        (3.0, 4.0, "S1", "Second point."),
    ]


def test_consolidate_keeps_text_order_across_consecutive_short_segments():
    # "x" prefers the later neighbour (same speaker); "y" would prefer the
    # earlier one, but going there would put it before "x".
    segments = [
        _segment(0.0, 1.0, "S0", "a"),
        _segment(1.0, 1.05, "S1", "x"),
        _segment(1.05, 1.1, "S0", "y"),
        _segment(1.1, 2.0, "S1", "b"),
    ]

    result = consolidate_diarized_transcript(segments)

    assert _spans(result) == [(0.0, 1.0, "S0", "a"), (1.0, 2.0, "S1", "x y b")]


def test_consolidate_keeps_an_isolated_short_segment_on_its_own():
    # Folding across seconds of silence would stretch the neighbour over it.
    segments = [
        _segment(0.0, 2.0, "S0", "First point."),
        _segment(5.0, 5.05, "S1", "Hm."),
        _segment(8.0, 9.0, "S0", "Second point."),
        _segment(9.0, 9.0, "S0", ""),
    ]

    result = consolidate_diarized_transcript(segments)

    assert _spans(result) == [
        (0.0, 2.0, "S0", "First point."),
        (5.0, 5.05, "S1", "Hm."),
        (8.0, 9.0, "S0", "Second point."),
    ]


def test_word_with_float_noise_duration_is_looked_up_as_an_instant():
    # pyannote treats a Segment this short as empty, so it overlaps no turn.
    word = {"start": 0.5, "end": 0.5 + 1e-9, "word": " ship"}
    transcription = {
        "segments": [{"start": 0.5, "end": 0.5, "text": " ship", "words": [word]}]
    }

    result = combine_transcription_diarization(
        transcription, FakeDiarization(ZERO_LENGTH_TURNS)
    )

    assert [seg["speaker"] for seg in result] == ["S0"]


@pytest.mark.parametrize(
    "segments",
    [
        # Folding back would grow the 10 s segment past the cap.
        [
            _segment(0.0, 10.0, "S0", "Long turn."),
            _segment(10.0, 10.05, "S0", "Hm."),
            _segment(12.0, 13.0, "S0", "Later."),
        ],
        # Folding forward would, too.
        [
            _segment(0.0, 0.05, "S0", "Hm."),
            _segment(0.05, 10.05, "S0", "Long turn."),
        ],
    ],
)
def test_consolidate_never_folds_a_segment_past_the_maximum_duration(segments):
    expected = _spans(segments)

    result = consolidate_diarized_transcript([dict(seg) for seg in segments])

    assert _spans(result) == expected


def test_consolidate_folds_into_the_earlier_neighbour_on_a_tie():
    segments = [
        _segment(0.0, 2.0, "S0", "First"),
        _segment(2.0, 2.05, "S2", "uh"),
        _segment(2.05, 4.0, "S1", "Second."),
    ]

    result = consolidate_diarized_transcript(segments)

    assert _spans(result) == [
        (0.0, 2.05, "S0", "First uh"),
        (2.05, 4.0, "S1", "Second."),
    ]


def test_consolidate_merges_one_turn_split_only_by_a_folded_segment():
    segments = [
        _segment(0.0, 2.0, "S0", "So the"),
        _segment(2.0, 2.05, "S1", "uh"),
        _segment(2.05, 4.0, "S0", "plan is set."),
    ]

    result = consolidate_diarized_transcript(segments)

    assert _spans(result) == [(0.0, 4.0, "S0", "So the uh plan is set.")]


def test_consolidate_fold_merges_metadata_as_a_merge_does():
    def pair(fragment_start):
        return [
            {**_segment(0.0, 2.0, "S0", "a"), "id": "live-a"},
            {
                **_segment(fragment_start, fragment_start + 0.05, "S0", "x"),
                "id": "live-x",
                "text_manually_edited": True,
            },
            _segment(5.0, 6.0, "S1", "b"),
        ]

    merged = consolidate_diarized_transcript(pair(2.0))[0]
    folded = consolidate_diarized_transcript(pair(2.05))[0]

    assert folded["source_public_ids"] == ["live-a", "live-x"]
    assert folded["text_manually_edited"] is True
    assert "id" not in folded
    metadata_keys = {"id", "source_public_ids", "text_manually_edited"}
    assert {k: v for k, v in folded.items() if k in metadata_keys} == {
        k: v for k, v in merged.items() if k in metadata_keys
    }


def test_consolidate_drops_a_short_segment_without_text():
    segments = [
        _segment(0.0, 2.0, "S0", "First point."),
        _segment(5.0, 5.05, "S1", ""),
        _segment(8.0, 9.0, "S0", "Second point."),
    ]

    result = consolidate_diarized_transcript(segments)

    assert [seg["text"] for seg in result] == ["First point.", "Second point."]


def _with_overlap(segment, overlapping):
    return {**segment, "overlapping_speakers": overlapping}


@pytest.mark.parametrize(
    "segments",
    [
        # Different overlapping speakers on the two sides.
        [
            _with_overlap(_segment(0.0, 2.0, "S0", "a"), ["S2"]),
            _segment(2.0, 2.05, "S1", "uh"),
            _segment(2.05, 4.0, "S0", "b"),
        ],
        # Joined, the line would run past 10 s.
        [
            _segment(0.0, 5.0, "S0", "a"),
            _segment(5.0, 5.05, "S1", "uh"),
            _segment(5.05, 11.0, "S0", "b"),
        ],
        # A 0.05 s pause after the fragment.
        [
            _segment(0.0, 2.0, "S0", "a"),
            _segment(2.0, 2.05, "S1", "uh"),
            _segment(2.1, 4.0, "S0", "b"),
        ],
        # A 0.05 s pause before two fragments that fold back and close the gap.
        [
            _segment(0.0, 2.0, "S0", "a"),
            _segment(2.05, 2.1, "S1", "uh"),
            _segment(2.1, 2.15, "S2", "hm"),
            _segment(2.15, 4.0, "S0", "b"),
        ],
    ],
)
def test_consolidate_keeps_neighbours_apart_unless_the_fold_tiles_one_turn(segments):
    result = consolidate_diarized_transcript([dict(seg) for seg in segments])

    assert len(result) == 2
    assert result[-1]["text"] == "b"


REJOIN_ACROSS_A_SPLIT = """
import json
from backend.utils.transcript_utils import consolidate_diarized_transcript

def seg(start, end, text, overlapping, words=None):
    out = {"start": start, "end": end, "speaker": "S0", "text": text,
           "overlapping_speakers": overlapping}
    if words:
        out["words"] = words
    return out

segments = [
    seg(0.0, 1.0, "a", ["S2", "S3"]),
    {**seg(1.0, 1.05, "uh", []), "speaker": "S1"},
    seg(1.05, 12.05, "one. two", ["S3", "S2"], [
        {"start": 1.05, "end": 9.05, "word": " one."},
        {"start": 9.05, "end": 12.05, "word": " two"},
    ]),
]
result = consolidate_diarized_transcript(segments)
print(json.dumps([(s["start"], s["end"], s["overlapping_speakers"]) for s in result]))
"""


def test_consolidate_output_does_not_depend_on_the_hash_seed():
    # A split chunk builds its overlapping speakers from a set, whose order
    # follows PYTHONHASHSEED; the rejoin must not depend on that order.
    repo_root = Path(__file__).resolve().parents[2]
    outputs = {
        subprocess.run(
            [sys.executable, "-c", REJOIN_ACROSS_A_SPLIT],
            cwd=repo_root,
            env={**os.environ, "PYTHONHASHSEED": str(seed)},
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for seed in range(8)
    }

    assert len(outputs) == 1
