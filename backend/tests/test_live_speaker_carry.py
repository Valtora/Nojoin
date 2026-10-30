"""The pure live-to-final rule: where a live speaker goes, and what a merge joins.

Times are milliseconds. Live labels are A, B, C, D; final labels F1, F2, F3.
"""

from __future__ import annotations

from backend.utils.speaker_names.live_carry import carry_live_speakers, live_groups


def test_merges_chain_onto_the_last_target_and_a_merge_within_a_group_is_a_no_op() -> (
    None
):
    assert live_groups([("C", "A"), ("A", "B")]) == {"C": "B", "A": "B"}
    # B is already C's group: merging it back changes nothing.
    assert live_groups([("C", "B"), ("B", "C")]) == {"C": "B"}
    assert live_groups([("A", "A")]) == {}


def test_each_live_speaker_goes_where_most_of_its_own_speech_is() -> None:
    carry = carry_live_speakers(
        {"A": [(0, 9000)], "B": [(9000, 20000)]},
        {},
        [("F1", 0, 8000), ("F2", 8000, 20000)],
    )
    assert carry.targets == {"A": "F1", "B": "F2"}
    assert (carry.folds, carry.moves) == ({}, {})


def test_a_merge_the_final_pass_already_agrees_with_changes_nothing() -> None:
    carry = carry_live_speakers(
        {"A": [(0, 5000)], "C": [(6000, 12000)], "B": [(12000, 20000)]},
        {"C": "A"},
        [("F1", 0, 12000), ("F2", 12000, 20000)],
    )
    assert carry.targets == {"A": "F1", "C": "F1", "B": "F2"}
    assert (carry.folds, carry.moves) == ({}, {})


def test_a_merge_folds_the_final_speaker_that_is_clearly_the_merged_voice() -> None:
    carry = carry_live_speakers(
        {"A": [(0, 5000)], "C": [(6000, 14000)], "B": [(14000, 20000)]},
        {"C": "A"},
        [("F1", 0, 5000), ("F3", 6000, 14000), ("F2", 14000, 20000)],
    )
    # The group (13 s) is mostly F3's, so F1 joins F3: one speaker either way.
    assert carry.targets == {"A": "F3", "C": "F3", "B": "F2"}
    assert carry.folds == {"F1": "F3"}
    assert carry.moves == {}


def test_a_final_speaker_another_live_speaker_goes_to_is_never_folded() -> None:
    # The final pass put C's voice with B's in F2.
    final = [("F1", 0, 9000), ("F2", 9000, 14000), ("F2", 14000, 24000)]
    carry = carry_live_speakers(
        {"A": [(0, 9000)], "C": [(9000, 14000)], "B": [(14000, 24000)]},
        {"C": "A"},
        final,
    )
    assert carry.targets == {"A": "F1", "C": "F1", "B": "F2"}
    assert carry.folds == {}
    # Only the final line C's live lines cover moves; B's stays with F2.
    assert carry.moves == {1: "F1"}


def test_a_final_speaker_mostly_holding_someone_else_is_not_folded() -> None:
    # F2 holds all of C, but D's speech in it outweighs C's, though D's own
    # final speaker is F3.
    final = [
        ("F1", 0, 9000),
        ("F2", 9000, 11000),
        ("F2", 11000, 15000),
        ("F3", 15000, 25000),
    ]
    carry = carry_live_speakers(
        {"A": [(0, 9000)], "C": [(9000, 11000)], "D": [(11000, 25000)]},
        {"C": "A"},
        final,
    )
    assert carry.targets["D"] == "F3"
    assert carry.folds == {}
    assert carry.moves == {1: "F1"}


def test_a_member_split_between_final_speakers_moves_only_its_lines() -> None:
    # C's speech is half F2's and half F3's: no final speaker is clearly C.
    final = [
        ("F1", 0, 9000),
        ("F2", 9000, 12000),
        ("F3", 12000, 15000),
        ("F3", 15000, 16000),
    ]
    carry = carry_live_speakers(
        {"A": [(0, 9000)], "C": [(9000, 15000)]},
        {"C": "A"},
        final,
    )
    assert carry.folds == {}
    # The last F3 line is no live speech of C's, so it stays.
    assert carry.moves == {1: "F1", 2: "F1"}


def test_nothing_folds_into_a_group_no_final_speaker_clearly_holds() -> None:
    # The group's 10 s are split 5:5 between F1 and F2.
    carry = carry_live_speakers(
        {"A": [(0, 5000)], "C": [(5000, 10000)]},
        {"C": "A"},
        [("F1", 0, 5000), ("F2", 5000, 10000)],
    )
    assert carry.targets["A"] == carry.targets["C"]
    assert carry.folds == {}
    target = carry.targets["A"]
    other = 1 if target == "F1" else 0
    assert carry.moves == {other: target}


def test_unattributed_final_lines_never_move_and_no_final_lines_map_nothing() -> None:
    carry = carry_live_speakers(
        {"A": [(0, 5000)], "C": [(5000, 10000)]},
        {"C": "A"},
        [("F1", 0, 5000), None, ("F2", 6000, 6500)],
    )
    assert 1 not in carry.moves
    # The group's speech is kept for its name even with no final lines.
    nothing = carry_live_speakers(
        {"A": [(0, 5000)], "C": [(4000, 9000)]}, {"C": "A"}, [None]
    )
    assert (nothing.targets, nothing.folds, nothing.moves) == ({}, {}, {})
    assert nothing.spans == {"A": ((0, 9000),)}
