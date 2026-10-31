"""A settling step's reads stay bounded as the meeting grows (#776).

The step's snapshot and publication select lines on settling flags that sit in
each line's compressed payload. Tested in SQL, every live line's payload was
decompressed on every 2 s step, so an hour in a step cost more than twice what
it did at the start. The flags are now kept per row version. These tests pin
that the selections are exactly the SQL's, kept flags included, and that the
payloads a step reads no longer grow with the transcript.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session

from backend.core.db import driver_urls
from backend.models.pipeline import TranscriptUtterance, TranscriptUtteranceState
from backend.models.recording import RecordingStatus
from backend.models.transcript import Transcript
from backend.tests.storage_hygiene_harness import add_recording
from backend.tests.test_live_settling_publication import _request
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)
from backend.utils.canonical_pipeline import live_settling_flags
from backend.utils.canonical_pipeline.live_settling import (
    legacy_live_frontier,
    live_settling_frontier,
    load_live_settling_words,
    publish_live_settling,
)
from backend.utils.canonical_pipeline.live_settling_projection import (
    SETTLING_KEY,
    live_rows,
)

ABSENT = object()


def _seed(engine: Engine) -> int:
    """A capture whose settling episode is live, as ``_seed`` makes one."""
    recording_id = add_recording(
        engine, f"settling-{uuid4().hex}.wav", status=RecordingStatus.UPLOADING
    )
    with Session(engine) as session:
        session.add(
            Transcript(
                recording_id=recording_id,
                segments=[],
                settling_state={
                    "version": 1,
                    "eligible": True,
                    "generation": "episode-a",
                    "capture_epoch": 0,
                    "sealed": False,
                    "phase": "live",
                    "policy": {"enabled": True},
                },
            )
        )
        session.commit()
    return recording_id


def _line(
    recording_id: int,
    index: int,
    *,
    flags: dict[str, Any] | None = None,
    state: TranscriptUtteranceState = TranscriptUtteranceState.PROVISIONAL,
    words: int = 60,
) -> TranscriptUtterance:
    """A live line whose payload, like a real one, is stored out of line."""
    start = index * 10_000
    flags = (
        {
            "version": 1,
            "text_locked": True,
            "line_locked": True,
            "line_closed": True,
        }
        if flags is None
        else flags
    )
    payload: dict[str, Any] = {
        "asr_segments": [
            {
                "start_ms": start,
                "end_ms": start + 9_000,
                "words": [
                    {
                        "id": str(uuid4()),
                        "word": f" word{index}x{n}",
                        "start_ms": start + n * 100,
                        "end_ms": start + n * 100 + 80,
                        "locked": True,
                        "last_decode_id": str(uuid4()),
                        "source_id": uuid4().hex + uuid4().hex,
                    }
                    for n in range(words)
                ],
            }
        ]
    }
    settling = {key: value for key, value in flags.items() if value is not ABSENT}
    if "absent" not in flags:
        payload[SETTLING_KEY] = settling
    return TranscriptUtterance(
        recording_id=recording_id,
        public_id=str(uuid4()),
        sort_key=f"{start:020d}:{index:08d}",
        start_ms=start,
        end_ms=start + 9_000,
        text=f"line {index}",
        state=state,
        source_kind="live",
        speaker_assignment_source="live",
        speaker_assignment_authority="provisional",
        confidence_payload=payload,
    )


def _both_ways(
    session: Session,
    recording_id: int,
    window_start_ms: int,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """What the kept flags select, and what the SQL selects."""
    import backend.utils.canonical_pipeline.live_settling as canonical

    def read() -> tuple[list[int], int, int]:
        return (
            [
                row.id
                for row in live_rows(
                    session, recording_id, window_start_ms=window_start_ms
                )
            ],
            live_settling_frontier(session, recording_id=recording_id),
            legacy_live_frontier(session, recording_id=recording_id),
        )

    kept = read()
    with monkeypatch.context() as patch:
        for module in (canonical, live_settling_flags):
            patch.setattr(module, "flags_readable", lambda _session: False)
        sql = read()
    return kept, sql


def test_kept_flags_select_exactly_what_the_sql_selects(
    settling_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording_id = _seed(settling_engine)
    other = _seed(settling_engine)
    values = (True, False, ABSENT)
    combinations = list(
        itertools.product((1, ABSENT, 2), values, values, values, (False, True))
    )
    with Session(settling_engine) as session:
        for index, (version, text_locked, line_locked, closed, late) in enumerate(
            combinations
        ):
            session.add(
                _line(
                    recording_id,
                    index + (1_000 if late else 0),
                    flags={
                        "version": version,
                        "text_locked": text_locked,
                        "line_locked": line_locked,
                        "line_closed": closed,
                    },
                    words=2,
                )
            )
        # Lines the selections must leave out, and an unmarked payload.
        session.add(_line(recording_id, 5, flags={"absent": True}, words=2))
        session.add(_line(recording_id, 6, state=TranscriptUtteranceState.SUPERSEDED))
        final = _line(recording_id, 7, words=2)
        final.source_kind = "final"
        session.add(final)
        session.add(_line(other, 3_000, flags={"text_locked": False}, words=2))
        session.commit()

    with Session(settling_engine) as session:
        for window_start_ms in (0, 5_000_000, 10_000_000, 99_000_000):
            kept, sql = _both_ways(session, recording_id, window_start_ms, monkeypatch)
            assert kept == sql
            assert sql[0], "the window must select some lines"

        # A changed line is read again, also when it changes twice in one
        # transaction (the same xmin, a new ctid).
        rows = live_rows(session, recording_id, window_start_ms=10_000_000)
        target = next(
            row
            for row in rows
            if (row.confidence_payload or {}).get(SETTLING_KEY, {}).get("version") == 1
        )
        for flags in (
            {"version": 1, "text_locked": False},
            {"version": 1, "text_locked": True, "line_locked": True},
        ):
            target.confidence_payload = {
                **(target.confidence_payload or {}),
                SETTLING_KEY: flags,
            }
            session.add(target)
            session.flush()
            kept, sql = _both_ways(session, recording_id, 10_000_000, monkeypatch)
            assert kept == sql
        session.commit()
        kept, sql = _both_ways(session, recording_id, 10_000_000, monkeypatch)
        assert kept == sql


@pytest.fixture
def one_connection(settling_engine: Engine, postgres_test_url: str) -> Iterator[Any]:
    """A single server backend, so its statistics can be flushed and read."""
    engine = create_engine(
        driver_urls(postgres_test_url)[1],
        connect_args={"options": "-csearch_path=live_settling_test,public"},
        poolclass=StaticPool,
    )

    def toast_blocks() -> int:
        with engine.connect() as connection:
            connection.execute(text("SELECT pg_stat_force_next_flush()"))
            connection.commit()
            connection.execute(text("SELECT pg_stat_clear_snapshot()"))
            return int(
                connection.execute(
                    text(
                        "SELECT coalesce(toast_blks_read, 0) + coalesce(toast_blks_hit, 0)"
                        " FROM pg_statio_all_tables"
                        " WHERE relid = 'transcript_utterances'::regclass"
                    )
                ).scalar_one()
            )

    yield engine, toast_blocks
    engine.dispose()


def _step(engine: Engine, recording_id: int, index: int, end_ms: int) -> None:
    """The step's snapshot, then its publication of the newest words."""
    with Session(engine) as session:
        load_live_settling_words(
            session, recording_id=recording_id, window_start_ms=end_ms - 10_000
        )
        live_settling_frontier(session, recording_id=recording_id)
        legacy_live_frontier(session, recording_id=recording_id)
    with Session(engine) as session:
        result = publish_live_settling(
            session,
            request=_request(session, recording_id, index, end_ms=end_ms),
            words=[
                {
                    "word": " still",
                    "start_ms": end_ms - 1_500,
                    "end_ms": end_ms - 1_200,
                },
                {
                    "word": " talking",
                    "start_ms": end_ms - 1_000,
                    "end_ms": end_ms - 600,
                },
            ],
        )
        assert result.accepted
        session.commit()


def test_payloads_a_step_reads_do_not_grow_with_the_transcript(
    one_connection: Any,
) -> None:
    engine, toast_blocks = one_connection

    def blocks_per_step(locked_lines: int) -> int:
        # Seeded on the measured backend too, so no other backend's pending
        # statistics can land inside the measurement.
        recording_id = _seed(engine)
        with Session(engine) as session:
            session.add_all(_line(recording_id, index) for index in range(locked_lines))
            session.commit()
        end_ms = locked_lines * 10_000 + 20_000
        # The first step reads every line's flags once; later steps should not.
        _step(engine, recording_id, 0, end_ms)
        _step(engine, recording_id, 1, end_ms + 2_000)
        before = toast_blocks()
        _step(engine, recording_id, 2, end_ms + 4_000)
        return toast_blocks() - before

    short, long = blocks_per_step(20), blocks_per_step(120)

    assert short > 0, "the lines' payloads must be stored out of line"
    assert long <= short + 4, (short, long)
