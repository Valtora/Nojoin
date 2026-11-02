"""The pure rule for live speakers the user merged: what folds, what goes by line.

Times are milliseconds. Live labels are A, B, C, D; final labels F1, F2, F3.
Each live speaker is placed as the carry-over places it: on its group's speech.
"""

from __future__ import annotations

from backend.utils.speaker_names.live_carry import (
    fold_merged_groups,
    group_spans,
    live_groups,
    users_act_on,
)
from backend.utils.speaker_names.resolve import place_event


def _fold(own, groups, lines):
    placed = {
        head: place_event(users_act_on(spans), lines)
        for head, spans in group_spans(own, groups).items()
    }
    return placed, fold_merged_groups(own, groups, lines, placed)


def test_merges_chain_onto_the_last_target_and_a_merge_within_a_group_is_a_no_op() -> (
    None
):
    assert live_groups([("C", "A"), ("A", "B")]) == {"C": "B", "A": "B"}
    # B is already C's group: merging it back changes nothing.
    assert live_groups([("C", "B"), ("B", "C")]) == {"C": "B"}
    assert live_groups([("A", "A")]) == {}


def test_a_group_is_placed_on_all_of_its_labels_speech() -> None:
    assert group_spans(
        {"A": [(0, 4000)], "C": [(3000, 9000)], "B": [(9000, 10000)]}, {"C": "A"}
    ) == {
        "A": ((0, 9000),),
        "B": ((9000, 10000),),
    }


def test_a_merge_the_final_pass_already_agrees_with_changes_nothing() -> None:
    placed, merged = _fold(
        {"A": [(0, 5000)], "C": [(6000, 12000)], "B": [(12000, 20000)]},
        {"C": "A"},
        [("F1", 0, 12000), ("F2", 12000, 20000)],
    )
    assert placed["A"].label == "F1"
    assert (merged.folds, merged.by_line) == ({}, {})


def test_a_merge_folds_the_final_speaker_that_is_clearly_the_merged_voice() -> None:
    placed, merged = _fold(
        {"A": [(0, 5000)], "C": [(6000, 14000)], "B": [(14000, 20000)]},
        {"C": "A"},
        [("F1", 0, 5000), ("F3", 6000, 14000), ("F2", 14000, 20000)],
    )
    # The group (13 s) is mostly F3's, so F1 joins F3: one speaker either way.
    assert placed["A"].label == "F3"
    assert merged.folds == {"F1": "F3"}
    assert merged.by_line == {}


def test_a_final_speaker_another_live_speaker_is_placed_on_is_never_folded() -> None:
    # The final pass put C's voice with B's, in F2.
    _, merged = _fold(
        {"A": [(0, 9000)], "C": [(9000, 14000)], "B": [(14000, 24000)]},
        {"C": "A"},
        [("F1", 0, 9000), ("F2", 9000, 14000), ("F2", 14000, 24000)],
    )
    assert merged.folds == {}
    # C's own lines go to the group's final speaker one by one instead.
    assert merged.by_line == {"C": "F1"}


def test_a_final_speaker_mostly_holding_someone_else_is_not_folded() -> None:
    # F2 holds all of C, but D's speech in it outweighs C's, though D is
    # placed on F3.
    placed, merged = _fold(
        {"A": [(0, 9000)], "C": [(9000, 11000)], "D": [(11000, 25000)]},
        {"C": "A"},
        [
            ("F1", 0, 9000),
            ("F2", 9000, 11000),
            ("F2", 11000, 15000),
            ("F3", 15000, 25000),
        ],
    )
    assert placed["D"].label == "F3"
    assert merged.folds == {}
    assert merged.by_line == {"C": "F1"}


def test_a_member_split_between_final_speakers_goes_by_line() -> None:
    # C's speech is half F2's and half F3's: no final speaker is clearly C.
    _, merged = _fold(
        {"A": [(0, 9000)], "C": [(9000, 15000)]},
        {"C": "A"},
        [("F1", 0, 9000), ("F2", 9000, 12000), ("F3", 12000, 15000)],
    )
    assert merged.folds == {}
    assert merged.by_line == {"C": "F1"}


def test_nothing_folds_into_a_group_no_final_speaker_clearly_holds() -> None:
    # The group's 10 s are split 5:5 between F1 and F2.
    placed, merged = _fold(
        {"A": [(0, 5000)], "C": [(5000, 10000)]},
        {"C": "A"},
        [("F1", 0, 5000), ("F2", 5000, 10000)],
    )
    target = placed["A"].label
    assert merged.folds == {}
    other = "C" if target == "F1" else "A"
    assert merged.by_line == {other: target}


def test_an_unmerged_label_is_never_folded_or_moved() -> None:
    _, merged = _fold(
        {"A": [(0, 5000)], "B": [(5000, 10000)]},
        {},
        [("F1", 0, 5000), ("F2", 5000, 10000)],
    )
    assert (merged.folds, merged.by_line) == ({}, {})
