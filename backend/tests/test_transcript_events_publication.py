"""Root commits send captured sequences; rollbacks and savepoints never send."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event, text
from sqlmodel import Session

from backend.models.speaker import RecordingSpeaker
from backend.tests import test_canonical_transcript_phase1 as schemas
from backend.tests.test_transcript_publication import _append, _recording, _transcript
from backend.utils.canonical_pipeline import get_canonical_transcript_revision


@pytest.fixture
def events_engine(tmp_path: Path) -> Iterator[Engine]:
    # Python's legacy SQLite mode releases an outermost SAVEPOINT as a commit.
    # Real transactions are necessary to exercise outer rollback faithfully.
    engine = create_engine(
        f"sqlite:///{tmp_path / 'events.db'}", connect_args={"autocommit": False}
    )
    with engine.begin() as connection:
        for name, schema in vars(schemas).items():
            if name.endswith("_SCHEMA"):
                connection.execute(text(schema))
    yield engine
    engine.dispose()


@pytest.mark.parametrize("survives", [False, True])
def test_only_root_commit_sends_surviving_savepoint_sequence(
    events_engine: Engine,
    stub_transcript_publication: list[dict[int, int]],
    survives: bool,
) -> None:
    with Session(events_engine) as session:
        recording_id = _recording(session)
        baseline = _transcript(session, recording_id).change_sequence
        stub_transcript_publication.clear()
        savepoint = session.begin_nested()
        _append(session, recording_id)
        session.flush()
        assert stub_transcript_publication == []
        if survives:
            savepoint.commit()
        else:
            savepoint.rollback()
        assert stub_transcript_publication == []
        session.commit()
        assert stub_transcript_publication == (
            [{recording_id: baseline + 1}] if survives else []
        )


def test_outer_rollback_discards_committed_savepoint_and_retry_publishes(
    events_engine: Engine, stub_transcript_publication: list[dict[int, int]]
) -> None:
    with Session(events_engine) as session:
        recording_id = _recording(session)
        baseline = _transcript(session, recording_id).change_sequence
        stub_transcript_publication.clear()
        with session.begin_nested():
            _append(session, recording_id)
        session.rollback()
        assert stub_transcript_publication == []
        _append(session, recording_id, prefix="retry")
        session.commit()
        assert stub_transcript_publication == [{recording_id: baseline + 1}]


def test_publication_performs_no_sql_after_commit_and_batches_recordings(
    events_engine: Engine, stub_transcript_publication: list[dict[int, int]]
) -> None:
    with Session(events_engine) as session:
        first, second = _recording(session), _recording(session)
        stub_transcript_publication.clear()
        _transcript(session, first).text = "Changed first"
        _transcript(session, second).text = "Changed second"
        session.flush()
        statements: list[str] = []

        def observe_sql(*arguments: Any) -> None:
            statements.append(arguments[2])

        event.listen(events_engine, "before_cursor_execute", observe_sql)
        try:
            session.commit()
        finally:
            event.remove(events_engine, "before_cursor_execute", observe_sql)
        assert statements == []
        assert stub_transcript_publication == [{first: 2, second: 2}]
        assert get_canonical_transcript_revision(session, first) == 0


def test_multiple_flushes_and_nested_writes_send_one_sequence(
    events_engine: Engine, stub_transcript_publication: list[dict[int, int]]
) -> None:
    with Session(events_engine) as session:
        recording_id = _recording(session)
        baseline = _transcript(session, recording_id).change_sequence
        stub_transcript_publication.clear()
        _append(session, recording_id)
        with session.begin_nested():
            _transcript(session, recording_id).text = "Inner change"
            session.flush()
        _transcript(session, recording_id).text = "Outer change"
        session.flush()
        session.commit()
        assert stub_transcript_publication == [{recording_id: baseline + 1}]


def test_failed_redis_publication_cannot_fail_a_committed_write(
    events_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.services import transcript_events_publish as publisher
    from backend.tests.test_transcript_events_publish import SyncPublisher
    from backend.utils import transcript_publication

    client = SyncPublisher(fail=True)
    monkeypatch.setattr(publisher.redis, "from_url", lambda *args, **kwargs: client)
    monkeypatch.setattr(
        transcript_publication,
        "publish_transcript_changes",
        publisher.publish_transcript_changes,
    )
    with Session(events_engine) as session:
        recording_id = _recording(session)
        _transcript(session, recording_id).text = "Durable despite Redis failure"
        session.commit()
    with Session(events_engine) as reader:
        assert _transcript(reader, recording_id).text == "Durable despite Redis failure"
        assert _transcript(reader, recording_id).change_sequence == 2


def test_name_only_hint_leaves_analytics_watermark_unchanged(
    events_engine: Engine, stub_transcript_publication: list[dict[int, int]]
) -> None:
    with Session(events_engine) as session:
        recording_id = _recording(session)
        speaker = RecordingSpeaker(
            recording_id=recording_id, diarization_label="SPEAKER_00", name="Speaker 1"
        )
        session.add(speaker)
        session.commit()
        baseline = _transcript(session, recording_id).change_sequence
        watermark = get_canonical_transcript_revision(session, recording_id)
        stub_transcript_publication.clear()
        speaker.local_name = "Mike"
        session.commit()
        assert stub_transcript_publication == [{recording_id: baseline + 1}]
        assert get_canonical_transcript_revision(session, recording_id) == watermark
