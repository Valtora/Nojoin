"""Short locked speech can carry a live name independently of final coverage."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlmodel import Session, select

from backend.models.pipeline import (
    ProcessingRunKind,
    TranscriptUtterance,
    TranscriptUtteranceState,
)
from backend.models.speaker_name_event import SpeakerNameEvent
from backend.tests import test_canonical_transcript_phase1 as schemas
from backend.tests.canonical_engine import _recording, _transcript
from backend.utils.canonical_pipeline import append_utterances_from_segments
from backend.utils.canonical_pipeline.guards import LineActionConflict
from backend.utils.speaker_names.events import UserNameAct, as_name_event
from backend.utils.speaker_names.live import (
    live_speaker_names,
    record_live_speaker_name,
)
from backend.utils.speaker_names.resolve import resolve_names

LABEL = "LIVE_PROVISIONAL_1"


@pytest.fixture
def name_engine(tmp_path: Path) -> Iterator[Engine]:
    engine = create_engine(f"sqlite:///{tmp_path / 'short-name.db'}")
    with engine.begin() as connection:
        for name, schema in vars(schemas).items():
            if name.endswith("_SCHEMA"):
                connection.execute(text(schema))
    try:
        yield engine
    finally:
        engine.dispose()


def _segment(start_ms: int, end_ms: int, *, locked: bool) -> dict[str, Any]:
    return {
        "start": start_ms / 1000,
        "end": end_ms / 1000,
        "text": "Yes",
        "speaker": LABEL,
        "confidence_payload": {
            "live_text_settling": {
                "version": 1,
                "text_locked": locked,
                "line_locked": locked,
            },
            "live_speaker_label": {"locked": locked},
            "asr_segments": [
                {
                    "words": [
                        {
                            "id": str(uuid4()),
                            "order": 0,
                            "word": "Yes",
                            "start_ms": start_ms,
                            "end_ms": end_ms,
                            "locked": locked,
                        }
                    ]
                }
            ],
        },
    }


def _seed_short(
    session: Session,
    *,
    marked: bool,
    locked: bool = True,
    end_ms: int = 800,
    longer_turn: bool = False,
) -> tuple[int, TranscriptUtterance]:
    recording_id = _recording(session)
    transcript = _transcript(session, recording_id)
    transcript.settling_state = {"settled": True} if marked else None
    session.add(transcript)
    segments = [_segment(0, end_ms, locked=locked)]
    if longer_turn:
        segments.append(_segment(3000, 5000, locked=True))
    rows = append_utterances_from_segments(
        session,
        recording_id=recording_id,
        segments=segments,
        source="live",
        run_kind=ProcessingRunKind.LIVE,
        state_override=TranscriptUtteranceState.PROVISIONAL,
    )
    session.commit()
    return recording_id, rows[0]


@pytest.mark.parametrize("marked", [False, True])
@pytest.mark.parametrize("with_reference", [False, True])
def test_short_locked_line_records_a_live_name_without_changing_final_coverage(
    name_engine: Engine, marked: bool, with_reference: bool
) -> None:
    with Session(name_engine) as session:
        recording_id, row = _seed_short(session, marked=marked)
        reference = (row.public_id, row.revision) if with_reference else None
        event = record_live_speaker_name(
            session,
            recording_id,
            LABEL,
            UserNameAct("Alex", None, None, "live"),
            reference,
        )
        assert event is not None
        assert (event.anchor_start_ms, event.anchor_end_ms) == (0, 800)
        session.commit()

    with Session(name_engine) as session:
        event = session.exec(select(SpeakerNameEvent)).one()
        assert (event.name, event.anchor_label, event.surface) == (
            "Alex",
            LABEL,
            "live",
        )
        assert live_speaker_names(session, recording_id) == {LABEL: "Alex"}
        if marked:
            (anchor,) = _transcript(session, recording_id).settling_state[
                "name_anchors"
            ]
            assert (anchor["anchor_start_ms"], anchor["anchor_end_ms"]) == (0, 800)
            assert anchor["name"] == "Alex"
        resolved, placements = resolve_names(
            [as_name_event(event)], [("SPEAKER_00", 0, 800)]
        )
        assert resolved == {}
        assert (placements[0].outcome, placements[0].covered_ms) == ("unresolved", 800)


def test_explicit_short_locked_source_is_not_replaced_by_a_longer_turn(
    name_engine: Engine,
) -> None:
    with Session(name_engine) as session:
        recording_id, row = _seed_short(session, marked=True, longer_turn=True)
        event = record_live_speaker_name(
            session,
            recording_id,
            LABEL,
            UserNameAct("Alex", None, None, "live"),
            (row.public_id, row.revision),
        )
        assert event is not None
        assert (event.anchor_start_ms, event.anchor_end_ms) == (0, 800)


@pytest.mark.parametrize("with_reference", [False, True])
@pytest.mark.parametrize("locked,end_ms", [(False, 800), (True, 0)])
def test_settling_or_empty_line_does_not_create_a_name(
    name_engine: Engine, with_reference: bool, locked: bool, end_ms: int
) -> None:
    with Session(name_engine) as session:
        recording_id, row = _seed_short(
            session, marked=True, locked=locked, end_ms=end_ms
        )
        reference = (row.public_id, row.revision) if with_reference else None
        with pytest.raises(LineActionConflict, match="settling"):
            record_live_speaker_name(
                session,
                recording_id,
                LABEL,
                UserNameAct("Alex", None, None, "live"),
                reference,
            )
        session.commit()
        assert session.exec(select(SpeakerNameEvent)).all() == []
        assert "name_anchors" not in _transcript(session, recording_id).settling_state
