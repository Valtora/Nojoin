"""Locked context cannot consume later repetitions across expiry."""

from typing import Any

import pytest

from backend.utils.live_word_settling import reconcile_word_state

Word = dict[str, Any]


def _first_observation(words: list[Word]) -> list[Word]:
    return reconcile_word_state(
        [],
        words,
        k=3,
        decode_id="first",
        window_start_ms=-6000,
        audio_end_ms=4000,
    )


def _after_expiry(existing: list[Word], decoded: list[Word]) -> list[Word]:
    return reconcile_word_state(
        existing,
        decoded,
        k=3,
        decode_id="next",
        window_start_ms=2000,
        audio_end_ms=12000,
        input_start_ms=2000,
    )


@pytest.mark.parametrize("repeated_start_ms", [2100, 2300])
def test_later_repeated_word_survives_locked_prefix_expiry(
    repeated_start_ms: int,
) -> None:
    first = _first_observation(
        [
            {"word": "yes", "start_ms": 1800, "end_ms": 2100},
            {"word": "yes", "start_ms": repeated_start_ms, "end_ms": 2600},
        ]
    )
    result = _after_expiry(
        first,
        [
            {"word": "yes", "start_ms": repeated_start_ms, "end_ms": 2600},
            {"word": "later", "start_ms": 11500, "end_ms": 11800},
        ],
    )

    assert [word["start_ms"] for word in result] == [
        1800,
        repeated_start_ms,
        11500,
    ]
    assert result[0] == {**first[0], "locked": True, "lock_reason": "expiry"}
    assert result[1]["id"] == first[1]["id"]
    assert result[1]["unchanged_count"] == 1
    assert not result[1]["locked"]
    assert result[1]["last_decode_id"] == "next"
    assert [word["order"] for word in result] == [0, 1, 2]


def test_later_anchor_survives_untimed_neighbour_matching_locked_text() -> None:
    first = _first_observation([{"word": "yes", "start_ms": 1800, "end_ms": 2100}])
    result = _after_expiry(
        first,
        [
            {"word": "new", "start_ms": 2300, "end_ms": 2600},
            {"word": " yes", "start_ms": None, "end_ms": None},
            {"word": "later", "start_ms": 11500, "end_ms": 11800},
        ],
    )

    assert [word["word"] for word in result] == ["yes", "new", " yes", "later"]
    assert result[1]["start_ms"] == 2300
    assert result[2]["start_ms"] is None
    assert result[2]["end_ms"] is None
    assert not any(word["locked"] for word in result[1:])


def test_straddling_locked_duplicate_and_untimed_neighbour_are_removed() -> None:
    first = _first_observation([{"word": "yes", "start_ms": 1800, "end_ms": 2100}])
    result = _after_expiry(
        first,
        [
            {"word": "yes", "start_ms": 2000, "end_ms": 2400},
            {"word": " duplicate", "start_ms": None, "end_ms": None},
            {"word": "next", "start_ms": 2600, "end_ms": 2900},
        ],
    )

    assert [word["word"] for word in result] == ["yes", "next"]
    assert result[0] == {**first[0], "locked": True, "lock_reason": "expiry"}
    assert result[1]["unchanged_count"] == 0
