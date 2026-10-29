"""Full recording reads retain the canonical live presentation contract."""

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from backend.models.recording import Recording, RecordingStatus
from backend.models.recording_public import serialize_transcript
from backend.tests.test_live_settling_pending import _enable_speakers, _split_decisions
from backend.tests.test_live_settling_publication import _request, _seed, _words
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)
from backend.utils.canonical_pipeline.core import build_transcript_segments_for_read
from backend.utils.canonical_pipeline.live_settling import publish_live_settling


@pytest.mark.parametrize(
    "status", [RecordingStatus.UPLOADING, RecordingStatus.PROCESSING]
)
def test_full_recording_preserves_canonical_pending(
    settling_engine: Engine, status: RecordingStatus
) -> None:
    recording_id = _seed(settling_engine)
    with Session(settling_engine) as session:
        transcript = _enable_speakers(session, recording_id)
        publish_live_settling(
            session,
            request=_request(session, recording_id, 0),
            words=_words(("Hello", " covered", " tail")),
            decide_speaker=_split_decisions,
        )
        recording = session.get(Recording, recording_id)
        assert recording is not None
        recording.status = status
        session.add(recording)
        session.commit()
        canonical = build_transcript_segments_for_read(session, recording_id)
        assert [line["diarisation_pending"] for line in canonical] == [
            False,
            False,
            True,
        ]
        assert all("confidence_payload" not in line for line in canonical)
        response = serialize_transcript(
            transcript, recording_public_id="recording", segments_override=canonical
        )
        assert [line.get("diarisation_pending") for line in response.segments] == [
            False,
            False,
            True,
        ]
