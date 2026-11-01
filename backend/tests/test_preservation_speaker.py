"""A kept line takes the final speaker of the words over it, not its old label."""

from __future__ import annotations

from typing import Any

import pytest

from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.utils.canonical_pipeline.preservation import preserve_human_segments

LIVE = "LIVE_PROVISIONAL_1"


def _edited(**fields: Any) -> TranscriptUtterance:
    """A live line at 4-6 s whose wording a person replaced."""
    values: dict[str, Any] = {
        "id": 1,
        "public_id": "edited",
        "recording_id": 1,
        "sort_key": "000000001",
        "start_ms": 4000,
        "end_ms": 6000,
        "text": "Edited words",
        "speaker_label": LIVE,
        "source_kind": "live",
        "state": TranscriptUtteranceState.STABLE,
        "revision": 2,
        "manual_text_locked": True,
    }
    return TranscriptUtterance(**{**values, **fields})


def _segment(start: float, end: float, speaker: str) -> dict[str, Any]:
    """Half-second words from ``start`` to ``end``."""
    words = [
        {"word": f" {speaker[-2:]}w{at}", "start": at / 2, "end": at / 2 + 0.5}
        for at in range(round(start * 2), round(end * 2))
    ]
    return {
        "start": start,
        "end": end,
        "speaker": speaker,
        "text": "".join(word["word"] for word in words).strip(),
        "words": words,
    }


def _kept(result: list[dict[str, Any]]) -> dict[str, Any]:
    return next(segment for segment in result if segment.get("id") == "edited")


def test_the_speaker_holding_most_of_the_words_over_the_line_takes_it() -> None:
    # 1.5 s of the words over 4-6 s are SPEAKER_00's, 0.5 s SPEAKER_01's.
    final = [_segment(0.0, 5.5, "SPEAKER_00"), _segment(5.5, 10.0, "SPEAKER_01")]
    kept = _kept(preserve_human_segments([_edited()], final))
    assert (kept["speaker"], kept["recording_speaker_id"]) == ("SPEAKER_00", None)
    assert kept["text"] == "Edited words"
    assert kept["text_manually_edited"] is True
    assert kept["confidence_payload"]["final_review"]["text"] == "00w8 00w9 00w10 01w11"


def test_words_with_no_speaker_name_none() -> None:
    final = [_segment(0.0, 5.5, "UNKNOWN"), _segment(5.5, 10.0, "SPEAKER_01")]
    assert _kept(preserve_human_segments([_edited()], final))["speaker"] == "SPEAKER_01"


@pytest.mark.parametrize("final", ["no-speakers", "no-words"])
def test_a_line_with_no_speaker_over_it_is_unknown(final: str) -> None:
    segments = (
        [_segment(0.0, 10.0, "UNKNOWN")]
        if final == "no-speakers"
        else [_segment(7.0, 10.0, "SPEAKER_00")]
    )
    assert _kept(preserve_human_segments([_edited()], segments))["speaker"] == "UNKNOWN"


def test_a_speaker_chosen_by_hand_stays_on_the_kept_line() -> None:
    line = _edited(
        manual_speaker_locked=True, recording_speaker_id=7, speaker_label="SPEAKER_04"
    )
    kept = _kept(preserve_human_segments([line], [_segment(0.0, 10.0, "SPEAKER_00")]))
    assert (kept["speaker"], kept["recording_speaker_id"]) == ("SPEAKER_04", 7)


def test_an_untimed_segment_reviewed_whole_gives_its_speaker() -> None:
    segment = {"start": 0.0, "end": 10.0, "speaker": "SPEAKER_02", "text": "All of it."}
    kept = _kept(preserve_human_segments([_edited()], [segment]))
    assert kept["speaker"] == "SPEAKER_02"
    assert kept["confidence_payload"]["final_review"]["text"] == "All of it."
