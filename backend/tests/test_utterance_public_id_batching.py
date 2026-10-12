"""The utterance public-id lookups stay one batched query per write.

``UtterancePublicIds`` (finalize, backfill) and ``RequestedPublicIds`` (a
client's full-replace edit) check every requested id against
``transcript_utterances`` with an ``IN`` clause, batched under the bind
parameter ceiling. A long meeting has thousands of segments, so a lookup made
per segment would turn one finalize into thousands of round trips, and an
unbatched ``IN`` would fail on the API's asyncpg path once the ids outgrow the
ceiling. SQLite cannot show the Postgres error, so these count the statements
and their parameters instead, as test_bind_parameter_batching_call_sites.py
does for the other call sites.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from math import ceil
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlmodel import Session

from backend.tests.sqlite_schemas import TRANSCRIPT_UTTERANCES_SCHEMA
from backend.utils.canonical_pipeline.public_ids import (
    RequestedPublicIds,
    UtterancePublicIds,
)
from backend.utils.db_batching import MAX_BIND_PARAMS

RECORDING_ID = 1
OTHER_RECORDING_ID = 2
# One utterance per 1.2 s over a four-hour meeting.
LONG_RECORDING_SEGMENTS = 12_000
OVERSIZED_SEGMENTS = MAX_BIND_PARAMS + 1_000
POSTGRES_BIND_CEILING = 32_767


@pytest.fixture
def session(tmp_path) -> Iterator[Session]:
    # Configures the mappers the utterance model's relationships name.
    importlib.import_module("backend.models.registry")
    engine = create_engine(f"sqlite:///{tmp_path / 'public-ids.sqlite3'}")
    with engine.begin() as connection:
        connection.execute(text(TRANSCRIPT_UTTERANCES_SCHEMA))
    with Session(engine) as db_session:
        yield db_session
    engine.dispose()


def _hold(session: Session, public_id: str, recording_id: int) -> None:
    session.execute(
        text(
            "INSERT INTO transcript_utterances (created_at, updated_at, public_id,"
            " recording_id, sort_key, start_ms, end_ms, text, state, source_kind,"
            " revision, overlap_rank, manual_text_locked, manual_speaker_locked,"
            " speaker_assignment_source, speaker_assignment_authority)"
            " VALUES (CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :public_id,"
            " :recording_id, '0', 0, 1000, 'held', 'finalized', 'final', 1, 0,"
            " 0, 0, 'legacy', 'provisional')"
        ),
        {"public_id": public_id, "recording_id": recording_id},
    )
    session.commit()


def _capture_lookups(session: Session) -> list[int]:
    """Record the bind parameter count of each utterance lookup issued."""
    lookups: list[int] = []

    @event.listens_for(session.get_bind(), "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, *_):
        if "transcript_utterances" in statement:
            lookups.append(len(parameters))

    return lookups


def _segments(count: int) -> list[dict]:
    return [{"id": str(uuid4()), "text": f"segment {index}"} for index in range(count)]


def test_finalize_lookup_is_two_statements_for_a_long_recording(
    session: Session,
) -> None:
    """One query for the recording's own ids, one for everyone else's."""
    segments = _segments(LONG_RECORDING_SEGMENTS)
    held_elsewhere = segments[-1]["id"]
    _hold(session, held_elsewhere, OTHER_RECORDING_ID)
    lookups = _capture_lookups(session)

    public_ids = UtterancePublicIds(
        session, recording_id=RECORDING_ID, segments=segments
    )
    assigned = [public_ids.assign(dict(segment)) for segment in segments]

    assert len(lookups) == 2
    assert assigned[:-1] == [segment["id"] for segment in segments[:-1]]
    assert assigned[-1] != held_elsewhere


def test_edit_lookup_is_one_statement_for_a_long_recording(session: Session) -> None:
    segments = _segments(LONG_RECORDING_SEGMENTS)
    lookups = _capture_lookups(session)

    public_ids = RequestedPublicIds(session, segments)
    assigned = [public_ids.assign(segment) for segment in segments]

    assert len(lookups) == 1
    assert assigned == [segment["id"] for segment in segments]


def test_lookups_split_only_at_the_bind_ceiling(session: Session) -> None:
    """Past the ceiling each lookup splits into as few statements as fit."""
    segments = _segments(OVERSIZED_SEGMENTS)
    batches = ceil(OVERSIZED_SEGMENTS / MAX_BIND_PARAMS)
    lookups = _capture_lookups(session)

    UtterancePublicIds(session, recording_id=RECORDING_ID, segments=segments)
    RequestedPublicIds(session, segments)

    assert len(lookups) == 1 + batches + batches
    # A full batch plus the other-recordings filter's own parameter.
    assert max(lookups) == MAX_BIND_PARAMS + 1 < POSTGRES_BIND_CEILING
