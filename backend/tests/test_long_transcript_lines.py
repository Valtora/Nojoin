"""New 30 s lines keep natural boundaries, export content and note anchors."""

from copy import deepcopy
from typing import Any

import pytest

from backend.processing.live_limits import DEFAULT_MAX_SEGMENT_S
from backend.services.exports import build_document, render
from backend.tests.export_fixtures import exported_text, meeting_parts
from backend.tests.test_export_timeline_notes import _note
from backend.tests.test_transcript_utils import FakeDiarization
from backend.utils.canonical_pipeline.live_labels import LiveLabelDecision
from backend.utils.canonical_pipeline.live_settling_lines import partition_words
from backend.utils.config_manager import DEFAULT_SYSTEM_CONFIG
from backend.utils.transcript_utils import (
    combine_transcription_diarization,
    consolidate_diarized_transcript,
)


def _line(length: int, *, sentence_at: int | None = None) -> dict[str, Any]:
    words = [
        {
            "start": float(i),
            "end": float(i + 1),
            "word": f"word{i}" + ("." if i + 1 == sentence_at else ""),
        }
        for i in range(length)
    ]
    return {
        "start": 0.0,
        "end": float(length),
        "speaker": "A",
        "text": " ".join(word["word"] for word in words),
        "words": words,
    }


def test_default_final_and_both_live_lanes_share_thirty_second_limit() -> None:
    assert DEFAULT_MAX_SEGMENT_S == DEFAULT_SYSTEM_CONFIG["live_max_segment_s"] == 30.0
    source = _line(65)
    result = consolidate_diarized_transcript([deepcopy(source)])
    assert [(line["start"], line["end"]) for line in result] == [
        (0, 30),
        (30, 60),
        (60, 65),
    ]
    assert " ".join(line["text"] for line in result) == source["text"]
    words = [
        {
            "id": str(i),
            "word": f"word{i}",
            "start_ms": i * 1000,
            "end_ms": (i + 1) * 1000,
            "locked": True,
        }
        for i in range(65)
    ]
    partitions = partition_words(
        words, [LiveLabelDecision("A", True, 1.0)] * len(words), closing=True
    )
    assert [len(part.words) for part in partitions] == [30, 30, 5]
    assert all(part.line_locked for part in partitions)


def test_final_prefers_sentence_end_near_thirty_second_target() -> None:
    result = consolidate_diarized_transcript([_line(40, sentence_at=28)])
    assert [(line["start"], line["end"]) for line in result] == [(0, 28), (28, 40)]
    assert result[0]["text"].endswith("word27.")
    assert result[1]["text"].startswith("word28 ")


def test_final_sentence_window_scales_above_target_as_well_as_below() -> None:
    result = consolidate_diarized_transcript([_line(40, sentence_at=32)])
    assert [(line["start"], line["end"]) for line in result] == [(0, 32), (32, 40)]
    assert result[0]["text"].endswith("word31.")


def test_custom_cap_changes_sentence_search_window_together() -> None:
    result = consolidate_diarized_transcript(
        [_line(40, sentence_at=18)], max_duration_s=20
    )
    assert [(line["start"], line["end"]) for line in result] == [
        (0, 18),
        (18, 38),
        (38, 40),
    ]


def test_sparse_words_split_below_cap_without_eating_next_word() -> None:
    source = {
        "start": 0.0,
        "end": 40.0,
        "speaker": "A",
        "text": "hello there",
        "words": [
            {"start": 0.0, "end": 2.0, "word": "hello"},
            {"start": 2.0, "end": 40.0, "word": "there"},
        ],
    }
    result = consolidate_diarized_transcript([source])
    assert [(line["text"], line["start"], line["end"]) for line in result] == [
        ("hello", 0, 2),
        ("there", 2, 40),
    ]


def test_wordless_fallback_keeps_text_and_thirty_second_spans() -> None:
    source = _line(65)
    del source["words"]
    result = consolidate_diarized_transcript([source])
    assert [line["end"] - line["start"] for line in result] == [30, 30, 5]
    assert " ".join(line["text"] for line in result) == source["text"]


def test_speaker_change_and_crosstalk_keep_separate_credited_lines() -> None:
    result = consolidate_diarized_transcript(
        [
            {"start": 0, "end": 20, "speaker": "A", "text": "Main turn"},
            {"start": 19, "end": 25, "speaker": "B", "text": "Interjection"},
            {"start": 25, "end": 30, "speaker": "A", "text": "Continued"},
        ]
    )
    assert [(line["speaker"], line["text"]) for line in result] == [
        ("A", "Main turn"),
        ("B", "Interjection"),
        ("A", "Continued"),
    ]


@pytest.mark.parametrize("gap,expected", [(1.0, 1), (1.01, 2)])
def test_final_word_grouping_retains_existing_one_second_pause_boundary(
    gap: float, expected: int
) -> None:
    source = _line(20)
    for word in source["words"][10:]:
        word["start"] += gap
        word["end"] += gap
    source["end"] += gap
    combined = combine_transcription_diarization(
        {"segments": [source]}, FakeDiarization([(0, 30, "A")])
    )
    result = consolidate_diarized_transcript(combined)
    assert len(result) == expected
    assert " ".join(line["text"] for line in result) == source["text"]


@pytest.mark.parametrize("export_format", ["txt", "pdf", "docx"])
def test_thirty_second_line_exports_all_words_and_places_internal_note_after_line(
    export_format: str,
) -> None:
    # Enough text to wrap heavily and exercise PDF page flow, including the tail.
    wording = " ".join(f"token{i}" for i in range(750))
    parts = meeting_parts(
        segments=[
            {"speaker": "SPEAKER_00", "start": 0, "end": 30, "text": wording},
            {"speaker": "SPEAKER_01", "start": 31, "end": 32, "text": "Next turn."},
        ],
        timeline_notes=[_note("inside", "point", "Internal reminder", anchor_ms=15000)],
    )
    content = " ".join(
        exported_text(
            render(build_document(parts, ["transcript"]), export_format), export_format
        ).split()
    )
    # PDF page footers may interrupt the paragraph, but every token must survive in order.
    positions = [content.index(f"token{i}") for i in range(750)]
    assert positions == sorted(positions)
    assert ("[00:00] Peter" if export_format == "pdf" else "[00:00] Peter:") in content
    assert (
        content.index("token749")
        < content.index("Internal reminder")
        < content.index("Next turn.")
    )
    assert "[00:15]" in content
