"""Unaligned rolling hypotheses may repeat context but must retain new wording."""

from copy import deepcopy
from typing import Any

import pytest

from backend.utils.live_word_settling import close_word_state, reconcile_word_state
from backend.utils.word_text import reconstruct_text_from_words

Word = dict[str, Any]


def _locked() -> list[Word]:
    words = [
        {"word": "Hello", "start_ms": 100, "end_ms": 400},
        {"word": " world", "start_ms": 550, "end_ms": 850},
    ]
    return close_word_state(
        reconcile_word_state(
            [], words, k=3, decode_id="initial", window_start_ms=-1, audio_end_ms=2000
        )
    )


def _observe(locked: list[Word], text: str, start: int | None = 0) -> list[Word]:
    return reconcile_word_state(
        locked,
        [{"word": text, "start_ms": None, "end_ms": None}],
        k=3,
        decode_id="unaligned",
        window_start_ms=-1,
        audio_end_ms=4000,
        input_start_ms=start,
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hello world again", "Hello world again"),
        ("world again", "Hello world again"),
        ("HELLO, WORLD!  Again, exactly!", "Hello world Again, exactly!"),
        ("Hello world Hello world again", "Hello world Hello world again"),
    ],
)
def test_unaligned_context_removes_only_the_matching_prefix(
    text: str, expected: str
) -> None:
    locked = _locked()
    before = deepcopy(locked)
    state = _observe(locked, text)
    assert reconstruct_text_from_words(state) == expected
    assert state[:2] == before
    assert state[-1]["start_ms"] is None and state[-1]["end_ms"] is None
    assert state[-1]["unchanged_count"] == 0


@pytest.mark.parametrize("start", [850, 1000])
def test_disjoint_input_keeps_a_legitimate_repeated_phrase(start: int) -> None:
    state = _observe(_locked(), "Hello world again", start)
    assert reconstruct_text_from_words(state) == "Hello world Hello world again"


@pytest.mark.parametrize("text", ["Hello world", "WORLD!", "hello, WORLD!!!"])
def test_context_only_hypothesis_does_not_create_an_empty_atom(text: str) -> None:
    locked = _locked()
    assert _observe(locked, text) == locked


@pytest.mark.parametrize(
    "text", ["worldwide again", "helloworld again", "worlds again"]
)
def test_context_matching_never_removes_a_partial_word(text: str) -> None:
    state = _observe(_locked(), text)
    assert reconstruct_text_from_words(state) == f"Hello world {text}"


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        ("prefix HELLO, WORLD!  Again,  exactly!", "Again,  exactly!"),
        ("prefix HELLO, WORLD! “Again,  exactly!”", "“Again,  exactly!”"),
        ("prefix Novel,  exactly!", "Novel,  exactly!"),
    ],
)
def test_atom_retains_original_source_character_span(
    original: str, expected: str
) -> None:
    source_start = len("prefix ")
    state = reconcile_word_state(
        _locked(),
        [
            {
                "word": original[source_start:],
                "start_ms": None,
                "end_ms": None,
                "source_start": source_start,
                "source_end": len(original),
                "source_id": "original",
            }
        ],
        k=3,
        decode_id="span",
        window_start_ms=-1,
        audio_end_ms=4000,
        input_start_ms=0,
    )
    atom = state[-1]
    assert atom["word"] == expected
    assert original[atom["source_start"] : atom["source_end"]] == atom["word"]
    assert atom["source_id"] == "original"
    assert reconstruct_text_from_words(state) == f"Hello world {expected}"


def test_input_start_excludes_older_locks_from_suffix_matching() -> None:
    locked = _locked()
    assert reconstruct_text_from_words(_observe(locked, "Hello world again", 500)) == (
        "Hello world Hello world again"
    )
    assert reconstruct_text_from_words(_observe(locked, "world again", 500)) == (
        "Hello world again"
    )


def test_mixed_locks_keep_the_untimed_frontier_guard() -> None:
    locked = _locked()
    atom = {
        "word": "Earlier atom.",
        "start_ms": None,
        "end_ms": None,
        "locked": True,
        "observed_audio_end_ms": 100,
        "order": -1,
        "id": "earlier",
    }
    assert _observe([atom, *locked], "Hello world again", 0) == [atom, *locked]
    state = _observe([atom, *locked], "Hello world again", 100)
    assert reconstruct_text_from_words(state) == "Earlier atom. Hello world again"
    assert state[:3] == [atom, *locked]
