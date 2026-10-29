"""Final speaker corrections and durable rejected names survive another pass."""

from copy import deepcopy

import pytest
from sqlalchemy import Engine, delete
from sqlmodel import Session, select

from backend.models.recording_public import serialize_transcript
from backend.models.speaker import RecordingSpeaker
from backend.models.speaker_name_event import SpeakerNameEvent
from backend.tests.canonical_engine import _transcript
from backend.tests.canonical_engine import (
    publication_engine as publication_engine,
)
from backend.tests.test_live_settling_finalisation import _closed_line, _final_segment
from backend.utils.canonical_pipeline import (
    finalize_utterances_from_segments,
    refresh_transcript_projection_from_canonical,
    serialize_canonical_delta,
    serialize_canonical_utterances,
    update_utterance_speaker,
)
from backend.utils.speaker_names.durability import (
    persist_name_anchors,
    restore_name_anchors,
    snapshot_name_anchors,
)
from backend.utils.speaker_names.events import add_event
from backend.utils.speaker_names.review import dismiss_pending_name, dismissed_names
from backend.utils.speaker_names.sources import AUTHORITY, SOURCE_USER
from backend.utils.transcript_settling import durable_settling_state


@pytest.mark.parametrize("engine", ["whisper", "parakeet", "canary"])
def test_speaker_corrected_final_line_takes_shifted_reprocess_wording(
    publication_engine: Engine, engine: str
) -> None:
    with Session(publication_engine) as session:
        recording_id, _ = _closed_line(session)
        finalize_utterances_from_segments(
            session, recording_id=recording_id, segments=[_final_segment(engine)]
        )
        refresh_transcript_projection_from_canonical(session, recording_id)
        before = serialize_canonical_utterances(session, recording_id)[0]
        row, speaker = update_utterance_speaker(
            session,
            recording_id=recording_id,
            utterance_public_id=before["id"],
            new_speaker_name="Correct person",
            expected_revision=before["revision"],
        )
        assert row.manual_speaker_locked and not row.manual_text_locked
        speaker_id = speaker.id
        session.commit()
        final = {**_final_segment(engine, "New"), "start": 0, "end": 1}
        finalize_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[final],
            idempotency_key="shifted-reprocess",
        )
        refresh_transcript_projection_from_canonical(session, recording_id)
        session.commit()
        session.expunge_all()
        canonical = serialize_canonical_utterances(session, recording_id)
        _, delta, _ = serialize_canonical_delta(session, recording_id)
        public = serialize_transcript(
            _transcript(session, recording_id), recording_public_id="recording"
        ).segments
        assert len(canonical) == len(delta) == len(public) == 1
        for line in (canonical[0], delta[0], public[0]):
            assert line["text"] == "New world"
            assert [word["word"].strip() for word in line["words"]] == ["New", "world"]
            assert line["recording_speaker_id"] == speaker_id
            assert line["speaker_manually_edited"]
            assert not line.get("final_review")


def test_snapshot_restore_keeps_all_dismissals_and_only_latest_positive_name(
    publication_engine: Engine,
) -> None:
    with Session(publication_engine) as session:
        recording_id, _ = _closed_line(session)
        row = finalize_utterances_from_segments(
            session, recording_id=recording_id, segments=[_final_segment("whisper")]
        )[0]
        speaker = RecordingSpeaker(
            recording_id=recording_id, diarization_label="SPEAKER_0"
        )
        session.add(speaker)
        session.flush()
        speaker_id = speaker.id
        row.recording_speaker_id = speaker_id
        row.speaker_label = speaker.diarization_label
        session.add(row)
        session.flush()
        for name in ("Alice", "Bob"):
            speaker.name = name
            speaker.name_source = "llm"
            session.add(speaker)
            session.flush()
            dismiss_pending_name(session, speaker, actor_user_id=None)
        for name in ("Earlier", "Latest"):
            add_event(
                session,
                speaker,
                SpeakerNameEvent(
                    recording_id=recording_id,
                    kind="name",
                    name=name,
                    source=SOURCE_USER,
                    authority=AUTHORITY[SOURCE_USER],
                    anchor_start_ms=row.start_ms,
                    anchor_end_ms=row.end_ms,
                ),
            )
        assert dismissed_names(session, speaker_id) == {"alice", "bob"}
        persist_name_anchors(session, recording_id)
        transcript = _transcript(session, recording_id)
        state = durable_settling_state(transcript.settling_state)
        assert state is not None
        anchors = deepcopy(state["name_anchors"])
        assert [(a["kind"], a["name"]) for a in anchors] == [
            ("dismiss", "Alice"),
            ("dismiss", "Bob"),
            ("name", "Latest"),
        ]
        transcript.settling_state = state
        session.add(transcript)
        session.execute(
            delete(SpeakerNameEvent).where(
                SpeakerNameEvent.recording_id == recording_id
            )
        )
        session.commit()
        assert not session.exec(select(SpeakerNameEvent)).all()
        restore_name_anchors(session, recording_id)
        restore_name_anchors(session, recording_id)
        session.commit()
        session.expunge_all()
        assert dismissed_names(session, speaker_id) == {"alice", "bob"}
        assert snapshot_name_anchors(session, recording_id) == anchors
        assert len(session.exec(select(SpeakerNameEvent)).all()) == 3
