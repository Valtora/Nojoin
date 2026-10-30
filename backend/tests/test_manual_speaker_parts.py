"""A chosen speaker reaches the final words its line covers, and no others."""

from __future__ import annotations

from typing import Any

import pytest

from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.models.speaker import RecordingSpeaker
from backend.utils.canonical_pipeline.manual_speaker_parts import (
    UNTIMED_MIN_SHARE,
    final_speaker_value,
    follow_chosen_speakers,
    split_for_manual_speakers,
    unplaced_live_pick,
)
from backend.utils.word_text import reconstruct_text_from_words

PICKED = "SPEAKER_01"
LIVE_LABEL = "LIVE_PROVISIONAL_2"


def _line(
    line_id: int,
    span: tuple[float, float],
    *,
    label: str = PICKED,
    speaker_id: int | None = 7,
    locked: bool = True,
) -> TranscriptUtterance:
    return TranscriptUtterance(
        id=line_id,
        public_id=f"line-{line_id}",
        recording_id=1,
        sort_key=f"{line_id:09}",
        start_ms=round(span[0] * 1000),
        end_ms=round(span[1] * 1000),
        text=f"line {line_id}",
        speaker_label=label,
        recording_speaker_id=speaker_id,
        manual_speaker_locked=locked,
        source_kind="live",
        state=TranscriptUtteranceState.STABLE,
        revision=1,
    )


def _words(start: float, end: float, prefix: str = "w") -> list[dict[str, Any]]:
    """0.4 s words every 0.5 s, the first 50 ms after ``start``."""
    words, index = [], 0
    while start + 0.5 * index + 0.45 <= end + 1e-9:
        at = round(start + 0.05 + 0.5 * index, 3)
        words.append(
            {"word": f" {prefix}{index}", "start": at, "end": round(at + 0.4, 3)}
        )
        index += 1
    return words


def _segment(
    start: float, end: float, *, speaker: str = "SPEAKER_00", timed: bool = True
) -> dict[str, Any]:
    words = _words(start, end)
    segment: dict[str, Any] = {
        "start": start,
        "end": end,
        "speaker": speaker,
        "text": reconstruct_text_from_words(words),
    }
    if timed:
        segment["words"] = words
    return segment


def _spans(parts: list[dict[str, Any]]) -> list[tuple[float, float, str]]:
    return [(part["start"], part["end"], part["text"]) for part in parts]


def test_a_short_choice_inside_a_long_segment_takes_only_its_words() -> None:
    segment = _segment(0.0, 20.4)
    parts, sources = split_for_manual_speakers([_line(1, (8.0, 10.0))], [segment])
    assert _spans(parts) == [
        (0.0, 8.05, " ".join(f"w{index}" for index in range(16))),
        (8.05, 10.05, "w16 w17 w18 w19"),
        (10.05, 20.4, " ".join(f"w{index}" for index in range(20, 40))),
    ]
    assert sources == [frozenset(), frozenset({1}), frozenset()]
    assert [word for part in parts for word in part["words"]] == segment["words"]
    assert all(part["speaker"] == "SPEAKER_00" for part in parts)


def test_a_choice_covering_most_of_a_segment_leaves_the_rest() -> None:
    parts, sources = split_for_manual_speakers(
        [_line(1, (1.0, 10.0))], [_segment(0.0, 10.0)]
    )
    assert [(part["start"], part["end"]) for part in parts] == [
        (0.0, 1.05),
        (1.05, 10.0),
    ]
    assert parts[0]["text"] == "w0 w1"
    assert sources == [frozenset(), frozenset({1})]


@pytest.mark.parametrize(
    ("end", "inherits"),
    [(8.0, False), (10.0, False), (10.1, True), (19.0, True)],
    ids=["two-fifths", "exactly-half", "just-over-half", "most"],
)
def test_an_untimed_segment_takes_a_choice_only_over_a_majority(
    end: float, inherits: bool
) -> None:
    assert UNTIMED_MIN_SHARE == 0.5
    segment = _segment(0.0, 20.0, timed=False)
    parts, sources = split_for_manual_speakers([_line(1, (0.0, end))], [segment])
    assert parts == [segment]
    assert sources == [frozenset({1}) if inherits else frozenset()]


def test_an_untimed_majority_counts_overlapping_lines_once() -> None:
    # Two choices for one speaker over the same 6 s of a 10 s segment, on two
    # audio sources, are 6 s of it, not 12.
    lines = [_line(1, (0.0, 6.0)), _line(2, (0.0, 6.0))]
    _, sources = split_for_manual_speakers(lines, [_segment(0.0, 10.0, timed=False)])
    assert sources == [frozenset({1, 2})]
    lines = [_line(1, (0.0, 4.5)), _line(2, (0.0, 4.5))]
    _, sources = split_for_manual_speakers(lines, [_segment(0.0, 10.0, timed=False)])
    assert sources == [frozenset()]


def test_two_speakers_tied_over_an_untimed_segment_take_nothing() -> None:
    lines = [
        _line(1, (0.0, 6.0)),
        _line(2, (4.0, 10.0), label="SPEAKER_02", speaker_id=8),
    ]
    _, sources = split_for_manual_speakers(lines, [_segment(0.0, 10.0, timed=False)])
    assert sources == [frozenset()]


@pytest.mark.parametrize("timed", [True, False], ids=["timed", "untimed"])
def test_a_choice_the_final_pass_agrees_with_keeps_the_segment_whole(
    timed: bool,
) -> None:
    segment = _segment(0.0, 12.0, speaker=PICKED, timed=timed)
    parts, sources = split_for_manual_speakers([_line(1, (4.0, 6.0))], [segment])
    assert parts == [segment]
    assert sources == [frozenset({1})]


def test_agreement_reads_the_chosen_speakers_current_label() -> None:
    # The row still holds an older label; its speaker is the segment's now.
    line = _line(1, (4.0, 6.0), label="SPEAKER_05")
    segment = _segment(0.0, 12.0, speaker=PICKED)
    assert split_for_manual_speakers([line], [segment], {7: PICKED}) == (
        [segment],
        [frozenset({1})],
    )
    parts, _ = split_for_manual_speakers([line], [segment], {8: PICKED})
    assert len(parts) == 3


def test_an_agreeing_choice_stays_in_the_segments_own_run() -> None:
    segment = _segment(0.0, 12.0, speaker=PICKED)
    lines = [
        _line(1, (2.0, 4.0)),
        _line(2, (8.0, 10.0), label="SPEAKER_02", speaker_id=8),
    ]
    parts, sources = split_for_manual_speakers(lines, [segment])
    assert [(part["start"], part["end"]) for part in parts] == [
        (0.0, 8.05),
        (8.05, 10.05),
        (10.05, 12.0),
    ]
    assert sources == [frozenset({1}), frozenset({2}), frozenset()]


def test_neighbouring_choices_of_one_speaker_make_one_part() -> None:
    lines = [_line(1, (4.0, 6.0)), _line(2, (6.0, 8.0))]
    parts, sources = split_for_manual_speakers(lines, [_segment(0.0, 12.0)])
    assert [(part["start"], part["end"]) for part in parts] == [
        (0.0, 4.05),
        (4.05, 8.05),
        (8.05, 12.0),
    ]
    assert sources == [frozenset(), frozenset({1, 2}), frozenset()]


@pytest.mark.parametrize("timed", [True, False], ids=["timed", "untimed"])
def test_a_live_speaker_pick_processing_placed_nowhere_carries_nothing(
    timed: bool,
) -> None:
    # Speaker assignment moves a pick to a processed speaker whenever there
    # is one, so a raw live label here means processing found no speakers.
    line = _line(1, (8.0, 10.0), label=LIVE_LABEL, speaker_id=None)
    segment = _segment(0.0, 10.4, speaker="UNKNOWN", timed=timed)
    assert unplaced_live_pick(line)
    assert split_for_manual_speakers([line], [segment]) == ([segment], [frozenset()])


def test_only_an_unplaced_live_pick_falls_back() -> None:
    assert not unplaced_live_pick(_line(1, (0.0, 1.0)))
    assert not unplaced_live_pick(
        _line(1, (0.0, 1.0), label=LIVE_LABEL, speaker_id=None, locked=False)
    )
    assert final_speaker_value({"speaker": LIVE_LABEL}) == "UNKNOWN"
    assert final_speaker_value({"speaker": None}) == "UNKNOWN"
    assert final_speaker_value({"speaker": "SPEAKER_03"}) == "SPEAKER_03"


@pytest.mark.parametrize(
    "line",
    [
        _line(1, (8.0, 10.0), locked=False),
        _line(1, (8.0, 10.0), label="UNKNOWN", speaker_id=None),
    ],
    ids=["unlocked", "no-speaker"],
)
def test_a_line_without_a_chosen_speaker_carries_nothing(
    line: TranscriptUtterance,
) -> None:
    segment = _segment(0.0, 20.4)
    assert split_for_manual_speakers([line], [segment]) == ([segment], [frozenset()])


def test_untimed_words_follow_their_timed_neighbour() -> None:
    segment = _segment(0.0, 6.0)
    words = segment["words"]
    # Untimed words after the last chosen word, after an unchosen one, and
    # before every timed word.
    words.insert(4, {"word": " um"})
    words.insert(9, {"word": " uh"})
    words.insert(0, {"word": " so"})
    parts, sources = split_for_manual_speakers([_line(1, (0.0, 2.0))], [segment])
    assert _spans(parts) == [
        (0.0, 2.05, "so w0 w1 w2 w3 um"),
        (2.05, 6.0, "w4 w5 w6 w7 uh w8 w9 w10 w11"),
    ]
    assert sources == [frozenset({1}), frozenset()]


def test_a_line_the_finaliser_matches_exactly_carries_onto_nothing_else() -> None:
    line = _line(1, (8.0, 10.0))
    exact = _segment(8.0, 10.0)
    overlapping = _segment(9.0, 15.0, speaker="SPEAKER_02")
    parts, sources = split_for_manual_speakers([line], [exact, overlapping])
    assert parts == [exact, overlapping]
    assert sources == [frozenset(), frozenset()]


def test_a_second_line_with_the_same_bounds_is_not_matched_twice() -> None:
    lines = [_line(1, (8.0, 10.0), locked=False), _line(2, (8.0, 10.0))]
    segment = _segment(8.0, 10.0, timed=False)
    _, sources = split_for_manual_speakers(lines, [segment])
    # The finaliser matches the first line; the second still carries.
    assert sources == [frozenset()]
    _, sources = split_for_manual_speakers(lines, [segment, dict(segment)])
    assert sources == [frozenset(), frozenset()]
    _, sources = split_for_manual_speakers(
        lines, [segment, _segment(8.0, 10.5, timed=False)]
    )
    assert sources == [frozenset(), frozenset({2})]


def _speakers(*labels: str) -> list[RecordingSpeaker]:
    return [
        RecordingSpeaker(id=index, recording_id=1, diarization_label=label)
        for index, label in enumerate(labels, start=7)
    ]


def _earlier_run() -> list[TranscriptUtterance]:
    """Speaker 7's line at 8-10 s moved to speaker 8, whose own speech is 21-30 s."""
    return [
        _line(1, (0.0, 8.0), label="SPEAKER_00", locked=False),
        _line(2, (8.0, 10.0), label="SPEAKER_01", speaker_id=8),
        _line(3, (21.0, 25.0), label="SPEAKER_01", speaker_id=8, locked=False),
        _line(4, (26.0, 30.0), label="SPEAKER_01", speaker_id=8, locked=False),
    ]


def test_a_choice_follows_its_speakers_speech_to_a_new_label() -> None:
    lines = _earlier_run()
    # This run numbers the two voices the other way round.
    final = [
        _segment(0.0, 20.4, speaker="SPEAKER_01"),
        _segment(21.0, 30.0, speaker="SPEAKER_00"),
    ]
    follow_chosen_speakers(lines, final, _speakers("SPEAKER_00", "SPEAKER_01"))
    assert (lines[1].recording_speaker_id, lines[1].speaker_label) == (7, "SPEAKER_00")
    # Only the chosen line moves; the earlier run's own lines are the finaliser's.
    assert [line.recording_speaker_id for line in lines] == [7, 7, 8, 8]
    parts, sources = split_for_manual_speakers(
        lines, final, {7: "SPEAKER_00", 8: "SPEAKER_01"}
    )
    assert sources[:3] == [frozenset(), frozenset({2}), frozenset()]
    assert parts[1]["text"] == "w16 w17 w18 w19"


def test_a_choice_follows_a_merge_to_its_survivor() -> None:
    lines = _earlier_run()
    speakers = _speakers("SPEAKER_00", "SPEAKER_01", "SPEAKER_02")
    speakers[1].merged_into_id = 9
    final = [_segment(21.0, 30.0, speaker="SPEAKER_01")]
    follow_chosen_speakers(lines, final, speakers)
    assert lines[1].recording_speaker_id == 9


@pytest.mark.parametrize("case", ["no-speech", "no-final-speakers"])
def test_a_choice_with_nothing_to_follow_keeps_its_speaker(case: str) -> None:
    lines = _earlier_run()
    if case == "no-speech":
        # Every line of the chosen speaker was moved by hand.
        lines = lines[:2]
        final = [_segment(0.0, 30.0, speaker="SPEAKER_00")]
    else:
        final = [_segment(0.0, 30.0, speaker="UNKNOWN")]
    follow_chosen_speakers(lines, final, _speakers("SPEAKER_00", "SPEAKER_01"))
    assert (lines[1].recording_speaker_id, lines[1].speaker_label) == (8, "SPEAKER_01")
