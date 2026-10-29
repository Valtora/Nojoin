"""Source spans cover the exact ASR wording without filling missing timings."""

from typing import Any

import numpy as np
import pytest

from backend.processing.alignment_spans import AlignmentFailure, map_alignment_spans
from backend.processing.forced_alignment import align_text


def entries(tokens: list[str]) -> list[dict[str, Any]]:
    return [
        {"text": token, "start_time": index * 0.2, "end_time": index * 0.2 + 0.1}
        for index, token in enumerate(tokens)
    ]


@pytest.mark.parametrize(
    "text,tokens",
    [
        ("Hello,  world!", ["Hello", "world"]),
        ('"Don\'t stop."', ["Don't", "stop"]),
        ("I can’t, won’t.", ["I", "cant", "wont"]),
        ("Café déjà vu — naïve!", ["Café", "déjà", "vu", "naïve"]),
        ("Cafe\u0301, déjà!", ["Cafe", "déjà"]),
        ("你好，世界！", ["你", "好", "世", "界"]),
        ("yes yes yes!", ["yes", "yes", "yes"]),
        ("well-known?", ["wellknown"]),
    ],
)
def test_lossless_original_spans_and_absolute_clock(
    text: str, tokens: list[str]
) -> None:
    result = map_alignment_spans(text, entries(tokens), 5.0, offset_ms=12_345)
    assert "".join(word.text for word in result.words) == text
    assert [text[word.source_start : word.source_end] for word in result.words] == [
        word.text for word in result.words
    ]
    assert result.words[0].source_start == 0
    assert result.words[-1].source_end == len(text)
    assert [(word.start_ms, word.end_ms) for word in result.words] == [
        (12_345 + index * 200, 12_445 + index * 200) for index in range(len(tokens))
    ]


@pytest.mark.parametrize(
    "tokens", [["one", "three"], ["one", "two"], ["two", "one", "three"]]
)
def test_dropped_or_reordered_word_rejects_whole_alignment(tokens: list[str]) -> None:
    with pytest.raises(ValueError, match="original"):
        map_alignment_spans("one two three", entries(tokens), 2.0)


@pytest.mark.parametrize(
    "start,end",
    [(0.2, 0.2), (0.3, 0.2), (-0.1, 0.2), (0, 2.1), (float("nan"), 1), (True, 1)],
)
def test_invalid_boundaries_are_not_repaired(start: float, end: float) -> None:
    with pytest.raises(ValueError):
        map_alignment_spans(
            "word", [{"text": "word", "start_time": start, "end_time": end}], 2.0
        )


def test_observed_overlapping_words_keep_their_actual_boundaries() -> None:
    result = map_alignment_spans(
        "Okay, okay.",
        [
            {"text": "Okay", "start_time": 0.4, "end_time": 1.12},
            {"text": "okay", "start_time": 1.04, "end_time": 1.12},
        ],
        2.0,
    )
    assert [(word.start_ms, word.end_ms) for word in result.words] == [
        (400, 1120),
        (1040, 1120),
    ]
    assert "".join(word.text for word in result.words) == "Okay, okay."


@pytest.mark.parametrize("start,end", [(0.3, 0.7), (0.6, 0.65)])
def test_words_out_of_start_or_end_order_are_left_untimed(
    start: float, end: float
) -> None:
    result = map_alignment_spans(
        "one two",
        [
            {"text": "one", "start_time": 0.4, "end_time": 0.7},
            {"text": "two", "start_time": start, "end_time": end},
        ],
        2.0,
    )
    assert (result.words[0].start_ms, result.words[0].end_ms) == (400, 700)
    assert (result.words[1].start_ms, result.words[1].end_ms) == (None, None)
    assert "".join(word.text for word in result.words) == "one two"


def test_missing_word_returns_typed_failure_with_original_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.processing import forced_aligner_model

    monkeypatch.setattr(
        forced_aligner_model, "align_audio", lambda *_args, **_kwargs: entries(["yes"])
    )
    result = align_text(
        np.zeros(16000, dtype=np.float32), text="yes yes!", language="en"
    )
    assert isinstance(result, AlignmentFailure)
    assert result.text == "yes yes!"
    assert "dropped" in result.reason


def test_unsupported_language_is_failure_and_text_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.processing import forced_aligner_model
    from backend.utils.forced_aligner_manifest import ForcedAlignerError

    def unsupported(*_args: Any, **_kwargs: Any) -> Any:
        raise ForcedAlignerError("Unsupported alignment language")

    monkeypatch.setattr(forced_aligner_model, "align_audio", unsupported)
    result = align_text(
        np.zeros(16000, dtype=np.float32), text="Original!", language="xx"
    )
    assert isinstance(result, AlignmentFailure)
    assert result.text == "Original!"
    assert "language" in result.reason
