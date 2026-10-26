"""Screen study (#653): frames on the transcript clock and the lag search.

Timelines here are made up: speakers take turns, the meeting app lights each
speaker's tile a known lag after they start, and the sampler keeps a frame
once a second when the screen changed, as the real one does.
"""

from __future__ import annotations

import random

import numpy as np
import pytest

from scripts.speaker_frames import align
from scripts.speaker_frames.align import NONE, UNKNOWN, Highlight, Utterance


def _turns(seed: int, speakers: int = 3, minutes: int = 6) -> list[Utterance]:
    rng = random.Random(seed)
    lines, at = [], 0
    while at < minutes * 60_000:
        who = rng.randrange(speakers)
        length = rng.randint(1_500, 9_000)
        lines.append(Utterance(at, at + length, who, who, 100 + who))
        at += length + rng.choice((0, 300, 800, 2_000))
    return lines


def _sampled(lines: list[Utterance], lag_ms: int, tick_ms: int = 1_000) -> Highlight:
    """The sampler's frames: one per tick when the lit tile changed, and a
    keep-alive every 15 s. Tile n is speaker n's."""
    end = max(u.end_ms for u in lines) + 5_000
    times, lit = [], []
    last: tuple[int, ...] | None = None
    last_time = -15_000
    for tick in range(0, end, tick_ms):
        spoken = tick - lag_ms
        state = tuple(
            sorted({u.reviewed - 100 for u in lines if u.start_ms <= spoken < u.end_ms})
        )  # type: ignore[operator]
        if state != last or tick - last_time >= 15_000:
            times.append(tick)
            lit.append(state)
            last, last_time = state, tick
    times_ms = np.array(times, np.int64)
    until = align.held_until(times, [0] * len(times))
    return Highlight(
        times_ms, tuple(lit), np.ones(len(times), bool), {0: 0, 1: 0, 2: 0}, until
    )


def test_frame_times_use_the_chunk_start_and_skip_unknown_chunks() -> None:
    rows = [
        {"sequence": 0, "offset_ms": 250},
        {"sequence": 2, "offset_ms": 1_000},
        {"sequence": 9, "offset_ms": 0},
    ]
    assert align.frame_times(rows, {0: 0, 2: 20_000}) == [250, 21_000, None]


def test_a_frame_holds_across_a_gap_only_when_no_tick_was_lost() -> None:
    until = align.held_until([0, 1_000, 5_000, 9_000], [None, 0, 3, None])
    assert until.tolist() == [1_000, 2_000, 6_000, 10_000]


def test_unobserved_spans_carry_no_highlight() -> None:
    times = np.array([0, 4_000, 8_000], np.int64)
    highlight = Highlight(
        times,
        ((1,), (2,), ()),
        np.ones(3, bool),
        {1: 0, 2: 0},
        align.held_until(times.tolist(), [None, 0, 5]),
    )
    tile, _ = align.tiles_at(highlight, np.array([500, 3_900, 4_500, 6_000, 8_200]))
    # 0-4 s held (nothing lost before the second frame); 4-8 s lost after a tick.
    assert tile.tolist() == [1, 1, 2, UNKNOWN, NONE]


def test_frames_from_before_the_field_existed_count_as_lost() -> None:
    until = align.held_until([0, 10_000], [None, None])
    assert until.tolist() == [1_000, 11_000]


@pytest.mark.parametrize("lag_ms", [-600, 0, 700, 1_500, 2_400])
def test_the_lag_search_finds_a_known_lag_when_sampled_finely(lag_ms: int) -> None:
    lines = _turns(seed=lag_ms % 97)
    found, agreement = align.fit_lag([(lines, _sampled(lines, lag_ms, tick_ms=100))])
    assert abs(found - lag_ms) <= 100
    assert agreement > 0.95


@pytest.mark.parametrize("lag_ms", [0, 700, 1_500])
def test_one_hertz_sampling_adds_up_to_a_tick_to_the_lag(lag_ms: int) -> None:
    # A change shows on the next tick, so the fitted lag sits up to a second
    # late, the same on every meeting the platform's lag is used for.
    lines = _turns(seed=lag_ms % 89 + 1)
    found, agreement = align.fit_lag([(lines, _sampled(lines, lag_ms))])
    assert lag_ms <= found <= lag_ms + 1_000
    assert agreement > 0.8


def test_the_lag_is_fitted_over_all_calibration_meetings_together() -> None:
    meetings = []
    for seed in (3, 4):
        lines = _turns(seed)
        meetings.append((lines, _sampled(lines, 800, tick_ms=100)))
    found, _ = align.fit_lag(meetings)
    assert abs(found - 800) <= 100


def test_no_frames_means_no_agreement_and_the_lag_nearest_zero() -> None:
    lines = _turns(seed=5)
    assert align.fit_lag([(lines, Highlight.empty())]) == (0, 0.0)


def test_overlapping_speech_is_marked_on_the_grid() -> None:
    lines = [Utterance(0, 1_000, 0, 0, 7), Utterance(500, 1_500, 1, 1, 8)]
    bins = align.meeting_bins(lines, Highlight.empty(), 0)
    assert bins.reviewed[:5].tolist() == [0] * 5
    assert bins.reviewed[5:10].tolist() == [align.SEVERAL] * 5
    assert bins.reviewed[10:15].tolist() == [1] * 5
    assert bins.reviewed_index == {7: 0, 8: 1}
