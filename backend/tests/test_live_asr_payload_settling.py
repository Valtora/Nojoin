"""Live repetition guards retain the matching exact native word spans."""

from backend.processing.live_asr_payload import (
    _build_live_confidence_payload,
    _prepare_live_region,
)
from backend.utils.word_text import reconstruct_text_from_words


def test_native_words_keep_unicode_punctuation_whitespace_and_original_spans() -> None:
    original = "Ça,  I'll go—明天!"
    words = [
        {"word": token, "start": index, "end": index + 0.5}
        for index, token in enumerate(["Ça", "I", "'ll", "go", "明天"])
    ]
    text, segments = _prepare_live_region(
        {"segments": [{"text": original, "start": 0, "end": 5, "words": words}]},
        0,
        exact_spans=True,
    )
    assert text == original
    payload = _build_live_confidence_payload(
        region_segment_payloads=segments,
        region_start_ms=10000,
        region_end_ms=15000,
    )
    stored = payload["asr_segments"][0]["words"]
    assert reconstruct_text_from_words(stored) == original
    assert [original[word["source_start"] : word["source_end"]] for word in stored] == [
        word["word"] for word in stored
    ]
    assert stored[-1]["start_ms"] == 14000


def test_phrase_repetition_filters_native_word_payload_and_text_atomically() -> None:
    tokens = "hello there hello there hello there goodbye".split()
    words = [
        {"word": token, "start": index, "end": index + 0.5}
        for index, token in enumerate(tokens)
    ]
    text, segments = _prepare_live_region(
        {
            "segments": [
                {"text": " ".join(tokens), "start": 0, "end": 7, "words": words}
            ]
        },
        0,
        exact_spans=True,
    )
    assert text == "hello there goodbye"
    assert reconstruct_text_from_words(segments[0]["words"]) == text
    assert [word["start"] for word in segments[0]["words"]] == [0, 1, 6]


def test_context_trimming_retains_the_exact_native_source_suffix() -> None:
    text, segments = _prepare_live_region(
        {
            "segments": [
                {
                    "text": "context  Hello, world!",
                    "start": 0,
                    "end": 3,
                    "words": [
                        {"word": "context", "start": 0, "end": 0.5},
                        {"word": "Hello,", "start": 1.2, "end": 1.7},
                        {"word": "world!", "start": 2, "end": 2.5},
                    ],
                }
            ]
        },
        1,
        exact_spans=True,
    )
    assert text == "Hello, world!"
    assert [word["source_start"] for word in segments[0]["words"]] == [7, 15]


def test_native_evidence_already_matching_filtered_target_gets_exact_spans() -> None:
    text, segments = _prepare_live_region(
        {
            "segments": [
                {
                    "text": "yes yes yes please",
                    "start": 0,
                    "end": 2,
                    "words": [
                        {"word": "yes", "start": 0, "end": 0.5},
                        {"word": "please", "start": 1, "end": 1.5},
                    ],
                }
            ]
        },
        0,
        exact_spans=True,
    )
    assert text == "yes please"
    assert [word["source_start"] for word in segments[0]["words"]] == [0, 3]
    assert reconstruct_text_from_words(segments[0]["words"]) == text
