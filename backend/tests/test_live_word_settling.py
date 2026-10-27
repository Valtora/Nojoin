"""Captured lexical agreement, window boundaries and immutable word payloads."""

from copy import deepcopy
from typing import Any

import pytest

from backend.utils.live_word_settling import (
    close_word_state,
    expire_word_state,
    plan_settling_window,
    reconcile_word_state,
)

Word = dict[str, Any]


def word(text: str = "hello", start: int | None = 1000, end: int | None = 1500) -> Word:
    return {"word": text, "start_ms": start, "end_ms": end}


def observe(old: list[Word], new: list[Word], step: int, k: int = 3) -> list[Word]:
    return reconcile_word_state(
        old,
        new,
        k=k,
        decode_id=f"decode-{step}",
        window_start_ms=-1,
        audio_end_ms=20_000,
    )


@pytest.mark.parametrize("k", [2, 3, 5])
def test_k_requires_comparisons_after_initial_observation(k: int) -> None:
    state = observe([], [word()], 0, k)
    original_id = state[0]["id"]
    for comparison in range(1, k):
        state = observe(state, [word()], comparison, k)
        assert not state[0]["locked"]
    state = observe(state, [word()], k, k)
    assert (state[0]["id"], state[0]["unchanged_count"], state[0]["lock_reason"]) == (
        original_id,
        k,
        "agreement",
    )


def test_lexical_change_resets_but_punctuation_case_and_retiming_do_not() -> None:
    state = observe([], [word("Hello,")], 0)
    identifier = state[0]["id"]
    state = observe(state, [word("hello.", 1200, 1800)], 1)
    assert state[0]["unchanged_count"] == 1
    assert state[0]["word"] == "hello."
    state = observe(state, [word("hullo.", 1300, 1900)], 2)
    assert state[0]["id"] == identifier
    assert state[0]["unchanged_count"] == 0
    assert state[0]["signature"] == "hullo"


def test_stable_suffix_waits_for_the_unstable_prefix() -> None:
    state: list[Word] = []
    for step, text in enumerate(["one", "two", "three", "three", "three"]):
        state = observe(state, [word(text), word("tail", 1600, 1900)], step)
        assert not any(item["locked"] for item in state)
    state = observe(state, [word("three"), word("tail", 1600, 1900)], 5)
    assert all(item["locked"] for item in state)


def test_repeated_word_far_away_has_new_identity_and_no_inherited_comparisons() -> None:
    state = observe([], [word()], 0)
    identifier = state[0]["id"]
    state = observe(state, [word(start=15000, end=15500)], 1)
    assert state[0]["id"] != identifier
    assert state[0]["unchanged_count"] == 0


def test_lcs_preserves_ids_around_insertions_deletions_and_repeated_words() -> None:
    initial = [
        word("yes", 1000, 1300),
        word("yes", 1800, 2100),
        word("tail", 2300, 2600),
    ]
    state = observe([], initial, 0)
    identifiers = [item["id"] for item in state]
    state = observe(state, [initial[0], word("new", 1400, 1600), *initial[1:]], 1)
    assert [state[index]["id"] for index in [0, 2, 3]] == identifiers
    state = observe(state, [initial[0], initial[2]], 2)
    assert [item["id"] for item in state] == [identifiers[0], identifiers[2]]


def test_duplicate_decode_and_skipped_observations_do_not_increment() -> None:
    state = observe([], [word()], 0)
    state = observe(state, [word()], 1)
    before = deepcopy(state)
    assert observe(state, [word("WRONG")], 1) == before
    # No call is made for failure, dropped passes or silence.
    state = observe(state, [word()], 9)
    assert state[0]["unchanged_count"] == 2


def test_locked_words_are_verbatim_and_context_cannot_duplicate_or_replace_them() -> (
    None
):
    state = close_word_state(observe([], [word("Hello,")], 0))
    before = deepcopy(state[0])
    state = observe(state, [word("wrong"), word("new", 2000, 2300)], 1)
    assert state[0] == before
    assert [item["word"] for item in state] == ["Hello,", "new"]
    assert state[1]["order"] > state[0]["order"]


def test_order_continues_after_pruned_immutable_locked_anchors() -> None:
    anchor = {**close_word_state(observe([], [word()], 0))[0], "order": 100}
    state = observe([anchor], [word("new", 2000, 2300)], 1)
    assert [item["order"] for item in state] == [100, 101]
    state = observe(state[1:], [word("new", 2000, 2300), word("later", 2500, 2800)], 2)
    assert [item["order"] for item in state] == [101, 102]


def test_expiry_uses_start_at_the_exact_cap_boundary_and_keeps_word_as_is() -> None:
    state = observe([], [word("unchanged", 1000, 2200)], 0)
    original = deepcopy(state[0])
    assert not expire_word_state(state, window_start_ms=999)[0]["locked"]
    expired = expire_word_state(state, window_start_ms=1000)[0]
    assert expired == {**original, "locked": True, "lock_reason": "expiry"}
    accepted = reconcile_word_state(
        state,
        [word("replacement", 1100, 2000)],
        k=3,
        decode_id="edge",
        window_start_ms=1000,
        audio_end_ms=11000,
    )
    assert accepted == [expired]


def test_new_and_retimed_words_at_the_edge_expire_on_acceptance() -> None:
    for previous in [[], observe([], [word(start=1100, end=1800)], 0)]:
        state = reconcile_word_state(
            previous,
            [word(start=1000, end=1800)],
            k=3,
            decode_id="edge",
            window_start_ms=1000,
            audio_end_ms=11000,
        )
        assert state[0]["lock_reason"] == "expiry"


def test_capture_beginning_and_lead_up_stay_within_total_window_cap() -> None:
    state = observe([], [word(start=0, end=500)], 0)
    assert not expire_word_state(state, window_start_ms=-1000)[0]["locked"]
    assert plan_settling_window(state, audio_end_ms=2000, window_ms=10000) == 0
    later = observe([], [word(start=8000, end=8500)], 0)
    assert plan_settling_window(later, audio_end_ms=14000, window_ms=10000) == 4000
    assert (
        plan_settling_window(
            [], audio_end_ms=20000, window_ms=10000, speech_start_ms=18000
        )
        == 13000
    )


def test_closure_uses_last_successful_hypothesis_without_an_observation() -> None:
    state = observe([], [word("last accepted")], 0)
    closed = close_word_state(state)
    assert closed[0]["lock_reason"] == "closure"
    assert closed[0]["unchanged_count"] == 0
    assert closed[0]["last_decode_id"] == "decode-0"
    assert not state[0]["locked"]


def test_untimed_neighbours_expire_and_lock_with_their_observed_anchor() -> None:
    hypothesis = [
        word("Leading,", None, None),
        word(" timed", 1000, 1500),
        word(" unknown?", None, None),
        word(" next", 2000, 2400),
        word(" trailing!", None, None),
    ]
    state = observe([], hypothesis, 0)
    state = expire_word_state(state, window_start_ms=1000)
    assert [item["locked"] for item in state] == [True, True, True, False, False]
    state = observe(state, hypothesis, 1)
    assert [item["word"] for item in state] == [item["word"] for item in hypothesis]
    assert all(
        item["start_ms"] is None
        for item in state
        if not item["word"].strip() in {"timed", "next"}
    )


def test_changed_untimed_neighbour_prevents_its_anchor_locking_early() -> None:
    state = observe([], [word(), word(" unknown", None, None)], 0, 2)
    state = observe(state, [word(), word(" unknown", None, None)], 1, 2)
    state = observe(state, [word(), word(" changed", None, None)], 2, 2)
    assert not any(item["locked"] for item in state)
    assert state[1]["unchanged_count"] == 0
    assert state[1]["id"]


def test_successful_empty_result_retracts_only_unlocked_words() -> None:
    locked = close_word_state(observe([], [word()], 0))
    state = observe(locked, [word("pending", 2000, 2400)], 1)
    assert observe(state, [], 2) == locked


def test_alignment_failure_stays_visible_as_untimed_atom_without_agreement() -> None:
    state = observe([], [word()], 0)
    for step in range(1, 6):
        state = observe(state, [word("unplaced", None, None)], step)
        assert state[0]["word"] == "unplaced"
        assert state[0]["start_ms"] is None and state[0]["end_ms"] is None
        assert state[0]["unchanged_count"] == 0
    assert not state[0]["locked"]
    assert close_word_state(state)[0]["lock_reason"] == "closure"
    assert expire_word_state(state, window_start_ms=20000)[0]["lock_reason"] == "expiry"
