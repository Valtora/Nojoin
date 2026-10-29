"""Unplaced final words beside corrected wording remain in the transcript."""

from typing import Any

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from backend.models.pipeline import ProcessingRunKind, TranscriptUtteranceState
from backend.tests.canonical_engine import _recording, _transcript
from backend.tests.canonical_engine import publication_engine as publication_engine
from backend.utils.canonical_pipeline import (
    append_utterances_from_segments,
    finalize_utterances_from_segments,
    refresh_transcript_projection_from_canonical,
    serialize_canonical_utterances,
    update_utterance_text,
)


def _word(
    text: str, start: float | None = None, end: float | None = None
) -> dict[str, Any]:
    return {"word": f" {text}", "start": start, "end": end, "source_id": text}


def _correct_lines(session: Session, recording_id: int) -> list[str]:
    append_utterances_from_segments(
        session,
        recording_id=recording_id,
        segments=[
            {"start": i * 2, "end": i * 2 + 2, "text": f"live {i}"} for i in range(5)
        ],
        run_kind=ProcessingRunKind.LIVE,
        source="live",
        state_override=TranscriptUtteranceState.STABLE,
    )
    session.commit()
    live = serialize_canonical_utterances(session, recording_id)
    ids = [live[index]["id"] for index in (1, 3)]
    for public_id, text in zip(ids, ("Corrected A", "Corrected B")):
        update_utterance_text(
            session,
            recording_id=recording_id,
            utterance_public_id=public_id,
            text=text,
        )
    session.commit()
    return ids


@pytest.mark.parametrize("bounds", [(0, 10), (2.1, 7.9)])
def test_untimed_runs_beside_corrections_survive_finalisation(
    publication_engine: Engine,
    bounds: tuple[float, float],
) -> None:
    with Session(publication_engine) as session:
        recording_id = _recording(session)
        transcript = _transcript(session, recording_id)
        transcript.settling_state = {"settled": True}
        session.add(transcript)
        protected_ids = _correct_lines(session, recording_id)
        words = [
            _word("leading-one"),
            _word("leading-two"),
            _word("machine-a", 2.2, 2.4),
            _word("inside-a"),
            _word("machine-a-end", 3.2, 3.4),
            _word("after-a-one"),
            _word("after-a-two"),
            _word("ordinary", 4.5, 5.5),
            _word("before-b-one"),
            _word("before-b-two"),
            _word("machine-b", 6.2, 7.5),
            _word("trailing-one"),
            _word("trailing-two"),
        ]
        finalize_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[
                {
                    "start": bounds[0],
                    "end": bounds[1],
                    "text": "".join(word["word"] for word in words).strip(),
                    "words": words,
                }
            ],
        )
        refresh_transcript_projection_from_canonical(session, recording_id)
        session.commit()
        lines = serialize_canonical_utterances(
            session, recording_id, include_confidence_payload=True
        )
        expected = [
            "leading-one leading-two",
            "Corrected A",
            "after-a-one after-a-two ordinary before-b-one before-b-two",
            "Corrected B",
            "trailing-one trailing-two",
        ]
        assert [line["text"] for line in lines] == expected
        assert _transcript(session, recording_id).text == " ".join(expected)
        assert [(line["start"], line["end"]) for line in lines] == [
            (min(bounds[0], 2), 2),
            (2, 4),
            (4.5, 5.5),
            (6, 8),
            (8, max(bounds[1], 8)),
        ]
        assert [lines[index]["id"] for index in (1, 3)] == protected_ids
        assert [lines[index]["final_review"]["text"] for index in (1, 3)] == [
            "machine-a inside-a machine-a-end",
            "machine-b",
        ]
        for index in (0, 2, 4):
            assert "final_review" not in lines[index]
            unplaced = [
                word
                for word in lines[index]["words"]
                if word["word"].strip() != "ordinary"
            ]
            assert unplaced
            assert all(
                word["start_ms"] is None and word["end_ms"] is None for word in unplaced
            )


def test_exact_bound_partial_alignment_keeps_unplaced_edges(
    publication_engine: Engine,
) -> None:
    with Session(publication_engine) as session:
        recording_id = _recording(session)
        transcript = _transcript(session, recording_id)
        transcript.settling_state = {"settled": True}
        session.add(transcript)
        protected_ids = _correct_lines(session, recording_id)
        words = [_word("leading"), _word("machine", 2.2, 3.4), _word("trailing")]
        finalize_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[
                {
                    "start": 2,
                    "end": 4,
                    "text": "leading machine trailing",
                    "words": words,
                }
            ],
        )
        refresh_transcript_projection_from_canonical(session, recording_id)
        session.commit()
        lines = serialize_canonical_utterances(
            session, recording_id, include_confidence_payload=True
        )
        assert (
            _transcript(session, recording_id).text
            == "leading Corrected A trailing Corrected B"
        )
        assert [line["text"] for line in lines] == [
            "leading",
            "Corrected A",
            "trailing",
            "Corrected B",
        ]
        assert lines[1]["id"] == protected_ids[0]
        assert lines[1]["final_review"]["text"] == "machine"
        assert [(lines[i]["start"], lines[i]["end"]) for i in (0, 2)] == [
            (2, 2),
            (4, 4),
        ]
        assert all(lines[i]["words"][0]["start_ms"] is None for i in (0, 2))
