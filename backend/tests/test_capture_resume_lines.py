"""The same capture cut divides live words, legacy audio and final evidence."""

from typing import Any

import pytest

from backend.tests.test_transcript_utils import FakeDiarization
from backend.utils.canonical_pipeline.live_labels import LiveLabelDecision
from backend.utils.canonical_pipeline.live_settling import _line_boundaries
from backend.utils.canonical_pipeline.live_settling_lines import partition_words
from backend.utils.capture_resume import split_resume_regions
from backend.worker.tasks.final_segments import combine_and_consolidate_segments


def test_rolling_words_cannot_rejoin_across_a_resume_without_silence() -> None:
    words = [
        {
            "id": "before",
            "word": "Before",
            "start_ms": 100,
            "end_ms": 1000,
            "locked": True,
        },
        {
            "id": "after",
            "word": " after",
            "start_ms": 1000,
            "end_ms": 1500,
            "locked": True,
        },
    ]
    boundaries = _line_boundaries([], {"resume_boundaries_ms": [1000]}, None, words)
    lines = partition_words(
        words, [LiveLabelDecision("A", True, 1.0)] * 2, boundaries=boundaries
    )
    assert [[word["id"] for word in line.words] for line in lines] == [
        ["before"],
        ["after"],
    ]
    assert lines[0].boundary_locked and lines[0].closed_at_ms == 1000


def test_legacy_cpu_decodes_separate_audio_regions_without_inventing_word_times() -> (
    None
):
    assert split_resume_regions(
        [{"start": 0.2, "end": 3.0}], 10.0, [10_000, 11_000, 12_000, 13_000]
    ) == [
        {"start": 0.2, "end": 1.0},
        {"start": 1.0, "end": 2.0},
        {"start": 2.0, "end": 3.0},
    ]


@pytest.mark.parametrize("diarized", [False, True])
def test_final_timed_words_split_without_short_segment_fold_crossing_resume(
    diarized: bool,
) -> None:
    words = [
        {"word": " Before", "start": 0.0, "end": 1.0, "confidence": 0.9},
        {"word": " yes", "start": 1.0, "end": 1.08, "confidence": 0.8},
        {"word": " later", "start": 2.0, "end": 3.0, "confidence": 0.7},
    ]
    result = combine_and_consolidate_segments(
        {
            "segments": [
                {"start": 0.0, "end": 3.0, "text": "Before yes later", "words": words}
            ]
        },
        FakeDiarization([(0.0, 3.0, "A")]) if diarized else None,
        enable_diarization=diarized,
        recording_id=1,
        config={"resume_boundaries_ms": [1000, 2000]},
    )
    assert [line["text"] for line in result] == ["Before", "yes", "later"]
    assert [word for line in result for word in line["words"]] == words
    assert [(line["start"], line["end"]) for line in result] == [
        (0.0, 1.0),
        (1.0, 1.08),
        (2.0, 3.0),
    ]


@pytest.mark.parametrize("alignment_error", [None, "No usable word timings"])
def test_final_untimed_crossing_remains_intact_without_fabricating_evidence(
    alignment_error: str | None,
) -> None:
    segment: dict[str, Any] = {"start": 0.0, "end": 2.0, "text": "before after"}
    if alignment_error:
        segment["alignment_error"] = alignment_error
    result = combine_and_consolidate_segments(
        {"segments": [segment]},
        None,
        enable_diarization=False,
        recording_id=1,
        config={"resume_boundaries_ms": [1000]},
    )
    assert len(result) == 1 and result[0]["text"] == "before after"
    assert not result[0].get("words")


def test_resume_does_not_split_a_fragment_inside_one_lexical_word() -> None:
    words = [
        {"word": " be", "start": 0.0, "end": 1.0, "source_id": "word"},
        {"word": "fore", "start": 1.0, "end": 1.2, "source_id": "word"},
        {"word": " after", "start": 1.2, "end": 2.0},
    ]
    result = combine_and_consolidate_segments(
        {
            "segments": [
                {"start": 0.0, "end": 2.0, "text": "before after", "words": words}
            ]
        },
        None,
        enable_diarization=False,
        recording_id=1,
        config={"resume_boundaries_ms": [1000]},
    )
    assert [line["text"] for line in result] == ["before", "after"]
    assert [word for line in result for word in line["words"]] == words


@pytest.mark.parametrize("noise", [-1e-12, 0.0, 1e-12])
def test_native_word_at_resume_cut_uses_existing_float_tolerance(noise: float) -> None:
    words = [
        {"word": "before", "start": 0.0, "end": 1.0},
        {"word": " after", "start": 1.0 + noise, "end": 2.0},
    ]
    result = combine_and_consolidate_segments(
        {
            "segments": [
                {"start": 0.0, "end": 2.0, "text": "before after", "words": words}
            ]
        },
        None,
        enable_diarization=False,
        recording_id=1,
        config={"resume_boundaries_ms": [1000]},
    )
    assert [line["text"] for line in result] == ["before", "after"]
    assert [word for line in result for word in line["words"]] == words


def test_never_resumed_capture_keeps_the_exact_raw_asr_line() -> None:
    words = [
        {"word": " Before", "start": 0.2, "end": 0.8},
        {"word": " after", "start": 1.0, "end": 1.8},
    ]
    result = combine_and_consolidate_segments(
        {
            "segments": [
                {
                    "id": "source",
                    "start": 0.0,
                    "end": 2.0,
                    "text": " Before after ",
                    "words": words,
                }
            ]
        },
        None,
        enable_diarization=False,
        recording_id=1,
    )
    assert result == [
        {
            "id": "source",
            "start": 0.0,
            "end": 2.0,
            "speaker": "UNKNOWN",
            "text": "Before after",
            "overlapping_speakers": [],
            "words": words,
        }
    ]
