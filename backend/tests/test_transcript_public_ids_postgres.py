"""Two recordings with a wordless Whisper segment finalize without an id collision.

``transcript_utterances.public_id`` is unique across every recording. The
per-run merge keeps a wordless segment's text but not its openai-whisper integer
index, and finalize mints the public id, so the second recording with a
wordless segment at the same index finalizes cleanly.

The production schema needs PostgreSQL (JSONB columns), so this skips unless
NOJOIN_TEST_POSTGRES_URL names a server. CI sets it for the backend suite.
"""

from __future__ import annotations

import importlib

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel

from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.tests.test_transcript_utils import (
    WORDLESS_SEGMENT_TURNS,
    FakeDiarization,
    whisper_transcription_with_a_wordless_segment,
)
from backend.utils.canonical_pipeline.core import finalize_utterances_from_segments
from backend.utils.transcript_utils import (
    combine_transcription_diarization,
    consolidate_diarized_transcript,
)

SCHEMA = "transcript_public_id_test"


@pytest.fixture
def pg_engine(postgres_test_url: str):
    """A psycopg2 engine (the worker's driver) over a throwaway schema."""
    importlib.import_module("backend.models.registry")
    engine = create_engine(
        postgres_test_url,
        connect_args={"options": f"-csearch_path={SCHEMA},public"},
    )
    try:
        with engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            connection.execute(text(f"CREATE SCHEMA {SCHEMA}"))
        SQLModel.metadata.create_all(engine)
        yield engine
        with engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
    finally:
        engine.dispose()


def _finalize_new_recording(session: Session, user: User, name: str) -> list[str]:
    recording = Recording(name=name, audio_path=f"/data/{name}.wav", user_id=user.id)
    session.add(recording)
    session.commit()
    segments = consolidate_diarized_transcript(
        combine_transcription_diarization(
            whisper_transcription_with_a_wordless_segment(),
            FakeDiarization(WORDLESS_SEGMENT_TURNS),
        )
    )
    session.add(Transcript(recording_id=recording.id, text="", segments=segments))
    session.commit()
    utterances = finalize_utterances_from_segments(
        session,
        recording_id=recording.id,
        segments=segments,
        trigger_source="worker",
    )
    session.commit()
    assert [utterance.text for utterance in utterances] == [
        "Let's start.",
        "Sorry, I was muted.",
        "No problem.",
    ]
    return [utterance.public_id for utterance in utterances]


def test_whisper_segment_index_does_not_collide_across_recordings(pg_engine):
    with Session(pg_engine) as session:
        user = User(username="alice", hashed_password="x")
        session.add(user)
        session.commit()

        first = _finalize_new_recording(session, user, "first")
        second = _finalize_new_recording(session, user, "second")

    assert not set(first) & set(second)
