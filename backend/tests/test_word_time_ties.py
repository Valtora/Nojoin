"""Consolidation thresholds that Parakeet word times can meet exactly.

Token starts lie on onnx-asr's 80 ms grid and a capped word ends at its start
plus the cap, so a span or gap built from word times can equal a threshold.
Each time here is computed as the mapper and _shift_segment compute it, at
every frame of a window and at two window offsets, so float rounding lands it
on both sides of the threshold somewhere in the sweep. A value equal to its
threshold must be treated the same at every frame.
"""

from __future__ import annotations

import pytest

from backend.utils.transcript_utils import consolidate_diarized_transcript

OFFSETS_S = [0.0, 7000.0]
FRAMES = range(30, 230)


def _t(frame: int, offset_s: float, cap_s: float = 0.0) -> float:
    """A word time: a token's frame, plus the cap for a capped end, then the
    window's offset, summed in the mapper's order."""
    return (frame * 0.08 + cap_s) + offset_s


def _segment(speaker: str, words: list[tuple[float, float, str]]) -> dict:
    return {
        "start": words[0][0],
        "end": words[-1][1],
        "speaker": speaker,
        "overlapping_speakers": [],
        "text": "".join(text for _, _, text in words).strip(),
        "words": [{"start": s, "end": e, "word": text} for s, e, text in words],
    }


def _frames_where(build, check) -> list[int]:
    """Frames at which consolidating build(frame) fails check, per offset."""
    return [
        (offset_s, frame)
        for offset_s in OFFSETS_S
        for frame in FRAMES
        if not check(consolidate_diarized_transcript(build(frame, offset_s)))
    ]


def _word_counts(result: list[dict]) -> list[int]:
    return [len(segment["words"]) for segment in result]


def test_a_segment_of_exactly_the_maximum_is_not_split():
    # Words 4 frames apart, each ending at the next start, the last capped:
    # 120 frames plus 0.4 s is 10.0 s. A split would take the sentence end at
    # 8.96 s.
    def build(f, off):
        words = [
            (_t(f + 4 * i, off), _t(f + 4 * i + 4, off), " w." if i == 27 else " w")
            for i in range(30)
        ]
        words.append((_t(f + 120, off), _t(f + 120, off, 0.4), " w"))
        return [_segment("A", words)]

    assert _frames_where(build, lambda r: len(r) == 1) == []


def test_two_segments_spanning_exactly_the_maximum_merge():
    def build(f, off):
        return [
            _segment("A", [(_t(f, off), _t(f + 60, off), " one")]),
            _segment("A", [(_t(f + 60, off), _t(f + 120, off, 0.4), " two")]),
        ]

    assert _frames_where(build, lambda r: len(r) == 1) == []


def test_a_word_ending_exactly_8_s_in_is_a_split_candidate():
    # The only end inside the 8 to 12 s window is exactly at 8 s; without it
    # the split falls back to the first word ending past 10 s, the last.
    def build(f, off):
        return [
            _segment(
                "A",
                [
                    (_t(f, off), _t(f + 95, off), " a"),
                    (_t(f + 95, off), _t(f + 95, off, 0.4), " b"),
                    (_t(f + 152, off), _t(f + 152, off, 0.4), " c"),
                ],
            )
        ]

    assert _frames_where(build, lambda r: _word_counts(r)[0] == 2) == []


def test_a_sentence_ending_exactly_12_s_in_is_a_split_candidate():
    # The sentence end at exactly 12 s beats the nearer plain word end.
    def build(f, off):
        return [
            _segment(
                "A",
                [
                    (_t(f, off), _t(f + 106, off), " a"),
                    (_t(f + 106, off), _t(f + 145, off, 0.4), " b."),
                    (_t(f + 160, off), _t(f + 160, off, 0.4), " c"),
                ],
            )
        ]

    assert _frames_where(build, lambda r: _word_counts(r)[0] == 2) == []


@pytest.mark.parametrize("ending", ["", "."])
def test_the_earlier_of_two_ends_equally_near_10_s_is_the_split(ending):
    # Ends at 9.6 s and 10.4 s, both 0.4 s from the target.
    def build(f, off):
        return [
            _segment(
                "A",
                [
                    (_t(f, off), _t(f + 120, off), " a" + ending),
                    (_t(f + 120, off), _t(f + 125, off, 0.4), " b" + ending),
                    (_t(f + 160, off), _t(f + 160, off, 0.4), " c"),
                ],
            )
        ]

    assert _frames_where(build, lambda r: _word_counts(r)[0] == 1) == []


def test_a_segment_of_exactly_the_minimum_is_not_folded():
    # Starting at a 0.3 s capped end and ending 5 frames on: 0.1 s.
    def build(f, off):
        return [
            _segment("A", [(_t(f - 10, off), _t(f, off, 0.2), " before")]),
            _segment("B", [(_t(f, off, 0.3), _t(f + 5, off), " yes")]),
            _segment("A", [(_t(f + 6, off), _t(f + 16, off), " after")]),
        ]

    assert _frames_where(build, lambda r: len(r) == 3) == []


def test_a_short_segment_exactly_1_s_from_its_neighbour_folds_into_it():
    def build(f, off):
        return [
            _segment("A", [(_t(f - 25, off), _t(f - 15, off, 0.2), " before")]),
            _segment("B", [(_t(f, off), _t(f + 1, off), " yes")]),
            _segment("A", [(_t(f + 40, off), _t(f + 50, off), " after")]),
        ]

    assert _frames_where(build, lambda r: len(r) == 2) == []


def test_a_short_segment_folds_into_a_neighbour_it_takes_to_exactly_the_maximum():
    def build(f, off):
        return [
            _segment("A", [(_t(f, off), _t(f + 120, off), " before")]),
            _segment("B", [(_t(f + 124, off), _t(f + 125, off), " yes")]),
            _segment("A", [(_t(f + 160, off), _t(f + 170, off), " after")]),
        ]

    assert _frames_where(build, lambda r: len(r) == 2) == []


def test_a_short_segment_equally_near_both_neighbours_folds_into_the_earlier():
    # 0.16 s on both sides: 5 frames less a 0.24 s cap, and 2 frames.
    def build(f, off):
        return [
            _segment("A", [(_t(f - 10, off), _t(f - 5, off, 0.24), " before")]),
            _segment("B", [(_t(f, off), _t(f + 1, off), " yes")]),
            _segment("C", [(_t(f + 3, off), _t(f + 13, off), " after")]),
        ]

    assert _frames_where(build, lambda r: r[0]["text"] == "before yes") == []


def test_neighbours_a_folded_segment_tiled_rejoin_at_exactly_the_maximum():
    def build(f, off):
        return [
            _segment("A", [(_t(f, off), _t(f + 60, off), " one")]),
            _segment("B", [(_t(f + 60, off), _t(f + 61, off), " yes")]),
            _segment("A", [(_t(f + 61, off), _t(f + 120, off, 0.4), " two")]),
        ]

    assert _frames_where(build, lambda r: len(r) == 1) == []
