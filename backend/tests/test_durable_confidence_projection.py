"""Canonical evidence survives public saves and final projection builders."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from backend.models.pipeline import ProcessingRunKind, TranscriptUtteranceState
from backend.models.recording_public import serialize_transcript
from backend.models.transcript import Transcript
from backend.tests.canonical_engine import _recording, _transcript
from backend.tests.canonical_engine import (
    publication_engine as publication_engine,
)
from backend.utils.canonical_pipeline import (
    append_utterances_from_segments,
    apply_compatibility_segment_replace,
    finalize_utterances_from_segments,
    serialize_canonical_utterances,
)
from backend.utils.canonical_pipeline.live_labels import (
    LiveLabelDecision,
    apply_live_speaker_labels,
)

EVIDENCE = {
    "asr_segments": [{"words": [{"word": "Hello", "start_ms": 0, "end_ms": 1000}]}]
}


def _seed(session: Session, evidence: dict[str, Any] | None) -> int:
    recording_id = _recording(session)
    append_utterances_from_segments(
        session,
        recording_id=recording_id,
        segments=[
            {
                "start": 0.0,
                "end": 1.0,
                "text": "Hello",
                "speaker": "UNKNOWN",
                "confidence_payload": evidence,
            }
        ],
        run_kind=ProcessingRunKind.LIVE,
        source="live",
        state_override=TranscriptUtteranceState.PROVISIONAL,
    )
    session.commit()
    return recording_id


@pytest.mark.parametrize("evidence", [EVIDENCE, None])
@pytest.mark.parametrize("incoming", [None, {"stale": True}])
def test_public_compatibility_save_keeps_canonical_evidence_authoritative(
    publication_engine: Engine,
    evidence: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> None:
    with Session(publication_engine) as session:
        recording_id = _seed(session, evidence)
        segments = serialize_canonical_utterances(session, recording_id)
        assert "confidence_payload" not in segments[0]
        if incoming is not None:
            segments[0]["confidence_payload"] = incoming
        rows = apply_compatibility_segment_replace(
            session, recording_id=recording_id, segments=segments
        )
        session.commit()
        assert rows[0].confidence_payload == evidence
        transcript = _transcript(session, recording_id)
        assert transcript.segments[0].get("confidence_payload") == evidence
        assert (
            "confidence_payload"
            not in serialize_transcript(
                transcript, recording_public_id="recording"
            ).segments[0]
        )


def test_finalise_from_public_segments_keeps_matched_word_evidence(
    publication_engine: Engine,
) -> None:
    with Session(publication_engine) as session:
        recording_id = _seed(session, EVIDENCE)
        segments = serialize_canonical_utterances(session, recording_id)
        rows = finalize_utterances_from_segments(
            session, recording_id=recording_id, segments=segments
        )
        session.commit()
        assert rows[0].confidence_payload == EVIDENCE
        assert (
            _transcript(session, recording_id).segments[0]["confidence_payload"]
            == EVIDENCE
        )


def test_live_labels_copy_current_protection_into_durable_projection(
    publication_engine: Engine,
) -> None:
    with Session(publication_engine) as session:
        recording_id = _seed(session, EVIDENCE)
        result = apply_live_speaker_labels(
            session,
            recording_id=recording_id,
            settled_ms=0,
            decide=lambda start, end: LiveLabelDecision(
                speaker="LIVE_00", locked=True, share=1.0
            ),
        )
        session.commit()
        assert result.changed == 1
        durable = _transcript(session, recording_id).segments[0]["confidence_payload"]
        assert durable == {
            **EVIDENCE,
            "live_speaker_label": {"locked": True, "share": 1.0},
        }


def test_public_read_keeps_durable_evidence_private_without_mutating_projection() -> (
    None
):
    evidence = {
        "asr_segments": [{"words": [{"word": "Hello", "start_ms": 0, "end_ms": 500}]}]
    }
    segment = {
        "id": "line",
        "text": "Hello",
        "start": 0.0,
        "end": 0.5,
        "confidence_payload": evidence,
    }
    transcript = Transcript(id=1, recording_id=1, text="Hello", segments=[segment])
    public = serialize_transcript(transcript, recording_public_id="recording")
    assert "confidence_payload" not in public.segments[0]
    assert public.segments[0]["text"] == "Hello"
    assert transcript.segments[0]["confidence_payload"] == evidence
