"""Review regressions at the native/forced timing and consolidation boundaries."""

from types import SimpleNamespace
from typing import Any

import pytest

from backend.processing.alignment_spans import map_alignment_spans
from backend.processing.forced_aligner_model import raw_alignment
from backend.processing.live_asr_payload import _prepare_live_region
from backend.utils import transcript_utils
from backend.worker.tasks.final_segments import combine_and_consolidate_segments


@pytest.mark.parametrize(
    "native_words", [["yes", "please"], ["yes", "yes", "yes", "please"]]
)
def test_native_repetition_keeps_head_text_and_unfiltered_confidence_words(
    native_words: list[str],
) -> None:
    words = [
        {"word": word, "start": index / 2, "end": index / 2 + 0.4}
        for index, word in enumerate(native_words)
    ]
    text, segments = _prepare_live_region(
        {
            "segments": [
                {"text": "yes yes yes please", "start": 0, "end": 3, "words": words}
            ]
        },
        0,
    )
    assert text == "yes please"
    assert segments[0]["text"] == "yes yes yes please"
    assert segments[0]["words"] == words


def test_native_context_trim_keeps_head_contraction_and_punctuation_spacing() -> None:
    words = [
        {"word": word, "start": index, "end": index + 0.5}
        for index, word in enumerate(["context", "I", "'ll", "go", "."])
    ]
    text, segments = _prepare_live_region(
        {
            "segments": [
                {"start": 0, "end": 5, "text": "context I'll go.", "words": words}
            ]
        },
        1,
    )
    assert text == "I 'll go ."
    assert [word["word"] for word in segments[0]["words"]] == ["I", "'ll", "go", "."]


def test_native_text_only_result_survives_without_second_extraction() -> None:
    text, segments = _prepare_live_region({"text": "hello there"}, 0)
    assert text == "hello there"
    assert segments == [{"start": 0, "end": 0, "text": "hello there"}]


def test_thirty_second_alignment_failure_keeps_original_wordless_window() -> None:
    source = {
        "start": 0,
        "end": 30,
        "text": "Bedankt!",
        "alignment_error": "Unplaced word",
    }
    final = combine_and_consolidate_segments(
        {"segments": [source]}, None, enable_diarization=False, recording_id=1
    )
    assert len(final) == 1
    assert {key: final[0][key] for key in source} == source
    assert "words" not in final[0]


@pytest.mark.parametrize("forced", [False, True], ids=["native", "forced"])
def test_ten_thousand_words_reconstruct_at_most_once_per_closed_segment(
    monkeypatch: pytest.MonkeyPatch, forced: bool
) -> None:
    from pyannote.core import Annotation, Segment

    turns = Annotation()
    turns[Segment(0, 10001)] = "A"
    words = [
        {
            "word": " hello",
            "start": index,
            "end": index + 0.8,
            **({"source_id": "source"} if forced else {}),
        }
        for index in range(10000)
    ]
    sizes: list[int] = []
    original = transcript_utils.reconstruct_text_from_words

    def reconstruct(entries: list[dict[str, Any]]) -> str:
        sizes.append(len(entries))
        return original(entries)

    monkeypatch.setattr(transcript_utils, "reconstruct_text_from_words", reconstruct)
    final = transcript_utils._combine_word_level([{"words": words}], turns)
    assert len(final) == 1
    assert final[0]["text"] == " ".join(["hello"] * 10000)
    assert final[0]["words"] == words
    assert sizes == ([10000] if forced else [])


def test_raw_timestamp_bins_map_at_exact_clip_end() -> None:
    import torch

    logits = torch.zeros((1, 2, 21))
    logits[0, torch.arange(2), [1, 20]] = 1
    config = SimpleNamespace(
        num_labels=21, timestamp_token_id=7, timestamp_segment_time=80
    )
    raw = raw_alignment(logits, torch.full((1, 2), 7), ["End"], config, duration=1.6)
    result = map_alignment_spans("End!", raw, duration_s=1.6, offset_ms=1000)
    assert result.words[0].text == "End!"
    assert (result.words[0].start_ms, result.words[0].end_ms) == (1080, 2600)


def test_overlapping_remainder_starts_at_first_remaining_word() -> None:
    words = [
        {"word": "First.", "start": 0, "end": 10.08},
        {"word": " Second", "start": 10, "end": 10.5},
        {"word": " last.", "start": 10.5, "end": 15},
    ]
    final = transcript_utils.consolidate_diarized_transcript(
        [
            {
                "start": 0,
                "end": 15,
                "speaker": "A",
                "text": "First. Second last.",
                "words": words,
            }
        ]
    )
    assert len(final) == 2
    assert final[0]["end"] == 10.08
    assert final[1]["start"] == final[1]["words"][0]["start"] == 10
