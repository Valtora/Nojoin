"""Pending display state preserves canonical identities through publication."""

from dataclasses import replace

from sqlalchemy import Engine
from sqlmodel import Session

from backend.api.v1.endpoints.transcripts.helpers import TranscriptUtteranceRead
from backend.models.recording_public import serialize_transcript
from backend.models.transcript import Transcript
from backend.tests.test_live_settling_publication import (
    _request,
    _seed,
    _words,
)
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)
from backend.utils.canonical_pipeline.core import (
    refresh_transcript_projection_from_canonical,
    serialize_canonical_utterances,
)
from backend.utils.canonical_pipeline.live_labels import (
    LiveLabelDecision,
    apply_live_speaker_labels,
)
from backend.utils.canonical_pipeline.live_settling import (
    close_live_settling,
    expire_live_settling,
    publish_live_settling,
)
from backend.utils.canonical_pipeline.projection import serialize_canonical_delta
from backend.utils.transcript_settling import load_capture_publication


def _enable_speakers(session: Session, recording_id: int) -> Transcript:
    transcript, _ = load_capture_publication(session, recording_id)
    assert transcript is not None
    transcript.settling_state = {
        **(transcript.settling_state or {}),
        "speaker_enabled": True,
    }
    session.add(transcript)
    return transcript


def _split_decisions(start: int, _end: int) -> LiveLabelDecision:
    if start < 500:
        return LiveLabelDecision("LIVE_PROVISIONAL_1", False, 1.0)
    return LiveLabelDecision("UNKNOWN", False, None, start >= 1000)


def test_pending_and_covered_unknown_have_distinct_public_lines_and_delta(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine)
    with Session(settling_engine) as session:
        _enable_speakers(session, recording_id)
        publish_live_settling(
            session,
            request=_request(session, recording_id, 0),
            words=_words(("Hello", " covered", " tail")),
            decide_speaker=_split_decisions,
        )
        session.commit()
        cursor, lines, _ = serialize_canonical_delta(session, recording_id)
        assert [line["speaker"] for line in lines] == [
            "LIVE_PROVISIONAL_1",
            "UNKNOWN",
            "UNKNOWN",
        ]
        assert [line["diarisation_pending"] for line in lines] == [False, False, True]
        ids = [word["id"] for line in lines for word in line["words"]]
        assert all(
            TranscriptUtteranceRead(**line).diarisation_pending
            == line["diarisation_pending"]
            for line in lines
        )
        refresh_transcript_projection_from_canonical(session, recording_id)
        transcript, _ = load_capture_publication(session, recording_id)
        assert transcript is not None
        compatibility = serialize_transcript(
            transcript, recording_public_id="recording"
        )
        assert [line["diarisation_pending"] for line in compatibility.segments] == [
            False,
            False,
            True,
        ]

        def resolved(start: int, _end: int) -> LiveLabelDecision:
            speaker = "UNKNOWN" if 500 <= start < 1000 else "LIVE_PROVISIONAL_1"
            return LiveLabelDecision(speaker, False, None)

        apply_live_speaker_labels(
            session, recording_id=recording_id, settled_ms=0, decide=resolved
        )
        session.commit()
        _, changed, _ = serialize_canonical_delta(
            session, recording_id, after_revision=cursor
        )
        assert len(changed) == 1 and changed[0]["diarisation_pending"] is False
        current = serialize_canonical_utterances(session, recording_id)
        assert [line["speaker"] for line in current] == [
            "LIVE_PROVISIONAL_1",
            "UNKNOWN",
            "LIVE_PROVISIONAL_1",
        ]
        assert [word["id"] for line in current for word in line["words"]] == ids


def test_pending_survives_expiry_then_closes_unknown(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine)
    with Session(settling_engine) as session:
        _enable_speakers(session, recording_id)
        publish_live_settling(
            session, request=_request(session, recording_id, 0), words=_words()
        )
        session.commit()
        original = serialize_canonical_utterances(session, recording_id)[0]
        assert original["diarisation_pending"] is True
        expire_live_settling(
            session,
            request=replace(
                _request(session, recording_id, 1, end_ms=12000), window_start_ms=2000
            ),
        )
        session.commit()
        expired = serialize_canonical_utterances(session, recording_id)[0]
        assert expired["diarisation_pending"] is True and expired["text_locked"] is True
        assert expired["locked"] is False
        close_live_settling(session, recording_id=recording_id)
        session.commit()
        closed = serialize_canonical_utterances(session, recording_id)[0]
        assert closed["speaker"] == "UNKNOWN" and closed["diarisation_pending"] is False
        assert closed["locked"] is True
        assert [word["id"] for word in closed["words"]] == [
            word["id"] for word in original["words"]
        ]
