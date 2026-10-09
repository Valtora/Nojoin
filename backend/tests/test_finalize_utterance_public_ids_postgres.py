"""Finalize keeps a segment's ``id`` as an utterance public id only when it is one.

``transcript_utterances.public_id`` is unique across every recording, but a
merged segment's ``id`` can be an engine's own segment number: openai-whisper
numbers its segments, and the merge passes the number on when diarisation is
off or word timestamps are off. Finalize used to persist any such id it had
not already reserved for the same recording, so the second recording through
either path failed with a UniqueViolation. Live-ASR reuse carries a live
utterance's public id through the same key on purpose, and that must survive.

The production schema needs PostgreSQL (JSONB columns, the vector extension),
so the database tests skip unless NOJOIN_TEST_POSTGRES_URL names a server. CI
sets it for the backend suite.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel, select

from backend.models.pipeline import TranscriptUtterance
from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.processing.live_transcribe import _build_live_utterance_public_id
from backend.tests.test_transcript_utils import FakeDiarization
from backend.utils.canonical_pipeline.core import finalize_utterances_from_segments
from backend.utils.canonical_pipeline.public_ids import is_utterance_public_id
from backend.utils.live_transcript import build_transcription_result_from_segments
from backend.worker.tasks.pipeline import _combine_and_consolidate_segments

SCHEMA = "finalize_public_id_test"
TEXTS = ["Hello there.", "Over here.", "And again."]
ALTERNATING_TURNS = [
    (0.0, 2.0, "SPEAKER_00"),
    (2.0, 4.0, "SPEAKER_01"),
    (4.5, 6.0, "SPEAKER_00"),
]


def _whisper_without_word_timestamps() -> dict:
    """openai-whisper's result with word timestamps off: numbered, no words."""
    return {
        "text": " Hello there. Over here. And again.",
        "segments": [
            {"id": 0, "start": 0.0, "end": 2.0, "text": " Hello there."},
            {"id": 1, "start": 2.0, "end": 4.0, "text": " Over here."},
            {"id": 2, "start": 4.5, "end": 6.0, "text": " And again."},
        ],
    }


def _live_reuse_transcription(live_public_id: str) -> dict:
    """A transcription rebuilt from one live segment, as live-ASR reuse does."""
    transcription, _ = build_transcription_result_from_segments(
        [
            {
                "id": live_public_id,
                "start": 0.0,
                "end": 2.0,
                "text": "Hello there.",
                "segment_source": "live",
            }
        ]
    )
    assert transcription is not None
    return transcription


@pytest.fixture
def session(postgres_test_url: str) -> Iterator[Session]:
    """A psycopg2 session (the worker's driver) over a throwaway schema."""
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
        with Session(engine) as db_session:
            yield db_session
        with engine.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
    finally:
        engine.dispose()


@pytest.fixture
def user(session: Session) -> User:
    account = User(username="alice", hashed_password="x")
    session.add(account)
    session.commit()
    return account


def _new_recording(session: Session, user: User, name: str) -> Recording:
    recording = Recording(name=name, audio_path=f"/data/{name}.wav", user_id=user.id)
    session.add(recording)
    session.commit()
    return recording


def _finalize(
    session: Session, recording: Recording, segments: list[dict]
) -> list[TranscriptUtterance]:
    assert recording.id is not None
    session.add(Transcript(recording_id=recording.id, text="", segments=segments))
    session.commit()
    utterances = finalize_utterances_from_segments(
        session,
        recording_id=recording.id,
        segments=[dict(segment) for segment in segments],
        trigger_source="worker",
    )
    session.commit()
    return utterances


@pytest.mark.parametrize(
    ("diarization", "enable_diarization", "expected_texts"),
    [
        pytest.param(
            FakeDiarization(ALTERNATING_TURNS), True, TEXTS, id="word-timestamps-off"
        ),
        pytest.param(
            None,
            False,
            ["Hello there. Over here.", "And again."],
            id="diarization-disabled",
        ),
    ],
)
def test_whisper_segment_numbers_never_become_utterance_ids(
    session: Session,
    user: User,
    diarization: FakeDiarization | None,
    enable_diarization: bool,
    expected_texts: list[str],
) -> None:
    public_ids: list[str] = []
    for name in ("first", "second"):
        segments = _combine_and_consolidate_segments(
            _whisper_without_word_timestamps(),
            diarization,
            enable_diarization=enable_diarization,
            recording_id=0,
        )
        utterances = _finalize(session, _new_recording(session, user, name), segments)
        assert [utterance.text for utterance in utterances] == expected_texts
        public_ids.extend(utterance.public_id for utterance in utterances)

    assert all(is_utterance_public_id(public_id) for public_id in public_ids)
    assert len(set(public_ids)) == len(public_ids)


def test_live_utterance_id_carried_through_the_merge_is_kept(
    session: Session, user: User
) -> None:
    recording = _new_recording(session, user, "live")
    assert recording.id is not None
    live_public_id = _build_live_utterance_public_id(
        recording_id=recording.id,
        span_start_ms=0,
        span_end_ms=2000,
        speaker_label="UNKNOWN",
        text="Hello there.",
    )
    segments = _combine_and_consolidate_segments(
        _live_reuse_transcription(live_public_id),
        FakeDiarization([(0.0, 2.0, "SPEAKER_00")]),
        enable_diarization=True,
        recording_id=recording.id,
    )

    utterances = _finalize(session, recording, segments)

    assert [utterance.public_id for utterance in utterances] == [live_public_id]


def test_utterance_id_held_by_another_recording_is_not_reused(
    session: Session, user: User
) -> None:
    carried_public_id = str(uuid4())
    first = _new_recording(session, user, "first")
    second = _new_recording(session, user, "second")
    for recording in (first, second):
        assert recording.id is not None
        segments = _combine_and_consolidate_segments(
            _live_reuse_transcription(carried_public_id),
            None,
            enable_diarization=False,
            recording_id=recording.id,
        )
        _finalize(session, recording, segments)

    rows = session.exec(
        select(TranscriptUtterance.recording_id, TranscriptUtterance.public_id)
    ).all()
    owners = {
        recording_id
        for recording_id, public_id in rows
        if public_id == carried_public_id
    }
    second_ids = [
        public_id for recording_id, public_id in rows if recording_id == second.id
    ]
    assert owners == {first.id}
    assert len(second_ids) == 1
    assert is_utterance_public_id(second_ids[0])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("1", False, id="whisper-segment-number"),
        pytest.param("0adff4bd3c474ae88f88b3d0252796ce", False, id="unhyphenated"),
        pytest.param("0ADFF4BD-3C47-4AE8-8F88-B3D0252796CE", False, id="upper-case"),
        pytest.param("{0adff4bd-3c47-4ae8-8f88-b3d0252796ce}", False, id="braced"),
        pytest.param("0adff4bd-3c47-4ae8-8f88-b3d0252796ce", True, id="uuid4"),
        pytest.param(
            _build_live_utterance_public_id(
                recording_id=7,
                span_start_ms=0,
                span_end_ms=1000,
                speaker_label="UNKNOWN",
                text="hi",
            ),
            True,
            id="live-uuid5",
        ),
    ],
)
def test_is_utterance_public_id_accepts_only_the_minted_form(
    value: str, expected: bool
) -> None:
    assert is_utterance_public_id(value) is expected
