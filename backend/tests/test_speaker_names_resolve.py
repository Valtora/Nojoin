"""Names follow the speech they were given for, onto the final pass's speakers.

Lines are ``(label, start_ms, end_ms)``. Every case states the overlap each
label holds, so the share against the 0.60 floor reads off the numbers.
"""

from __future__ import annotations

import pytest

from backend.utils.speaker_names.anchors import (
    first_turn,
    lines_from_final_segments,
    longest_turn,
    turn_nearest,
    turns_of,
)
from backend.utils.speaker_names.resolve import (
    MIN_COVERED_MS,
    NameEvent,
    place_event,
    resolve_names,
    standing_events,
)


def _event(event_id: int, name: str, start: int, end: int, **fields) -> NameEvent:
    """A user's name event; ``fields`` override source, authority, person,
    kind and supersedes."""
    return NameEvent(
        event_id=event_id,
        kind=fields.get("kind", "name"),
        name=name,
        global_speaker_id=fields.get("person"),
        source=fields.get("source", "user"),
        authority=fields.get("authority", 40),
        anchor_start_ms=start,
        anchor_end_ms=end,
        supersedes_event_id=fields.get("supersedes"),
    )


# --------------------------------------------------------------- anchors


def test_a_turn_runs_across_gaps_under_one_and_a_half_seconds() -> None:
    lines = [("A", 0, 1000), ("A", 2499, 3000), ("A", 4500, 5000)]
    # 1499 ms joins the first two; 1500 ms starts a new turn.
    assert turns_of(lines) == [("A", 0, 3000), ("A", 4500, 5000)]


def test_another_speakers_line_ends_a_turn() -> None:
    lines = [("A", 0, 1000), ("B", 1100, 1200), ("A", 1300, 9000)]
    assert turns_of(lines) == [("A", 0, 1000), ("B", 1100, 1200), ("A", 1300, 9000)]
    assert longest_turn(lines, "A") == (1300, 9000)
    assert first_turn(lines, "A") == (0, 1000)


def test_turns_are_built_in_time_order_whatever_order_the_lines_come_in() -> None:
    lines = [("A", 3000, 4000), ("A", 0, 1000), ("A", 1500, 2500)]
    assert turns_of(lines) == [("A", 0, 4000)]


def test_the_longest_turn_is_the_earliest_of_equals() -> None:
    lines = [("A", 0, 2000), ("B", 2000, 3000), ("A", 9000, 11000)]
    assert longest_turn(lines, "A") == (0, 2000)
    assert longest_turn(lines, "C") is None


def test_an_llm_name_anchors_to_the_speakers_turn_next_to_its_evidence() -> None:
    # B's line at 5-6 s names A; A answers at 6.2-9 s and spoke longer at 20 s.
    lines = [("A", 0, 1000), ("B", 5000, 6000), ("A", 6200, 9000), ("A", 20000, 40000)]
    assert turn_nearest(lines, "A", 5000, 6000) == (6200, 9000)
    # The speaker's own evidence line is the turn holding it.
    assert turn_nearest(lines, "A", 21000, 22000) == (20000, 40000)


def test_final_segments_leave_unattributed_speech_out() -> None:
    segments = [
        {"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"},
        {"start": 1.0, "end": 2.0, "speaker": "UNKNOWN"},
        {"start": 2.0, "end": 3.0, "speaker": ""},
        {"start": 3.0, "end": 3.0, "speaker": "SPEAKER_01"},
        {"start": 3.0, "end": 4.25, "speaker": "SPEAKER_01"},
    ]
    assert lines_from_final_segments(segments) == [
        ("SPEAKER_00", 0, 1000),
        ("SPEAKER_01", 3000, 4250),
    ]


# -------------------------------------------------------------- placement


def test_a_short_turn_inside_another_speakers_block_goes_to_its_own_speaker() -> None:
    # The user named a 2 s reply; the final pass kept it as SPEAKER_01's own
    # line in the middle of SPEAKER_00's long turn.
    lines = [
        ("SPEAKER_00", 0, 10000),
        ("SPEAKER_01", 10000, 12000),
        ("SPEAKER_00", 12000, 30000),
    ]
    placement = place_event(_event(1, "Alex", 10000, 12000), lines)
    assert (placement.outcome, placement.label, placement.share) == (
        "held",
        "SPEAKER_01",
        1.0,
    )


@pytest.mark.parametrize(
    ("split_ms", "outcome"),
    [
        # 6000 of 10000 ms is exactly the floor: held.
        (6000, "held"),
        # 5999 of 10000 ms is just under it: offered, never applied.
        (5999, "near_tie"),
    ],
)
def test_a_span_is_held_only_from_sixty_percent(split_ms: int, outcome: str) -> None:
    lines = [("SPEAKER_00", 0, split_ms), ("SPEAKER_01", split_ms, 10000)]
    placement = place_event(_event(1, "Alex", 0, 10000), lines)
    assert placement.outcome == outcome
    assert placement.label == "SPEAKER_00"


def test_speech_nobody_holds_does_not_count_against_the_holder() -> None:
    # 4 s of the span has no attributed speech; SPEAKER_00 holds all 6 s that do.
    lines = [("SPEAKER_00", 4000, 10000)]
    placement = place_event(_event(1, "Alex", 0, 10000), lines)
    assert (placement.outcome, placement.share, placement.covered_ms) == (
        "held",
        1.0,
        6000,
    )


def test_a_span_with_under_a_second_of_speech_names_nobody() -> None:
    lines = [("SPEAKER_00", 0, MIN_COVERED_MS - 1)]
    placement = place_event(_event(1, "Alex", 0, 10000), lines)
    assert (placement.outcome, placement.label) == ("unresolved", None)
    resolved, _ = resolve_names([_event(1, "Alex", 0, 10000)], lines)
    assert resolved == {}
    lines = [("SPEAKER_00", 0, MIN_COVERED_MS)]
    assert place_event(_event(1, "Alex", 0, 10000), lines).outcome == "held"


# ------------------------------------------------------------- resolution


def test_two_live_labels_named_alike_land_on_one_final_speaker() -> None:
    lines = [("SPEAKER_00", 0, 20000), ("SPEAKER_01", 20000, 30000)]
    events = [_event(1, "Alex", 0, 5000), _event(2, "alex", 12000, 18000)]
    resolved, _ = resolve_names(events, lines)
    assert set(resolved) == {"SPEAKER_00"}
    name = resolved["SPEAKER_00"]
    # The newest act's spelling is the one shown.
    assert (name.name, name.pending, name.event_ids) == ("alex", False, (1, 2))


def test_one_label_split_across_two_final_speakers_names_each_held_part() -> None:
    lines = [("SPEAKER_00", 0, 10000), ("SPEAKER_01", 10000, 20000)]
    events = [_event(1, "Alex", 0, 9000), _event(2, "Sam", 11000, 20000)]
    resolved, _ = resolve_names(events, lines)
    assert {label: name.name for label, name in resolved.items()} == {
        "SPEAKER_00": "Alex",
        "SPEAKER_01": "Sam",
    }


def test_a_near_tie_is_offered_on_the_top_holder_as_pending() -> None:
    lines = [("SPEAKER_00", 0, 5500), ("SPEAKER_01", 5500, 10000)]
    resolved, placements = resolve_names([_event(1, "Alex", 0, 10000)], lines)
    assert placements[0].outcome == "near_tie"
    assert resolved["SPEAKER_00"].pending is True
    assert resolved["SPEAKER_00"].reason == "near_tie"
    assert "SPEAKER_01" not in resolved


@pytest.mark.parametrize(
    ("alex_ms", "sam_ms", "name", "reason"),
    [
        # Alex holds 7 of 10 s of naming: clear.
        (7000, 3000, "Alex", "held"),
        # Alex holds 5.5 of 10 s: too close to call, the stronger shown.
        (5500, 4500, "Alex", "conflict"),
        # Summing: two Sam acts of 2.5 s (5 of 8 s) outweigh one Alex act of 3 s.
        (3000, 5000, "Sam", "held"),
    ],
)
def test_names_at_one_authority_are_summed_and_need_sixty_percent(
    alex_ms: int, sam_ms: int, name: str, reason: str
) -> None:
    lines = [("SPEAKER_00", 0, 60000)]
    events = [_event(1, "Alex", 0, alex_ms)]
    if sam_ms == 5000:
        events += [_event(2, "Sam", 10000, 12500), _event(3, "Sam", 20000, 22500)]
    else:
        events.append(_event(2, "Sam", 10000, 10000 + sam_ms))
    resolved, _ = resolve_names(events, lines)
    assert (resolved["SPEAKER_00"].name, resolved["SPEAKER_00"].reason) == (
        name,
        reason,
    )
    assert resolved["SPEAKER_00"].pending is (reason != "held")


def test_the_same_person_counts_once_however_it_is_spelled() -> None:
    lines = [("SPEAKER_00", 0, 60000)]
    events = [
        _event(1, "Alex", 0, 3000, person=7),
        _event(2, "Alexander", 5000, 8000, person=7),
        _event(3, "Sam", 10000, 14000),
    ]
    resolved, _ = resolve_names(events, lines)
    # Person 7 holds 6 of 10 s.
    assert resolved["SPEAKER_00"].global_speaker_id == 7
    assert resolved["SPEAKER_00"].reason == "held"


def test_a_user_name_outranks_a_machine_name_with_more_speech() -> None:
    lines = [("SPEAKER_00", 0, 60000)]
    events = [
        _event(1, "Alexa", 0, 30000, source="llm", authority=20),
        _event(2, "Alex", 40000, 41500),
    ]
    resolved, _ = resolve_names(events, lines)
    assert (resolved["SPEAKER_00"].name, resolved["SPEAKER_00"].source) == (
        "Alex",
        "user",
    )


def test_a_held_name_beats_a_higher_authority_near_tie_on_the_same_speaker() -> None:
    lines = [("SPEAKER_00", 0, 20000), ("SPEAKER_01", 20000, 26000)]
    events = [
        # Held by SPEAKER_00 outright.
        _event(1, "Alex", 0, 5000, source="voiceprint", authority=30),
        # 5 of 9 s on SPEAKER_00: a near tie.
        _event(2, "Sam", 15000, 24000),
    ]
    resolved, _ = resolve_names(events, lines)
    assert (resolved["SPEAKER_00"].name, resolved["SPEAKER_00"].pending) == (
        "Alex",
        False,
    )


def test_a_superseded_name_no_longer_stands() -> None:
    events = [_event(1, "Alx", 0, 5000), _event(2, "Alex", 0, 5000, supersedes=1)]
    assert [event.event_id for event in standing_events(events)] == [2]
    resolved, _ = resolve_names(events, [("SPEAKER_00", 0, 5000)])
    assert resolved["SPEAKER_00"].name == "Alex"


def test_a_dismissal_names_nobody_and_removes_what_it_dismissed() -> None:
    events = [
        _event(1, "Alex", 0, 5000, source="llm", authority=20),
        _event(2, "Alex", 0, 5000, kind="dismiss", supersedes=1),
    ]
    assert standing_events(events) == []
    resolved, placements = resolve_names(events, [("SPEAKER_00", 0, 5000)])
    assert resolved == {} and placements == []


def test_no_events_resolve_to_nothing() -> None:
    assert resolve_names([], [("SPEAKER_00", 0, 5000)]) == ({}, [])
