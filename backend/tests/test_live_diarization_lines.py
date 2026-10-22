"""Which label a live transcript line shows, from the pieces inside it."""

from __future__ import annotations

import numpy as np

from backend.processing.live_diarization.lines import LabelTimeline


def _timeline(
    pieces: list[tuple[float, float, int, bool]], complete_until_s: float = 100.0
):
    """Pieces as (start, end, label or -1 while held, locked)."""
    return LabelTimeline(
        start=np.array([p[0] for p in pieces], np.float64),
        end=np.array([p[1] for p in pieces], np.float64),
        label=np.array([p[2] for p in pieces], np.int64),
        locked=np.array([p[3] for p in pieces], bool),
        complete_until_s=complete_until_s,
    )


def test_a_line_takes_the_label_holding_most_of_its_speech():
    timeline = _timeline([(0.0, 3.0, 4, False), (3.0, 4.0, 1, False)])

    decision = timeline.label_line(0.0, 4.0)

    assert decision.speaker == "LIVE_5"
    assert decision.share == 0.75
    assert decision.locked is False


def test_held_speech_counts_against_the_label():
    # 1.5 s labelled against 1.5 s held: no majority, so no label yet.
    timeline = _timeline([(0.0, 1.5, 2, True), (1.5, 3.0, -1, False)])

    assert timeline.label_line(0.0, 3.0).speaker == "UNKNOWN"


def test_a_line_split_evenly_between_two_voices_stays_unlabelled():
    timeline = _timeline([(0.0, 2.0, 0, True), (2.0, 4.0, 1, True)])

    decision = timeline.label_line(0.0, 4.0)

    assert decision.speaker == "UNKNOWN"
    assert decision.locked is True


def test_only_the_part_of_a_piece_inside_the_line_counts():
    timeline = _timeline([(0.0, 10.0, 0, False), (10.0, 12.0, 1, False)])

    assert timeline.label_line(9.0, 12.0).speaker == "LIVE_2"


def test_a_line_locks_only_when_every_piece_is_locked_and_emitted():
    timeline = _timeline(
        [(0.0, 2.0, 3, True), (2.0, 4.0, 3, True)], complete_until_s=3.5
    )

    assert timeline.label_line(0.0, 3.0).locked is True
    # The engine has not reached the end of this line yet.
    assert timeline.label_line(0.0, 4.0).locked is False

    mixed = _timeline([(0.0, 2.0, 3, True), (2.0, 4.0, 3, False)])
    assert mixed.label_line(0.0, 4.0).locked is False


def test_a_line_with_held_speech_never_locks_until_it_is_labelled():
    timeline = _timeline([(0.0, 3.0, 1, True), (3.0, 3.5, -1, True)])

    decision = timeline.label_line(0.0, 3.5)

    assert decision.speaker == "LIVE_2"
    assert decision.locked is False


def test_a_line_with_no_speech_found_settles_unlabelled_once_emitted():
    timeline = _timeline([(0.0, 1.0, 0, True)], complete_until_s=6.0)

    assert timeline.label_line(2.0, 5.0).speaker == "UNKNOWN"
    assert timeline.label_line(2.0, 5.0).locked is True
    assert timeline.label_line(5.0, 7.0).locked is False
