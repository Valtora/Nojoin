"""Screen study (#653): the pass-rule measures on hand-made meetings.

Each meeting is a list of lines: when, who spoke after review, which
pipeline speaker the line got, and which tile the meeting app lit. The
measures should come out as worked by hand below each one.
"""

from __future__ import annotations

import numpy as np

from scripts.speaker_frames import align, metrics
from scripts.speaker_frames.align import Highlight, Utterance

# People 1, 2 and 3; their tiles 11, 12 and 13 in layout 0.
LAYOUT = {11: 0, 12: 0, 13: 0, 21: 1}


def _meeting(
    lines: list[tuple[float, float, int, int | None, int | None]],
    *,
    visible: float = 1.0,
) -> metrics.Meeting:
    """Lines as (start s, end s, reviewed person, pipeline speaker, lit tile).

    The app lights the tile for exactly the line, with no lag, and a frame
    is kept at every start and end. ``visible`` is the share of frames, from
    the start, on which tiles are on screen.
    """
    utterances = [
        Utterance(int(a * 1000), int(b * 1000), None, p, r) for a, b, r, p, _ in lines
    ]
    events: dict[int, tuple[int, ...]] = {}
    for a, b, _, _, tile in lines:
        events.setdefault(int(b * 1000), ())
        events[int(a * 1000)] = () if tile is None else (tile,)
    times = sorted(events)
    shown = np.array([i < visible * len(times) for i in range(len(times))])
    highlight = Highlight(
        np.array(times, np.int64),
        tuple(events[t] for t in times),
        shown,
        LAYOUT,
        align.held_until(times, [0] * len(times)),
    )
    return metrics.Meeting(
        "rec", "teams", align.meeting_bins(utterances, highlight, 0), utterances
    )


def _rota(
    seconds: float = 4.0,
) -> list[tuple[float, float, int, int | None, int | None]]:
    """People 1, 2, 3 in turn, three times each, pipeline right, tiles right."""
    lines, at = [], 0.0
    for _ in range(3):
        for person, tile in ((1, 11), (2, 12), (3, 13)):
            lines.append((at, at + seconds, person, person, tile))
            at += seconds
    return lines


def test_a_perfect_meeting_matches_everything() -> None:
    meeting = _meeting(_rota())
    assert metrics.visible_speech(meeting.bins) == (360, 360)
    assert metrics.speaker_counts(meeting.bins) == (3, 3, 3)
    matched, right, speech = metrics.matching(meeting.bins)
    assert (matched, right, speech) == (360, 360, 360)


def test_tiles_off_screen_count_against_the_precondition() -> None:
    meeting = _meeting(_rota(), visible=0.25)
    shown, speech = metrics.visible_speech(meeting.bins)
    assert shown / speech < 0.5
    scored = metrics.score([meeting])
    assert scored["precondition"]["result"] == metrics.FAIL
    assert scored["matching"] == metrics.NOT_SCORED


def test_a_wrong_tile_lowers_accuracy_but_not_the_match() -> None:
    lines = _rota()
    # Person 2's second turn lights person 3's tile.
    start, end, person, pipeline, _ = lines[4]
    lines[4] = (start, end, person, pipeline, 13)
    matched, right, speech = metrics.matching(_meeting(lines).bins)
    assert (matched, speech) == (360, 360)
    assert right == 320


def test_an_over_split_speaker_gets_a_right_merge_hint() -> None:
    lines = _rota()
    # The pipeline splits person 1 into speakers 1 and 4; the tile stays 11.
    lines = [
        (a, b, r, 4 if (r == 1 and i >= 3) else p, t)
        for i, (a, b, r, p, t) in enumerate(lines)
    ]
    meeting = _meeting(lines)
    assert metrics.speaker_counts(meeting.bins) == (3, 4, 3)
    found = metrics.hints(meeting.bins)
    assert (
        found.needed_merges,
        found.merge_hints,
        found.right_merges,
        found.fixed_merges,
    ) == (1, 1, 1, 1)
    assert found.split_hints == 0


def test_a_speaker_holding_two_people_gets_a_split_hint() -> None:
    lines = _rota()
    # The pipeline gives people 2 and 3 one speaker, 2.
    lines = [(a, b, r, 2 if r in (2, 3) else p, t) for a, b, r, p, t in lines]
    found = metrics.hints(_meeting(lines).bins)
    assert (found.two_person, found.split_hints, found.right_splits) == (1, 1, 1)


def test_tiles_that_wander_give_wrong_merge_hints() -> None:
    lines = _rota()
    # Two people share one tile: hints say their speakers are one person.
    lines = [(a, b, r, p, 11 if t == 12 else t) for a, b, r, p, t in lines]
    found = metrics.hints(_meeting(lines).bins)
    assert (found.merge_hints, found.right_merges) == (1, 0)


def test_short_replies_are_fixed_by_the_lit_tile() -> None:
    lines = _rota()
    at = lines[-1][1]
    # Ten one-second replies by person 2, labelled as pipeline speaker 1 but
    # lit on person 2's tile, and ten right ones by person 3.
    for k in range(10):
        lines.append((at + 2 * k, at + 2 * k + 1, 2, 1, 12))
    at += 20
    for k in range(10):
        lines.append((at + 2 * k, at + 2 * k + 1, 3, 3, 13))
    counts = metrics.short_replies(_meeting(lines))
    assert counts == metrics.ReplyCounts(errors=10, fixed=10, correct=10, broken=0)


def test_a_reply_lit_on_the_wrong_tile_is_broken() -> None:
    lines = _rota()
    at = lines[-1][1]
    lines.append((at, at + 1, 3, 3, 12))
    counts = metrics.short_replies(_meeting(lines))
    assert counts == metrics.ReplyCounts(errors=0, fixed=0, correct=1, broken=1)


def test_rules_below_their_minimum_cases_are_inconclusive() -> None:
    scored = metrics.score([_meeting(_rota())])
    assert scored["precondition"]["result"] == metrics.PASS
    assert scored["speaker_count"]["tiles_at_least_speakers"]["result"] == metrics.PASS
    assert (
        scored["speaker_count"]["tiles_below_overcount"]["result"]
        == metrics.INCONCLUSIVE
    )
    assert scored["merge_hints"]["result"] == metrics.INCONCLUSIVE
    assert scored["split_hints"]["result"] == metrics.INCONCLUSIVE
    assert scored["short_replies"]["result"] == metrics.INCONCLUSIVE


def test_matching_needs_two_platforms() -> None:
    teams = _meeting(_rota())
    zoom = metrics.Meeting("rec2", "zoom", teams.bins, teams.utterances)
    assert metrics.score([teams])["matching"]["result"] == metrics.FAIL
    assert metrics.score([teams, zoom])["matching"]["result"] == metrics.PASS
