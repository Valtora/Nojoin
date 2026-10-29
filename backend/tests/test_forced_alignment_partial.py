"""Per-word retention keeps AMI-derived predictions without repairing timestamps."""

from types import SimpleNamespace
from typing import Any

import pytest

from backend.processing.alignment_spans import map_alignment_spans
from backend.processing.forced_aligner_model import raw_alignment
from backend.processing.live_asr_payload import (
    _build_live_confidence_payload,
    _extract_region_segment_payloads,
    _filter_region_repetitions,
)
from backend.processing.unheard_words import remove_unheard_words
from backend.utils.transcript_utils import (
    _combine_word_level,
    consolidate_diarized_transcript,
)
from backend.utils.word_text import reconstruct_text_from_words
from backend.worker.tasks.final_segments import combine_and_consolidate_segments

# Raw numeric predictions only; no audio. AMI Meeting Corpus, AMI Consortium,
# CC BY 4.0. EN2002a dev sample, Parakeet stand-in wording, pinned Qwen predictions
# from .scratch/settling/aligner-validity/predictions.jsonl (2026-10-26).
# These two windows both failed whole-window policy A. Values are milliseconds.
SAVED_WINDOWS = [
    (
        0.0,
        18.0,
        "I wonder how much of the meetings is talking about the stuff at the meetings. "
        "So what do we need to talk about? Shall we just go round and everyone says what they",
        [
            0,
            560,
            960,
            1200,
            1200,
            1280,
            1280,
            1440,
            1440,
            1520,
            1600,
            1600,
            1600,
            2000,
            2000,
            2160,
            2240,
            2560,
            2560,
            2720,
            2720,
            2800,
            2800,
            2960,
            3040,
            3040,
            3120,
            3200,
            3200,
            3520,
            12320,
            12480,
            13200,
            13360,
            13280,
            13440,
            13440,
            13520,
            13520,
            13760,
            13840,
            14000,
            14080,
            14320,
            14320,
            14480,
            15440,
            15520,
            15520,
            15680,
            15680,
            15840,
            15840,
            15920,
            16000,
            16320,
            16320,
            16640,
            16800,
            17200,
            17200,
            17360,
            17360,
            17600,
            17600,
            17760,
        ],
    ),
    (
        47.6,
        19.5,
        "Okay. Okay. Sorry. Yeah. So yeah, we've got a browser with um which comes up "
        "automatically with the transcription box and the topics. And then when you go "
        "on the menu, you can select a summarization box which pops up and an audio player.",
        [
            720,
            1120,
            1120,
            1360,
            1760,
            2080,
            2240,
            2400,
            2800,
            3360,
            3600,
            3760,
            3840,
            4000,
            4000,
            4160,
            4160,
            4160,
            4160,
            4640,
            4640,
            5200,
            5600,
            6160,
            6800,
            7040,
            7040,
            7280,
            7280,
            7440,
            7440,
            8160,
            8160,
            8480,
            8560,
            8720,
            8720,
            9440,
            9440,
            9920,
            10000,
            10480,
            10480,
            10560,
            10560,
            11120,
            11760,
            11840,
            11840,
            12080,
            12080,
            12160,
            12160,
            12320,
            12320,
            12480,
            12480,
            12560,
            12560,
            12640,
            12640,
            13280,
            13440,
            13600,
            13600,
            14080,
            14080,
            14560,
            14880,
            15040,
            15120,
            15840,
            15840,
            16320,
            16400,
            16480,
            16560,
            16800,
            16800,
            17040,
            17520,
            18080,
            18320,
            18400,
            18480,
            18720,
            18720,
            18960,
        ],
    ),
]


@pytest.mark.parametrize("offset,duration,text,raw_ms", SAVED_WINDOWS)
def test_saved_rejected_windows_keep_over_93_percent_without_changing_text_or_times(
    offset: float, duration: float, text: str, raw_ms: list[int]
) -> None:
    import torch

    tokens = [token.strip(".,?!") for token in text.split()]
    assert len(raw_ms) == 2 * len(tokens)
    assert any(start == end for start, end in zip(raw_ms[::2], raw_ms[1::2]))
    bins = torch.tensor(raw_ms) // 80
    n_labels = int(bins.max()) + 1
    logits = torch.zeros((1, len(bins), n_labels))
    logits[0, torch.arange(len(bins)), bins] = 1
    config = SimpleNamespace(
        num_labels=n_labels, timestamp_token_id=7, timestamp_segment_time=80
    )
    raw = raw_alignment(logits, torch.full((1, len(bins)), 7), tokens, config, duration)
    mapped = map_alignment_spans(text, raw, duration, round(offset * 1000))
    assert "".join(word.text for word in mapped.words) == text
    timed = 0
    for word, start, end in zip(mapped.words, raw_ms[::2], raw_ms[1::2]):
        if word.start_ms is not None:
            timed += 1
            assert (word.start_ms, word.end_ms) == (
                round(offset * 1000) + start,
                round(offset * 1000) + end,
            )
        else:
            assert word.end_ms is None
    assert timed / len(tokens) >= 0.93


@pytest.fixture
def mixed_segment() -> dict[str, Any]:
    text = "Edge,  valid! interior? kept. last!"
    entries = [
        {"text": token, "start_time": start, "end_time": end}
        for token, start, end in [
            ("Edge", 0, 0),
            ("valid", 2, 3),
            ("interior", 4, 3),
            ("kept", 12, 13),
            ("last", 29, 31),
        ]
    ]
    mapped = map_alignment_spans(text, entries, 30)
    return {
        "start": 0,
        "end": 30,
        "text": text,
        "words": [
            {
                "word": word.text,
                "start": word.start_ms / 1000 if word.start_ms is not None else None,
                "end": word.end_ms / 1000 if word.end_ms is not None else None,
                "source_id": "mixed",
                "source_start": word.source_start,
                "source_end": word.source_end,
            }
            for word in mapped.words
        ],
    }


@pytest.mark.parametrize("diarise", [False, True])
def test_mixed_final_consumers_keep_text_and_never_split_untimed_words(
    mixed_segment: dict[str, Any], diarise: bool
) -> None:
    from pyannote.core import Annotation, Segment

    turns = Annotation()
    turns[Segment(0, 10)] = "A"
    turns[Segment(10, 30)] = "B"
    if diarise:
        direct = _combine_word_level([mixed_segment], turns)
        assert [s["speaker"] for s in direct] == [
            "A",
            "B",
        ]
    final = combine_and_consolidate_segments(
        {"segments": [mixed_segment]},
        turns if diarise else None,
        enable_diarization=diarise,
        recording_id=1,
    )
    words = [word for segment in final for word in segment.get("words", [])]
    assert words == mixed_segment["words"]
    assert reconstruct_text_from_words(words) == mixed_segment["text"]
    assert all(not segment.get("alignment_error") for segment in final)
    # Without diarisation the normal soft split may keep this 11-second line.
    assert [segment["text"] for segment in final] == (
        ["Edge,  valid! interior?", "kept. last!"]
        if diarise
        else [mixed_segment["text"]]
    )
    assert [(s["start"], s["end"]) for s in final] == (
        [(2, 3), (12, 13)] if diarise else [(2, 13)]
    )
    reconsolidated = consolidate_diarized_transcript(final)
    assert [word for s in reconsolidated for word in s["words"]] == words
    assert [(s["start"], s["end"], s["text"]) for s in reconsolidated] == [
        (s["start"], s["end"], s["text"]) for s in final
    ]


@pytest.mark.parametrize("prefix", [0.0, 10.0, 29.0])
def test_mixed_live_context_payload_keeps_unplaced_words_without_offsetting_them(
    mixed_segment: dict[str, Any], prefix: float
) -> None:
    region = _extract_region_segment_payloads({"segments": [mixed_segment]}, prefix)
    evidence = _build_live_confidence_payload(
        region_segment_payloads=region,
        region_start_ms=50_000,
        region_end_ms=80_000,
    )
    words = evidence["asr_segments"][0]["words"]
    untimed = [word for word in words if word["start_ms"] is None]
    assert [word["word"] for word in untimed] == ["Edge,", " interior?", " last!"]
    assert all(word["end_ms"] is None for word in untimed)
    timed = [word for word in words if word["start_ms"] is not None]
    assert evidence["asr_word_timestamps_available"] is bool(timed)
    for word in timed:
        original = next(w for w in mixed_segment["words"] if w["word"] == word["word"])
        assert word["start_ms"] == 50_000 + round((original["start"] - prefix) * 1000)
    assert not evidence["asr_segments"][0].get("alignment_error")


def test_mixed_phantom_filter_keeps_unknown_positions_and_original_envelope(
    mixed_segment: dict[str, Any],
) -> None:
    filtered, dropped = remove_unheard_words(
        {"segments": [mixed_segment]}, [(1, 4), (11, 14)]
    )
    assert dropped.words == 2
    segment = filtered["segments"][0]
    assert segment["text"] == "Edge, interior? last!"
    assert all(
        word["start"] is None and word["end"] is None for word in segment["words"]
    )
    assert (segment["start"], segment["end"]) == (0, 30)


def test_mixed_repetitions_cannot_remove_unplaced_text() -> None:
    segment = {
        "start": 0,
        "end": 3,
        "text": "yes yes yes",
        "words": [
            {"word": word, "start": start, "end": end, "source_id": "repeat"}
            for word, start, end in [
                ("yes", 0, 1),
                (" yes", None, None),
                (" yes", 2, 3),
            ]
        ],
    }
    assert _filter_region_repetitions([segment]) == [segment]


def test_rejected_predictions_never_anchor_later_words() -> None:
    mapped = map_alignment_spans(
        "one two three four five",
        [
            {"text": token, "start_time": start, "end_time": end}
            for token, start, end in [
                ("one", 2, 3),
                ("two", 9, 8),
                ("three", 1, 4),
                ("four", 3, 4),
                ("five", 4, 4),
            ]
        ],
        10,
    )
    assert [(word.start_ms, word.end_ms) for word in mapped.words] == [
        (2000, 3000),
        (None, None),
        (None, None),
        (3000, 4000),
        (None, None),
    ]


def test_no_retained_predictions_is_still_an_alignment_failure() -> None:
    import torch

    from backend.utils.forced_aligner_manifest import ForcedAlignerError

    config = SimpleNamespace(
        num_labels=2, timestamp_token_id=7, timestamp_segment_time=80
    )
    with pytest.raises(ForcedAlignerError, match="no usable"):
        raw_alignment(
            torch.zeros((1, 4, 2)), torch.full((1, 4), 7), ["one", "two"], config, 1
        )


def test_short_segment_fold_cannot_absorb_unplaced_words() -> None:
    source = [
        {"start": 0, "end": 0.05, "speaker": "A", "text": "Short", "words": []},
        {
            "start": 0.05,
            "end": 2,
            "speaker": "A",
            "text": "unknown!",
            "words": [
                {
                    "word": "unknown!",
                    "start": None,
                    "end": None,
                    "source_id": "unplaced",
                }
            ],
        },
    ]
    final = consolidate_diarized_transcript(source)
    assert len(final) == 2
    assert final[1]["text"] == "unknown!"
    assert final[1]["words"] == []
    assert final[1]["alignment_error"] == "No usable word timings"
    assert (final[1]["start"], final[1]["end"]) == (0.05, 2)


def test_split_keeps_unknown_words_with_previous_timed_anchor() -> None:
    words = [
        {"word": word, "start": start, "end": end, "source_id": "split"}
        for word, start, end in [
            ("Leading", None, None),
            (" timed.", 0, 9),
            (" unknown", None, None),
            (" next.", 9, 18),
            (" trailing", None, None),
        ]
    ]
    final = consolidate_diarized_transcript(
        [
            {
                "start": 0,
                "end": 30,
                "speaker": "A",
                "words": words,
                "text": reconstruct_text_from_words(words),
            }
        ],
        max_duration_s=10.0,
    )
    assert [(s["start"], s["end"], s["text"]) for s in final] == [
        (0, 9, "Leading timed. unknown"),
        (9, 18, "next. trailing"),
    ]
    assert [word for s in final for word in s["words"]] == words
