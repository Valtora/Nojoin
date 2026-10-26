"""Canonical publication takes Transcript before line locks and preserves state."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from backend.core.db import driver_urls
from backend.models.pipeline import (
    ProcessingRunKind,
    TranscriptUtterance,
    TranscriptUtteranceState,
)
from backend.models.recording import Recording, RecordingStatus
from backend.models.transcript import Transcript
from backend.models.user import User
from backend.tests.postgres_setup import fresh_schema
from backend.utils.canonical_pipeline import (
    append_utterances_from_segments,
    apply_compatibility_segment_replace,
    clear_utterance_manual_locks,
    finalize_utterances_from_segments,
    get_canonical_transcript_revision,
    refresh_transcript_projection_from_canonical,
    replace_utterances_from_segments,
    serialize_canonical_delta,
    serialize_canonical_utterances,
    update_utterance_speaker,
    update_utterance_text,
)
from backend.utils.canonical_pipeline.constants import _load_transcript
from backend.utils.canonical_pipeline.live_labels import (
    LiveLabelDecision,
    apply_live_speaker_labels,
)

SCHEMA = "canonical_publication_fence_test"
Database = tuple[Engine, int, list[dict[str, Any]]]


@pytest.fixture
def publication_database(postgres_test_url: str) -> Iterator[Database]:
    """Use the production schema and PostgreSQL worker driver in isolation."""
    engine = create_engine(
        driver_urls(postgres_test_url)[1],
        connect_args={"options": f"-csearch_path={SCHEMA},public"},
    )
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        fresh_schema(engine, SCHEMA)
        with Session(engine) as session:
            owner = User(username="fence-owner", hashed_password="x")
            session.add(owner)
            session.flush()
            recording = Recording(
                name="Capture",
                audio_path="capture.wav",
                user_id=owner.id,
                status=RecordingStatus.UPLOADING,
            )
            session.add(recording)
            session.flush()
            recording_id = int(recording.id)
            session.add(Transcript(recording_id=recording_id, text="", segments=[]))
            # Deliberately put row ID order and end-time order at odds.
            append_utterances_from_segments(
                session,
                recording_id=recording_id,
                segments=[
                    {"id": "fence-a", "start": 20, "end": 29, "text": "last"},
                    {"id": "fence-b", "start": 1, "end": 9, "text": "first"},
                    {"id": "fence-c", "start": 10, "end": 14, "text": "middle"},
                ],
                run_kind=ProcessingRunKind.LIVE,
                source="live",
                state_override=TranscriptUtteranceState.PROVISIONAL,
            )
            session.commit()
            segments = serialize_canonical_utterances(session, recording_id)
        yield engine, recording_id, segments
    finally:
        engine.dispose()


def _publish(
    session: Session, recording_id: int, operation: str, segments: list[dict[str, Any]]
) -> None:
    """Exercise the shared writers rather than a test-only locking function."""
    if operation == "text":
        update_utterance_text(
            session,
            recording_id=recording_id,
            utterance_public_id="fence-a",
            text="corrected",
        )
    elif operation == "speaker":
        update_utterance_speaker(
            session,
            recording_id=recording_id,
            utterance_public_id="fence-a",
            new_speaker_name="Ada",
        )
    elif operation == "clear":
        clear_utterance_manual_locks(
            session,
            recording_id=recording_id,
            utterance_public_id="fence-a",
        )
    elif operation == "compatibility":
        apply_compatibility_segment_replace(
            session,
            recording_id=recording_id,
            segments=[{**segment, "text": "replacement"} for segment in segments],
        )
    elif operation == "replace":
        replace_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=segments,
            run_kind=ProcessingRunKind.CATCH_UP,
            source="catch_up",
            force=True,
        )
    else:
        finalize_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=segments,
        )


@pytest.mark.parametrize(
    "operation", ["text", "speaker", "clear", "compatibility", "replace", "finalise"]
)
def test_writers_wait_for_transcript_without_holding_lines(
    publication_database: Database,
    operation: str,
) -> None:
    engine, recording_id, segments = publication_database
    waiting = threading.Event()

    def publish() -> None:
        with Session(engine) as session:
            session.execute(text("SET lock_timeout = '5s'"))

            def entering_lock(
                connection: Any, cursor: Any, statement: str, *args: Any
            ) -> None:
                if "FROM transcripts" in statement and "FOR UPDATE" in statement:
                    waiting.set()

            event.listen(session.connection(), "before_cursor_execute", entering_lock)
            _publish(session, recording_id, operation, segments)
            session.commit()

    with ThreadPoolExecutor(max_workers=1) as executor, Session(engine) as holder:
        _load_transcript(holder, recording_id, lock=True)
        future = executor.submit(publish)
        try:
            assert waiting.wait(5), "Writer never reached the Transcript fence"
            # Any line-before-Transcript acquisition makes NOWAIT fail here.
            with Session(engine) as observer:
                lines = (
                    observer.execute(
                        select(TranscriptUtterance)
                        .where(TranscriptUtterance.recording_id == recording_id)
                        .order_by(TranscriptUtterance.id)
                        .with_for_update(nowait=True)
                    )
                    .scalars()
                    .all()
                )
                assert len(lines) == 3
        finally:
            holder.rollback()
        future.result(timeout=10)


def test_repeated_fence_preserves_pending_projection_and_status(
    publication_database: Database,
) -> None:
    engine, recording_id, _ = publication_database
    with Session(engine) as session:
        transcript = _load_transcript(session, recording_id, lock=True)
        assert transcript is not None
        transcript.text = "pending publication"
        transcript.transcript_status = "completed"
        transcript.segments = [{"id": "protected", "text": "pending publication"}]
        flag_modified(transcript, "segments")
        assert _load_transcript(session, recording_id, lock=True) is transcript
        assert transcript.text == "pending publication"
        assert transcript.transcript_status == "completed"
        assert transcript.segments[0]["id"] == "protected"
        session.commit()
    with Session(engine) as session:
        transcript = _load_transcript(session, recording_id)
        assert transcript.text == "pending publication"
        assert transcript.segments[0]["id"] == "protected"


def test_first_fence_refreshes_stale_clean_projection(
    publication_database: Database,
) -> None:
    engine, recording_id, _ = publication_database
    with Session(engine) as reader:
        stale = _load_transcript(reader, recording_id)
        with Session(engine) as writer:
            update_utterance_text(
                writer,
                recording_id=recording_id,
                utterance_public_id="fence-a",
                text="corrected elsewhere",
            )
            writer.commit()
        assert "corrected elsewhere" not in stale.text
        assert _load_transcript(reader, recording_id, lock=True) is stale
        assert "corrected elsewhere" in stale.text


def test_first_fence_keeps_pending_fields_without_flushing(
    publication_database: Database,
) -> None:
    engine, recording_id, _ = publication_database
    with Session(engine) as session:
        transcript = _load_transcript(session, recording_id)
        original = transcript.text
        transcript.text = "staged before entering the fence"
        _load_transcript(session, recording_id, lock=True)
        assert transcript.text == "staged before entering the fence"
        stored = (
            session.connection()
            .execute(
                select(Transcript.text).where(Transcript.recording_id == recording_id)
            )
            .scalar_one()
        )
        assert stored == original
        session.commit()


def test_fence_reacquires_after_savepoint_rollback(
    publication_database: Database,
) -> None:
    engine, recording_id, _ = publication_database
    with Session(engine) as session:
        savepoint = session.begin_nested()
        _load_transcript(session, recording_id, lock=True)
        savepoint.rollback()
        with Session(engine) as writer:
            writer.execute(text("SET lock_timeout = '1s'"))
            update_utterance_text(
                writer,
                recording_id=recording_id,
                utterance_public_id="fence-a",
                text="committed after savepoint rollback",
            )
            writer.commit()
        transcript = _load_transcript(session, recording_id, lock=True)
        assert "committed after savepoint rollback" in transcript.text


def test_serialisation_and_delta_reads_do_not_publish(
    publication_database: Database,
) -> None:
    engine, recording_id, _ = publication_database
    with Session(engine) as session:
        transcript = _load_transcript(session, recording_id)
        before_sequence = transcript.change_sequence
        before_revision = get_canonical_transcript_revision(session, recording_id)
        before_ids = [
            row["id"] for row in serialize_canonical_utterances(session, recording_id)
        ]
        for _ in range(3):
            assert serialize_canonical_delta(
                session,
                recording_id,
                after_revision=before_revision,
            ) == (before_revision, [], [])
            assert [
                row["id"]
                for row in serialize_canonical_utterances(
                    session,
                    recording_id,
                )
            ] == before_ids
            session.commit()
        session.refresh(transcript)
        assert transcript.change_sequence == before_sequence
        assert (
            get_canonical_transcript_revision(session, recording_id) == before_revision
        )


def test_live_labels_lock_by_id_but_advance_in_end_time_order(
    publication_database: Database,
) -> None:
    engine, recording_id, _ = publication_database
    locked_queries: list[str] = []
    decisions: list[int] = []

    def record(connection: Any, cursor: Any, statement: str, *args: Any) -> None:
        if "FROM transcript_utterances" in statement and "FOR UPDATE" in statement:
            locked_queries.append(statement)

    def decide(start_ms: int, end_ms: int) -> LiveLabelDecision:
        decisions.append(end_ms)
        return LiveLabelDecision("LIVE_PROVISIONAL_1", locked=True, share=1.0)

    event.listen(engine, "before_cursor_execute", record)
    try:
        with Session(engine) as session:
            result = apply_live_speaker_labels(
                session,
                recording_id=recording_id,
                settled_ms=0,
                decide=decide,
            )
            session.commit()
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert decisions == [9000, 14000, 29000]
    assert result.settled_ms == 29000
    assert len(locked_queries) == 1
    assert "ORDER BY transcript_utterances.id FOR UPDATE" in locked_queries[0]


def test_projection_refresh_keeps_word_evidence(
    publication_database: Database,
) -> None:
    engine, recording_id, _ = publication_database
    evidence = {"asr_segments": [{"words": [{"word": "last", "start_ms": 20000}]}]}
    with Session(engine) as session:
        line = session.execute(
            select(TranscriptUtterance).where(
                TranscriptUtterance.public_id == "fence-a"
            )
        ).scalar_one()
        line.confidence_payload = evidence
        session.add(line)
        projection = refresh_transcript_projection_from_canonical(session, recording_id)
        assert (
            next(row for row in projection if row["id"] == "fence-a")[
                "confidence_payload"
            ]
            == evidence
        )
        session.commit()
    with Session(engine) as session:
        transcript = _load_transcript(session, recording_id)
        assert (
            next(row for row in transcript.segments if row["id"] == "fence-a")[
                "confidence_payload"
            ]
            == evidence
        )


def test_replacement_keeps_word_evidence_through_projection_refresh(
    publication_database: Database,
) -> None:
    engine, recording_id, _ = publication_database
    evidence = {"asr_segments": [{"words": [{"word": "timed", "start_ms": 1000}]}]}
    with Session(engine) as session:
        replacement = replace_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[
                {
                    "start": 1,
                    "end": 2,
                    "text": "timed",
                    "confidence_payload": evidence,
                }
            ],
            run_kind=ProcessingRunKind.BACKFILL,
            source="backfill",
            force=True,
        )
        assert replacement[0].confidence_payload == evidence
        projection = refresh_transcript_projection_from_canonical(session, recording_id)
        assert projection[0]["confidence_payload"] == evidence
        session.commit()


@pytest.mark.parametrize("route", ["direct", "permanent", "batch", "user"])
def test_deletion_waits_without_blocking_publishers_recording_foreign_keys(
    publication_database: Database,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
) -> None:
    from backend.api.v1.endpoints import users
    from backend.api.v1.endpoints.recordings import routes_actions, routes_batch_init

    engine, recording_id, _ = publication_database
    waiting = threading.Event()
    route_module = {"batch": routes_batch_init, "user": users}.get(
        route, routes_actions
    )
    monkeypatch.setattr(
        route_module,
        "delete_recording_artifacts_after_commit",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(route_module, "delete_document_files", AsyncMock())
    with Session(engine) as lookup:
        recording = lookup.get(Recording, recording_id)
        public_id, owner_id = recording.public_id, recording.user_id
        recording.is_deleted = True
        admin = User(username="deleting-owner", hashed_password="x", role="owner")
        lookup.add(admin)
        lookup.commit()
        admin_id = admin.id

    async def delete() -> Any:
        async_engine = create_async_engine(
            engine.url.set(drivername="postgresql+asyncpg"),
            connect_args={
                "server_settings": {"search_path": SCHEMA, "lock_timeout": "5s"}
            },
        )

        def entering_lock(
            connection: Any, cursor: Any, statement: str, *args: Any
        ) -> None:
            if ("FROM transcripts" in statement and "FOR UPDATE" in statement) or (
                route == "user" and statement.startswith("DELETE FROM users")
            ):
                waiting.set()

        event.listen(async_engine.sync_engine, "before_cursor_execute", entering_lock)
        try:
            async with AsyncSession(async_engine) as session:
                owner = User(id=owner_id, username="fence-owner", hashed_password="x")
                if route == "batch":
                    return await routes_batch_init.batch_permanently_delete_recordings(
                        routes_batch_init.BatchRecordingIds(recording_ids=[public_id]),
                        db=session,
                        current_user=owner,
                    )
                if route == "permanent":
                    return await routes_actions.permanently_delete_recording(
                        public_id, db=session, current_user=owner
                    )
                if route == "user":
                    return await users.delete_user(
                        db=session,
                        user_id=owner_id,
                        current_user=User(
                            id=admin_id,
                            username="deleting-owner",
                            hashed_password="x",
                            role="owner",
                        ),
                    )
                return await routes_actions.delete_recording(
                    public_id,
                    db=session,
                    current_user=owner,
                )
        finally:
            await async_engine.dispose()

    with ThreadPoolExecutor(max_workers=1) as executor, Session(engine) as publisher:
        publisher.execute(text("SET lock_timeout = '1s'"))
        _load_transcript(publisher, recording_id, lock=True)
        future = executor.submit(lambda: asyncio.run(delete()))
        try:
            assert waiting.wait(5), "Deletion never reached the Transcript fence"
            # Processing-run and utterance inserts perform real Recording FK checks.
            # A delete holding Recording FOR UPDATE would deadlock these inserts.
            append_utterances_from_segments(
                publisher,
                recording_id=recording_id,
                segments=[{"start": 30, "end": 31, "text": "published before delete"}],
                run_kind=ProcessingRunKind.LIVE,
                source="live",
            )
            publisher.commit()
        finally:
            publisher.rollback()
        result = future.result(timeout=10)
        assert result.id == owner_id if route == "user" else result["ok"] is True
    with Session(engine) as session:
        assert session.get(Recording, recording_id) is None
