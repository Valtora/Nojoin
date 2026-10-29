"""Writers that change what a transcript reader shows advance its change counter.

A finished recording's page polls the counter (through the recording read's
ETag) and reads the transcript again when it moves.
"""

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from backend.models.pipeline import TranscriptUtterance
from backend.models.transcript import Transcript
from backend.tests.test_live_line_actions import _lines
from backend.tests.test_live_settling_publication import _request, _seed, _words
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)
from backend.utils.canonical_pipeline import (
    SplitCommand,
    clear_utterance_manual_locks,
    split_utterance,
    update_utterance_speaker,
    update_utterance_text,
)
from backend.utils.canonical_pipeline.live_settling import publish_live_settling
from backend.utils.canonical_pipeline.live_settling_projection import row_words


def _sequence(session: Session, recording_id: int) -> int:
    return session.exec(
        select(Transcript.change_sequence).where(
            Transcript.recording_id == recording_id
        )
    ).one()


def _act(session: Session, recording_id: int, public_id: str, action: str) -> None:
    row = session.exec(
        select(TranscriptUtterance).where(TranscriptUtterance.public_id == public_id)
    ).one()
    if action == "text":
        update_utterance_text(
            session,
            recording_id=recording_id,
            utterance_public_id=public_id,
            text="Hi.",
        )
    elif action == "speaker":
        update_utterance_speaker(
            session,
            recording_id=recording_id,
            utterance_public_id=public_id,
            new_speaker_name="Alex",
        )
    elif action == "split":
        split_utterance(
            session,
            SplitCommand(
                recording_id, public_id, row_words(row)[1]["id"], row.revision
            ),
        )
    else:
        clear_utterance_manual_locks(
            session, recording_id=recording_id, utterance_public_id=public_id
        )


@pytest.mark.parametrize("action", ["text", "speaker", "split", "unlock"])
def test_a_line_action_advances_the_counter(
    settling_engine: Engine, action: str
) -> None:
    recording_id, ids = _lines(settling_engine)
    with Session(settling_engine) as session:
        before = _sequence(session, recording_id)
        _act(session, recording_id, ids[0], action)
        session.commit()

        assert _sequence(session, recording_id) > before


def test_a_settling_pass_advances_the_counter_only_when_it_publishes(
    settling_engine: Engine,
) -> None:
    recording_id = _seed(settling_engine)
    with Session(settling_engine) as session:
        before = _sequence(session, recording_id)
        request = _request(session, recording_id, 0)
        assert publish_live_settling(session, request=request, words=_words()).accepted
        session.commit()
        published = _sequence(session, recording_id)
        # The same step again is a duplicate and changes nothing a reader sees.
        assert not publish_live_settling(
            session, request=request, words=_words()
        ).accepted
        session.commit()

        assert published > before
        assert _sequence(session, recording_id) == published
