"""Word boundaries survive mixed decode provenance and alignment outages."""

from typing import Any

from backend.utils.live_word_settling import (
    close_word_state,
    expire_word_state,
    plan_settling_window,
    reconcile_word_state,
)
from backend.utils.word_text import reconstruct_text_from_words

Word = dict[str, Any]


def test_exact_cjk_join_survives_a_locked_prefix_from_another_decode() -> None:
    prefix = {
        "id": "first",
        "order": 0,
        "word": "你",
        "start_ms": 100,
        "end_ms": 300,
        "source_id": "earlier",
        "source_start": 0,
        "source_end": 1,
        "locked": True,
        "lock_reason": "agreement",
    }
    state = [prefix]
    hypothesis = [
        {
            "word": "你",
            "start_ms": 100,
            "end_ms": 300,
            "source_id": "new",
            "source_start": 0,
            "source_end": 1,
        },
        {
            "word": "好",
            "start_ms": 300,
            "end_ms": 600,
            "source_id": "new",
            "source_start": 1,
            "source_end": 2,
        },
    ]
    for step in range(2):
        state = reconcile_word_state(
            state,
            hypothesis,
            k=1,
            decode_id=f"later-{step}",
            window_start_ms=-1,
            audio_end_ms=2000,
        )
    assert all(word["locked"] for word in state)
    assert state[0] == prefix
    assert state[0]["source_id"] != state[1]["source_id"]
    assert reconstruct_text_from_words(state) == "你好"


def test_explicit_exact_join_leaves_legacy_native_spacing_unchanged() -> None:
    assert reconstruct_text_from_words([{"word": "one"}, {"word": "two"}]) == "one two"
    assert (
        reconstruct_text_from_words(
            [
                {"word": "one", "source_id": "old"},
                {"word": "two", "source_id": "new", "join_left": True},
            ]
        )
        == "onetwo"
    )


def test_untimed_outage_expires_latest_atom_and_decodes_only_new_audio() -> None:
    state: list[Word] = []
    for end_ms in range(2000, 12000, 2000):
        state = reconcile_word_state(
            state,
            [{"word": f"Words through {end_ms}", "start_ms": None, "end_ms": None}],
            k=3,
            decode_id=f"step-{end_ms}",
            window_start_ms=end_ms - 10000,
            audio_end_ms=end_ms,
            input_start_ms=0,
        )
        assert len(state) == 1
        assert state[0]["first_observed_audio_end_ms"] == 2000
        assert state[0]["observed_audio_end_ms"] == end_ms
    start_ms = plan_settling_window(state, audio_end_ms=12000, window_ms=10000)
    assert start_ms == 10000
    state = reconcile_word_state(
        state,
        [{"word": "New speech", "start_ms": None, "end_ms": None}],
        k=3,
        decode_id="new-tail",
        window_start_ms=2000,
        audio_end_ms=12000,
        input_start_ms=start_ms,
    )
    assert [word["word"] for word in state] == ["Words through 10000", "New speech"]
    assert [word["locked"] for word in state] == [True, False]
    assert state[0]["lock_reason"] == "expiry"
    assert all(word["start_ms"] is None and word["end_ms"] is None for word in state)
    assert all(word["locked"] for word in close_word_state(state))


def test_overlapping_untimed_hypothesis_cannot_duplicate_expired_atom() -> None:
    atom = [{"word": "Hello world", "start_ms": None, "end_ms": None}]
    state = reconcile_word_state(
        [],
        atom,
        k=3,
        decode_id="first",
        window_start_ms=-8000,
        audio_end_ms=2000,
        input_start_ms=0,
    )
    state = reconcile_word_state(
        state,
        atom,
        k=3,
        decode_id="overlap",
        window_start_ms=2000,
        audio_end_ms=12000,
        input_start_ms=0,
    )
    assert [word["word"] for word in state] == ["Hello world"]
    assert state[0]["lock_reason"] == "expiry"


def test_timed_context_cannot_expire_a_newer_standalone_unaligned_atom() -> None:
    state = reconcile_word_state(
        [],
        [{"word": "Unplaced", "start_ms": None, "end_ms": None}],
        k=3,
        decode_id="atom",
        window_start_ms=-1,
        audio_end_ms=8000,
    )
    anchor = {
        "id": "anchor",
        "word": "Old",
        "start_ms": 0,
        "end_ms": 1000,
        "locked": True,
    }
    expired = expire_word_state([anchor, *state], window_start_ms=1000)
    assert not expired[1]["locked"]
    assert expired[1]["start_ms"] is None
