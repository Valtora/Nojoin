"""A segment's ``id`` becomes an utterance public id only when it is one.

``transcript_utterances.public_id`` is unique across every recording, but a
merged segment's ``id`` can be an engine's own segment number: openai-whisper
numbers its segments, and the merge passes the number on when diarisation is
off or word timestamps are off. Finalize used to persist any such id it had
not already reserved for the same recording, and the backfill/replace path
persisted every id unchecked, so the second recording through either failed
with a UniqueViolation. Live-ASR reuse carries a live utterance's public id
through the same key on purpose, and that must survive.

The production schema needs PostgreSQL (JSONB columns, the vector extension),
so the database tests skip unless NOJOIN_TEST_POSTGRES_URL names a server. CI
sets it for the backend suite.
"""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, col, select

from backend.api.deps import get_current_user, get_db
from backend.api.v1.api import api_router
from backend.models.pipeline import (
    ProcessingRunKind,
    TranscriptUtterance,
    TranscriptUtteranceState,
)
from backend.models.recording import Recording
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.processing.live_transcribe import _build_live_utterance_public_id
from backend.tests.test_transcript_utils import FakeDiarization
from backend.utils.canonical_pipeline.core import (
    finalize_utterances_from_segments,
    replace_utterances_from_segments,
)
from backend.utils.canonical_pipeline.public_ids import is_utterance_public_id
from backend.utils.canonical_pipeline.startup import ensure_canonical_backfill
from backend.utils.live_transcript import build_transcription_result_from_segments
from backend.worker.tasks.final_segments import combine_and_consolidate_segments

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
        segments = combine_and_consolidate_segments(
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
    segments = combine_and_consolidate_segments(
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
        segments = combine_and_consolidate_segments(
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


def _backfill(
    session: Session, recording: Recording, segments: list[dict]
) -> list[TranscriptUtterance]:
    """Canonicalise a recording that has only ``Transcript.segments``.

    That is a recording finalised before canonical writes, or with them
    turned off, or restored from a backup, which carries no utterance rows.
    """
    assert recording.id is not None
    session.add(Transcript(recording_id=recording.id, text="", segments=segments))
    session.commit()
    utterances = ensure_canonical_backfill(session, recording.id)
    session.commit()
    return utterances


def _segment(public_id: str, start: float, end: float, text: str) -> dict:
    return {
        "id": public_id,
        "start": start,
        "end": end,
        "speaker": "SPEAKER_00",
        "text": text,
    }


def test_backfill_of_whisper_numbered_projections_never_collides(
    session: Session, user: User
) -> None:
    """A projection's own ids are kept, as upstream keeps them, unless taken."""
    public_ids: list[list[str]] = []
    for name in ("first", "second"):
        segments = combine_and_consolidate_segments(
            _whisper_without_word_timestamps(),
            None,
            enable_diarization=False,
            recording_id=0,
        )
        utterances = _backfill(session, _new_recording(session, user, name), segments)
        assert [utterance.text for utterance in utterances] == [
            "Hello there. Over here.",
            "And again.",
        ]
        public_ids.append([utterance.public_id for utterance in utterances])

    first, second = public_ids
    assert first == ["1", "2"]
    assert all(is_utterance_public_id(public_id) for public_id in second)


def test_backfill_does_not_reuse_an_id_another_recording_holds(
    session: Session, user: User
) -> None:
    carried_public_id = str(uuid4())
    first = _backfill(
        session,
        _new_recording(session, user, "first"),
        [_segment(carried_public_id, 0.0, 2.0, "Hello there.")],
    )
    second = _backfill(
        session,
        _new_recording(session, user, "second"),
        [_segment(carried_public_id, 0.0, 2.0, "Hello there.")],
    )

    assert [utterance.public_id for utterance in first] == [carried_public_id]
    assert len(second) == 1
    assert second[0].public_id != carried_public_id
    assert is_utterance_public_id(second[0].public_id)


def test_forced_backfill_mints_over_held_ids_and_records_lineage_on_the_row(
    session: Session, user: User
) -> None:
    """A forced rebuild resends the projection's ids, which its own rows hold.

    The rebuild supersedes every active utterance, whose rows keep their
    public ids, so each new row gets a fresh id and names the one it replaces.
    """
    recording = _new_recording(session, user, "rebuilt")
    original_id = str(uuid4())
    _backfill(session, recording, [_segment(original_id, 0.0, 2.0, "Hello there.")])
    assert recording.id is not None

    projection = session.exec(
        select(Transcript).where(Transcript.recording_id == recording.id)
    ).one()
    rebuilt = replace_utterances_from_segments(
        session,
        recording_id=recording.id,
        segments=[dict(segment) for segment in projection.segments],
        run_kind=ProcessingRunKind.BACKFILL,
        source="backfill",
        force=True,
        idempotency_key="rebuild",
    )
    session.commit()

    assert len(rebuilt) == 1
    assert rebuilt[0].public_id != original_id
    assert is_utterance_public_id(rebuilt[0].public_id)
    assert rebuilt[0].confidence_payload == {"source_public_ids": [original_id]}
    original = session.exec(
        select(TranscriptUtterance).where(TranscriptUtterance.public_id == original_id)
    ).one()
    assert original.state == TranscriptUtteranceState.SUPERSEDED


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client(
    postgres_test_url: str,
    session: Session,
    user: User,
    stub_meeting_edge_dispatch,
) -> AsyncIterator[AsyncClient]:
    """The transcripts API over asyncpg (the API's driver), in the same schema."""
    engine = create_async_engine(
        postgres_test_url.replace("postgresql://", "postgresql+asyncpg://", 1),
        connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}},
    )
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with session_maker() as db_session:
            yield db_session

    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=user.id, username=user.username, settings={}, force_password_change=False
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as http_client:
            yield http_client
    finally:
        await engine.dispose()


def _utterance_rows(session: Session) -> list[tuple]:
    session.expire_all()
    rows = session.exec(
        select(
            TranscriptUtterance.public_id,
            TranscriptUtterance.text,
            TranscriptUtterance.state,
            TranscriptUtterance.manual_text_locked,
            TranscriptUtterance.manual_speaker_locked,
            TranscriptUtterance.speaker_label,
        ).order_by(col(TranscriptUtterance.id))
    ).all()
    session.commit()
    return [tuple(row) for row in rows]


def _segments_url(
    session: Session, user: User, segments: list[dict] | None = None
) -> str:
    recording = _new_recording(session, user, "edited")
    session.add(
        Transcript(
            recording_id=recording.id,
            text="hello",
            segments=segments or [_segment("", 0.0, 1.0, "hello")],
        )
    )
    session.commit()
    return f"/api/v1/transcripts/{recording.public_id}"


@pytest.mark.anyio
async def test_bulk_put_of_a_recordings_own_legacy_ids_keeps_its_text_lock(
    client: AsyncClient, session: Session, user: User
) -> None:
    """Legacy ids a client already holds edit in place, as upstream does.

    The recording has only its projection, whose ids predate UUIDs. The PUT
    backfills first; keeping those ids lets the edit match the rows and take
    the in-place path, which honours the text lock.
    """
    locked = _segment("legacy-segment-1", 0.0, 1.0, "locked legacy")
    locked["text_manually_edited"] = True
    url = _segments_url(
        session, user, [locked, _segment("legacy-segment-2", 1.0, 2.0, "two")]
    )

    response = await client.put(
        f"{url}/segments",
        json={
            "segments": [
                _segment("legacy-segment-1", 0.0, 1.0, "client overwrite"),
                _segment("legacy-segment-2", 1.0, 2.0, "two"),
            ]
        },
    )

    assert response.status_code == 200
    assert [
        (segment["id"], segment["text"]) for segment in response.json()["segments"]
    ] == [("legacy-segment-1", "locked legacy"), ("legacy-segment-2", "two")]
    rows = _utterance_rows(session)
    assert [(row[0], row[1], row[3]) for row in rows] == [
        ("legacy-segment-1", "locked legacy", True),
        ("legacy-segment-2", "two", False),
    ]
    assert all(row[2] != TranscriptUtteranceState.SUPERSEDED for row in rows)


@pytest.mark.anyio
async def test_bulk_put_reusing_held_ids_is_a_conflict_and_changes_nothing(
    client: AsyncClient, session: Session, user: User
) -> None:
    """Omitting a segment forces a full replace, which cannot reuse held ids.

    The replace supersedes every active utterance, and those rows keep their
    public ids, so the kept segments' ids would be inserted a second time.
    """
    url = _segments_url(session, user)
    seeded = await client.put(
        f"{url}/segments",
        json={
            "segments": [
                _segment("", 0.0, 1.0, "one"),
                _segment("", 1.0, 2.0, "two"),
                _segment("", 2.0, 3.0, "three"),
            ]
        },
    )
    assert seeded.status_code == 200
    first, second, _third = seeded.json()["segments"]
    locked_text = await client.patch(
        f"{url}/utterances/{first['id']}/text", json={"text": "ONE locked"}
    )
    assert locked_text.status_code == 200
    locked_speaker = await client.patch(
        f"{url}/utterances/{second['id']}/speaker",
        json={"new_speaker_name": "Dana", "scope": "utterance_only"},
    )
    assert locked_speaker.status_code == 200
    rows_before = _utterance_rows(session)
    locks = {
        row[0]: (row[3], row[4])
        for row in rows_before
        if row[2] != TranscriptUtteranceState.SUPERSEDED
    }
    assert locks[first["id"]] == (True, False)
    assert locks[second["id"]] == (False, True)
    read_before = (await client.get(f"{url}/utterances")).json()["utterances"]

    response = await client.put(
        f"{url}/segments",
        json={
            "segments": [
                _segment(segment["id"], segment["start"], segment["end"], "edited")
                for segment in (first, second)
            ]
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "This transcript has changed since it was loaded. Reload it and try again."
    )
    assert _utterance_rows(session) == rows_before
    read_after = (await client.get(f"{url}/utterances")).json()["utterances"]
    assert read_after == read_before
    assert [utterance["text"] for utterance in read_after] == [
        "ONE locked",
        "two",
        "three",
    ]


@pytest.mark.anyio
async def test_bulk_put_with_ids_nothing_holds_keeps_them_as_given(
    client: AsyncClient, session: Session, user: User
) -> None:
    """A full replace that upstream accepted persists the client's ids unchanged."""
    url = _segments_url(session, user)
    initial = (await client.get(f"{url}/utterances")).json()["utterances"]
    assert len(initial) == 1

    response = await client.put(
        f"{url}/segments",
        json={
            "segments": [
                _segment("client-seg-a", 0.0, 0.5, "first half"),
                _segment("client-seg-b", 0.5, 1.0, "second half"),
            ]
        },
    )

    assert response.status_code == 200
    assert [segment["id"] for segment in response.json()["segments"]] == [
        "client-seg-a",
        "client-seg-b",
    ]
    states = {row[0]: row[2] for row in _utterance_rows(session)}
    assert states[initial[0]["id"]] == TranscriptUtteranceState.SUPERSEDED
    assert states["client-seg-a"] != TranscriptUtteranceState.SUPERSEDED
    assert states["client-seg-b"] != TranscriptUtteranceState.SUPERSEDED


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
