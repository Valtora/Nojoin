"""Sentence-start speaker correction: generator, each rule's veto, the combiner,
and the final merge (``backend/processing/sentence_start.py``, #813)."""

from __future__ import annotations

import numpy as np
import pytest

from backend.processing import sentence_start as ss
from backend.processing.diarization_union import native_annotation
from backend.processing.sentence_start import (
    EDITED_LINE_SPANS,
    RULES,
    Words,
    activity_from_frames,
    activity_from_turns,
    certain,
    complete_sentence,
    corrected,
    ends_sentence,
    keep_turn,
    settled,
)
from backend.tests.test_transcript_utils import FakeDiarization
from backend.utils.capture_resume import RESUME_BOUNDARIES
from backend.utils.transcript_utils import combine_transcription_diarization

# "It is done. so we go": L says the first sentence, T replies with "so we go".
# The diariser hears T from 1.0 s, while L's turn runs on to 1.3 s.
TOKENS = [
    (" It", 0.0, 0.3),
    (" is", 0.3, 0.6),
    (" done.", 0.6, 1.0),
    (" so", 1.05, 1.3),
    (" we", 1.3, 1.5),
    (" go", 1.5, 1.8),
]
LATE_CHANGE = ["L", "L", "L", "L", "T", "T"]
OVERLAP = [(0.0, 1.3, "L"), (1.0, 1.8, "T")]


def _words(tokens, labels, turns, **extra) -> Words:
    return Words(
        starts=[start for _, start, _ in tokens],
        ends=[end for _, _, end in tokens],
        marks=[ends_sentence(text) for text, _, _ in tokens],
        labels=labels,
        activity=extra.pop("activity", activity_from_turns(turns)),
        regions=extra.pop("regions", [(0, len(tokens))]),
        pause_s=extra.pop("pause_s", 1.0),
        **extra,
    )


def _without(rule):
    return [other for other in RULES if other is not rule]


def test_the_reply_word_after_the_sentence_end_moves_to_the_next_speaker():
    result = corrected(_words(TOKENS, LATE_CHANGE, OVERLAP))

    assert result == ["L", "L", "L", "T", "T", "T"]


@pytest.mark.parametrize(
    ("token", "ends"),
    [
        (" done.", True),
        (" really?", True),
        (" stop!", True),
        ("。", True),
        (" 好吗？", True),
        (' "done."', True),
        (" (right?)", True),
        (" so,", False),
        (" well;", False),
        (" so", False),
        (" wait…", False),
    ],
    ids=[
        "full-stop",
        "question",
        "exclamation",
        "ideographic-stop",
        "fullwidth-question",
        "closing-quote",
        "closing-bracket",
        "comma",
        "semicolon",
        "plain",
        "ellipsis-character",
    ],
)
def test_sentence_terminals_are_unicode_sentence_terminals(token, ends):
    """Closing brackets and quotes after a terminal still end a sentence;
    clause marks and U+2026 do not."""
    assert ends_sentence(token) is ends


def test_the_unicode_tables_are_the_generated_ones():
    """``scripts/generate_sentence_terminals.py --check`` in the suite: a table
    edited by hand fails here (#975). regex is pinned in requirements/test.txt,
    so newer Unicode data fails the Dependabot pull request that bumps it."""
    from scripts.generate_sentence_terminals import table_drift

    assert table_drift() == []


# --- generator ------------------------------------------------------------------
def _long_tail(count: int) -> tuple[list, list[str], list]:
    tokens = [(" done.", 0.0, 0.5)]
    tokens += [(" um", 0.5 + 0.2 * i, 0.7 + 0.2 * i) for i in range(count)]
    end = tokens[-1][2]
    tokens.append((" we", end, end + 0.3))
    labels = ["L"] * (count + 1) + ["T"]
    return tokens, labels, [(0.0, end, "L"), (0.4, end + 0.3, "T")]


def test_a_tail_of_eight_words_moves_and_nine_stay():
    eight = _long_tail(8)
    nine = _long_tail(9)

    assert corrected(_words(*eight))[1:9] == ["T"] * 8
    assert corrected(_words(*nine))[1:10] == ["L"] * 9


def test_no_candidate_crosses_a_region_or_an_unsafe_word():
    across_region = _words(TOKENS, LATE_CHANGE, OVERLAP, regions=[(0, 3), (3, 6)])
    unsafe_tail = _words(TOKENS, LATE_CHANGE, OVERLAP, unsafe=frozenset({3}))
    unsafe_next = _words(TOKENS, LATE_CHANGE, OVERLAP, unsafe=frozenset({4}))

    assert ss.candidates(across_region) == []
    assert ss.candidates(unsafe_tail) == []
    assert ss.candidates(unsafe_next) == []


def test_an_unknown_word_in_the_tail_or_after_it_makes_no_candidate():
    unknown_tail = ["L", "L", "L", "UNKNOWN", "T", "T"]
    unknown_next = ["L", "L", "L", "L", "UNKNOWN", "T"]

    assert ss.candidates(_words(TOKENS, unknown_tail, OVERLAP)) == []
    assert ss.candidates(_words(TOKENS, unknown_next, OVERLAP)) == []


# --- R1 keep_turn ----------------------------------------------------------------
def test_keep_turn_never_moves_a_speakers_whole_run():
    """The tail is all of L's speech: the word ending the sentence is X's."""
    labels = ["X", "X", "X", "L", "T", "T"]
    words = _words(TOKENS, labels, [(0.0, 1.0, "X"), *OVERLAP[1:], (1.0, 1.3, "L")])

    assert corrected(words) == labels
    assert corrected(words, _without(keep_turn))[3] == "T"


def test_keep_turn_vetoes_a_pause_longer_than_the_line_break_pause():
    tokens = [(" done.", 0.0, 0.5), (" so", 1.8, 2.0), (" we", 2.0, 2.3)]
    words = _words(tokens, ["L", "L", "T"], [(0.0, 2.0, "L"), (1.8, 2.3, "T")])

    assert corrected(words) == ["L", "L", "T"]
    assert corrected(words, _without(keep_turn)) == ["L", "T", "T"]
    # The owner's pause decides: at 1.5 s the same gap is not a pause.
    assert corrected(
        _words(tokens, ["L", "L", "T"], [], activity=words.activity, pause_s=1.5)
    ) == ["L", "T", "T"]


def test_keep_turn_vetoes_when_under_a_tenth_of_a_second_of_l_would_remain():
    tokens = [
        (" ok", 0.0, 0.4),
        (" Yes.", 0.45, 0.5),
        (" so", 0.5, 0.7),
        (" we", 0.7, 0.9),
    ]
    turns = [(0.0, 0.45, "T"), (0.45, 0.7, "L"), (0.55, 0.9, "T")]
    words = _words(tokens, ["T", "L", "L", "T"], turns)

    assert corrected(words) == ["T", "L", "L", "T"]
    assert corrected(words, _without(keep_turn)) == ["T", "L", "T", "T"]


def test_keep_turn_does_not_count_a_manual_word_as_ls_turn():
    """In the final pass a person's chosen speaker is applied later, so a
    manual word before the tail is nobody's remainder yet."""
    tokens = [(" Fine.", 0.0, 0.3), (" so", 0.35, 0.6), (" we", 0.6, 0.8)]
    turns = [(0.0, 0.6, "L"), (0.4, 0.8, "T")]
    plain = _words(tokens, ["L", "L", "T"], turns)
    manual_p = _words(tokens, ["L", "L", "T"], turns, manual=frozenset({0}))

    assert corrected(plain) == ["L", "T", "T"]
    assert keep_turn(ss.candidates(manual_p)[0], manual_p)


# --- R2 complete_sentence ----------------------------------------------------------
def test_complete_sentence_keeps_a_tail_that_ends_a_sentence():
    tokens = [(" done.", 0.0, 0.5), (" Right.", 0.55, 0.8), (" we", 0.8, 1.0)]
    words = _words(tokens, ["L", "L", "T"], [(0.0, 0.8, "L"), (0.6, 1.0, "T")])

    assert corrected(words) == ["L", "L", "T"]
    assert corrected(words, _without(complete_sentence)) == ["L", "T", "T"]


# --- R3 settled ------------------------------------------------------------------
def test_settled_keeps_a_tail_holding_a_word_locked_on_l():
    tokens = [*TOKENS[:4], (" um", 1.3, 1.4), (" we", 1.4, 1.6), (" go", 1.6, 1.8)]
    labels = ["L", "L", "L", "L", "L", "T", "T"]
    turns = [(0.0, 1.4, "L"), (1.0, 1.8, "T")]
    locked_l = _words(tokens, labels, turns, stored=labels, locked=frozenset({3}))

    assert corrected(locked_l) == labels
    # Without the rule the move would split the tail: L T where "so um" was.
    assert corrected(locked_l, _without(settled))[3:5] == ["L", "T"]


def test_a_tail_word_locked_after_an_earlier_move_lets_the_rest_follow():
    tokens = [*TOKENS[:4], (" um", 1.3, 1.4), (" we", 1.4, 1.6), (" go", 1.6, 1.8)]
    raw = ["L", "L", "L", "L", "L", "T", "T"]
    shown = ["L", "L", "L", "T", "L", "T", "T"]
    turns = [(0.0, 1.4, "L"), (1.0, 1.8, "T")]

    words = _words(tokens, raw, turns, stored=shown, locked=frozenset({3}))

    assert corrected(words)[3:5] == ["L", "T"]  # the locked word keeps its label
    assert not settled(ss.candidates(words)[0], words)


@pytest.mark.parametrize("manual", [{3}, {4}])
def test_settled_never_moves_out_of_or_into_a_line_a_person_edited(manual):
    words = _words(TOKENS, LATE_CHANGE, OVERLAP, manual=frozenset(manual))

    assert corrected(words) == LATE_CHANGE
    assert corrected(words, _without(settled))[3] == "T"


# --- R4 certain ------------------------------------------------------------------
def test_certain_keeps_a_word_holding_none_of_the_next_speakers_activity():
    after_so = [(0.0, 1.3, "L"), (1.3, 1.8, "T")]
    words = _words(TOKENS, LATE_CHANGE, after_so)

    assert corrected(words) == LATE_CHANGE
    assert corrected(words, _without(certain))[3] == "T"


def test_certain_keeps_the_whole_tail_when_any_word_is_certain():
    """All or nothing: "so" holds T, "um" does not."""
    tokens = [*TOKENS[:4], (" um", 1.3, 1.5), (" we", 1.6, 1.8)]
    labels = ["L", "L", "L", "L", "L", "T"]
    turns = [(0.0, 1.5, "L"), (1.0, 1.2, "T"), (1.6, 1.8, "T")]

    assert corrected(_words(tokens, labels, turns)) == labels


def test_certain_waits_for_evidence_that_has_not_arrived():
    activity = activity_from_turns(OVERLAP, coverage=(0.0, 1.2))
    words = _words(TOKENS, LATE_CHANGE, [], activity=activity)

    assert corrected(words) == LATE_CHANGE
    assert corrected(words, _without(certain))[3] == "T"


@pytest.mark.parametrize(("t_from", "moved"), [(1.05, True), (1.1, False)])
def test_certain_reads_a_zero_length_word_at_its_instant(t_from, moved):
    tokens = [(" done.", 0.0, 0.5), (" so", 1.05, 1.05), (" we", 1.1, 1.3)]
    turns = [(0.0, 1.1, "L"), (t_from, 1.3, "T")]

    result = corrected(_words(tokens, ["L", "L", "T"], turns))

    assert result[1] == ("T" if moved else "L")


def _frames(first: int, runs: dict[int, tuple[int, int]], count: int):
    """Posteriors from ``first`` for ``count`` frames, channel k active over its run."""
    posteriors = np.zeros((count, 2), np.float32)
    for k, (start, stop) in runs.items():
        posteriors[start - first : stop - first, k] = 1.0
    return posteriors


@pytest.mark.parametrize(
    ("first", "runs", "so"),
    [
        # T starts at frame 35; "so" is a zero-length word at 350 ms.
        (0, {0: (0, 35), 1: (35, 60)}, (0.35, 0.35)),
        # Evidence retained from frame 35; "so" starts at 350 ms.
        (35, {1: (35, 60)}, (0.35, 0.45)),
    ],
    ids=["run-start", "coverage-start"],
)
def test_native_activity_starting_exactly_at_a_word_counts(first, runs, so):
    """Frame 35 is the same instant as a word at 350 ms (I1)."""
    tokens = [(" done.", 0.0, 0.3), (" so", *so), (" we", 0.46, 0.55)]
    activity = activity_from_frames(
        _frames(first, runs, 60 - first), ["L", "T"], first, 60
    )

    result = corrected(_words(tokens, ["L", "L", "T"], [], activity=activity))

    assert result == ["L", "T", "T"]


def test_the_final_merge_reads_a_native_run_starting_exactly_at_a_word():
    """The final pass reads native posteriors as 10 ms runs the same way: a
    zero-length word at 350 ms belongs to the channel that starts at frame 35."""
    probabilities = np.zeros((60, 8), np.float32)
    probabilities[0:35, 0] = 1.0
    probabilities[35:60, 1] = 1.0
    tokens = [
        (" It", 0.0, 0.1),
        (" is", 0.1, 0.2),
        (" done.", 0.2, 0.3),
        *[(" so", 0.35, 0.35), (" we", 0.36, 0.45), (" go", 0.45, 0.55)],
    ]

    result = combine_transcription_diarization(
        _whisper(tokens), native_annotation(probabilities)
    )

    assert _lines(result) == [("L0", "It is done."), ("L1", "so we go")]


def test_activity_at_an_instant_counts_a_run_end_only_where_nothing_starts():
    activity = activity_from_turns([(0.0, 1.0, "L"), (1.0, 2.0, "T"), (3.0, 4.0, "X")])

    assert not activity.at("L", 1.0) and activity.at("T", 1.0)
    assert activity.at("X", 4.0)


# --- combiner ----------------------------------------------------------------------
def test_the_combiner_moves_each_candidate_no_rule_vetoes_judged_on_the_input():
    """Two changes in one list; a rule that vetoes one leaves the other. Each
    candidate is judged on the labels as they came in."""
    tokens = [*TOKENS, (" Fine.", 1.85, 2.2), (" ok", 2.25, 2.5), (" now", 2.5, 2.8)]
    labels = [*LATE_CHANGE, "T", "T", "U"]
    turns = [*OVERLAP, (1.8, 2.5, "T"), (2.3, 2.8, "U")]
    words = _words(tokens, labels, turns)

    def veto_u(c, _words):
        return c.T == "U"

    assert corrected(words) == ["L", "L", "L", "T", "T", "T", "T", "U", "U"]
    assert corrected(words, [*RULES, veto_u]) == [
        "L",
        "L",
        "L",
        "T",
        "T",
        "T",
        "T",
        "T",
        "U",
    ]


# --- final merge --------------------------------------------------------------------
def _whisper(tokens) -> dict:
    words = [{"word": text, "start": start, "end": end} for text, start, end in tokens]
    return {
        "segments": [
            {
                "start": tokens[0][1],
                "end": tokens[-1][2],
                "text": "".join(text for text, _, _ in tokens),
                "words": words,
            }
        ]
    }


def _lines(result) -> list[tuple[str, str]]:
    return [(segment["speaker"], segment["text"]) for segment in result]


def test_the_final_merge_moves_a_whisper_reply_start_to_the_replying_speaker():
    result = combine_transcription_diarization(
        _whisper(TOKENS), FakeDiarization(OVERLAP)
    )

    assert _lines(result) == [("L", "It is done."), ("T", "so we go")]


def test_the_final_merge_moves_a_parakeet_reply_start():
    """Parakeet words carry token starts only; their ends are built from the next
    start and the word length cap."""
    from backend.processing.engines.onnx_asr_engine import (
        _Recognition,
        map_onnx_asr_recognition,
    )

    transcription = map_onnx_asr_recognition(
        _Recognition(
            " It is done. so we go",
            [text for text, _, _ in TOKENS],
            [start for _, start, _ in TOKENS],
        ),
        audio_duration=2.0,
    )

    result = combine_transcription_diarization(transcription, FakeDiarization(OVERLAP))

    assert _lines(result) == [("L", "It is done."), ("T", "so we go")]


@pytest.mark.parametrize(
    "config",
    [
        {EDITED_LINE_SPANS: [(1000, 1300)]},  # "so" lies in an edited line
        {RESUME_BOUNDARIES: [1020]},  # a capture resume between "done." and "so"
    ],
)
def test_the_final_merge_leaves_edited_lines_and_resume_cuts_alone(config):
    result = combine_transcription_diarization(
        _whisper(TOKENS), FakeDiarization(OVERLAP), config
    )

    assert [seg["speaker"] for seg in result if "so" in seg["text"]] == ["L"]


def test_the_final_merge_skips_a_tail_carrying_an_untimed_word():
    transcription = _whisper(TOKENS)
    words = transcription["segments"][0]["words"]
    words.insert(4, {"word": " uh", "start": None, "end": None, "source_id": "s"})

    result = combine_transcription_diarization(transcription, FakeDiarization(OVERLAP))

    assert _lines(result)[0] == ("L", "It is done. so uh")
