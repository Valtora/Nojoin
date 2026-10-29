"""Final preservation takes only the final segments over protected wording."""

from typing import Any

import pytest

from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.utils.canonical_pipeline import preservation


def _lines(count: int, lock: str | None = None) -> list[TranscriptUtterance]:
    """Live lines two seconds apart; ``lock`` names line 7's manual lock."""
    return [
        TranscriptUtterance(
            public_id=f"old-{index}",
            recording_id=1,
            sort_key=f"{index:09}",
            start_ms=index * 2000,
            end_ms=(index + 1) * 2000,
            text=f"original {index}",
            speaker_label="UNKNOWN",
            source_kind="live",
            state=TranscriptUtteranceState.STABLE,
            revision=1,
            **({lock: index == 7} if lock else {}),
        )
        for index in range(count)
    ]


def _drifted_final(count: int) -> list[dict[str, Any]]:
    """Each final segment overlaps two live lines, chaining the whole stretch."""
    return [
        {"start": index * 2 + 1, "end": index * 2 + 3, "text": f"final {index}"}
        for index in range(count)
    ]


def test_text_edit_takes_only_the_final_segments_it_overlaps() -> None:
    final = _drifted_final(20)
    result = preservation.preserve_human_segments(
        _lines(20, "manual_text_locked"), final
    )
    kept = [segment for segment in result if segment.get("id") == "old-7"]
    assert len(kept) == 1
    assert kept[0]["text"] == "original 7"
    assert kept[0]["confidence_payload"]["final_review"] == {
        "text": "final 6 final 7",
        "reason": "ambiguous_alignment",
    }
    assert [segment for segment in result if segment.get("id") != "old-7"] == [
        segment for index, segment in enumerate(final) if index not in (6, 7)
    ]


@pytest.mark.parametrize("lock", [None, "manual_speaker_locked"])
def test_speaker_only_correction_leaves_final_wording_to_the_finaliser(
    lock: str | None,
) -> None:
    final = _drifted_final(20)
    assert preservation.preserve_human_segments(_lines(20, lock), final) == final
