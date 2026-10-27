"""Human actions share one fenced policy and reject broad scopes atomically."""

import copy
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from backend.models.pipeline import (
    ProcessingRunKind,
    RecordingSpeakerAlias,
    SpeakerCorrectionScope,
    TranscriptUtterance,
    TranscriptUtteranceEvent,
    TranscriptUtteranceState,
)
from backend.models.speaker import RecordingSpeaker
from backend.models.speaker_name_event import SpeakerNameEvent
from backend.models.timeline_note import TimelineNoteKind
from backend.models.timeline_note_public import TimelineNoteCreate, TimelineNoteUpdate
from backend.models.transcript import Transcript
from backend.services.timeline_note_anchors import guard_note_anchor
from backend.tests.test_live_settling_publication import _seed
from backend.tests.test_live_settling_publication import (
    settling_engine as settling_engine,
)
from backend.utils.canonical_pipeline import (
    SplitCommand,
    append_utterances_from_segments,
    apply_compatibility_segment_replace,
    clear_utterance_manual_locks,
    serialize_canonical_delta,
    serialize_canonical_utterances,
    split_utterance,
    update_utterance_speaker,
    update_utterance_text,
)
from backend.utils.canonical_pipeline.guards import (
    LineActionConflict,
    settling_required,
)
from backend.utils.canonical_pipeline.live_settling_projection import row_words
from backend.utils.speaker_names.events import UserNameAct
from backend.utils.speaker_names.live import record_live_speaker_name


def _lines(
    engine: Engine, *, marker: bool = True, second_locked: bool = False
) -> tuple[int, list[str]]:
    recording_id = _seed(engine)
    segments: list[dict[str, Any]] = []
    for index, locked in enumerate((True, second_locked)):
        start = index * 2000
        words = [
            {
                "id": str(uuid4()),
                "order": index * 2 + i,
                "word": word,
                "start_ms": start + i * 500,
                "end_ms": start + i * 500 + 450,
                "locked": locked,
            }
            for i, word in enumerate(("First", " words."))
        ]
        segments.append(
            {
                "start": start / 1000,
                "end": (start + 1500) / 1000,
                "text": "First words.",
                "speaker": "LIVE_PROVISIONAL_1",
                "confidence_payload": {
                    "live_text_settling": {
                        "version": 1,
                        "generation": "episode-a",
                        "text_locked": locked,
                        "line_locked": locked,
                    },
                    "live_speaker_label": {"locked": locked},
                    "asr_segments": [{"words": words}],
                },
            }
        )
    with Session(engine) as session:
        transcript = session.exec(
            select(Transcript).where(Transcript.recording_id == recording_id)
        ).one()
        transcript.settling_state = (
            {**transcript.settling_state, "settled": True} if marker else None
        )
        rows = append_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=segments,
            source="live",
            run_kind=ProcessingRunKind.LIVE,
            state_override=TranscriptUtteranceState.PROVISIONAL,
        )
        ids = [row.public_id for row in rows]
        session.commit()
    return recording_id, ids


def _snapshot(session: Session, recording_id: int) -> dict[str, Any]:
    transcript = session.exec(
        select(Transcript).where(Transcript.recording_id == recording_id)
    ).one()
    return copy.deepcopy(
        {
            "transcript": (
                transcript.text,
                transcript.segments,
                transcript.change_sequence,
            ),
            "rows": [
                (
                    row.public_id,
                    row.text,
                    row.speaker_label,
                    row.revision,
                    row.state,
                    row.confidence_payload,
                )
                for row in session.exec(
                    select(TranscriptUtterance).where(
                        TranscriptUtterance.recording_id == recording_id
                    )
                ).all()
            ],
            "events": len(session.exec(select(TranscriptUtteranceEvent)).all()),
            "speakers": len(session.exec(select(RecordingSpeaker)).all()),
            "aliases": len(session.exec(select(RecordingSpeakerAlias)).all()),
            "names": len(session.exec(select(SpeakerNameEvent)).all()),
        }
    )


@pytest.mark.parametrize("scope", list(SpeakerCorrectionScope))
def test_speaker_scope_preflights_every_target_before_creating_a_speaker(
    settling_engine: Engine, scope: SpeakerCorrectionScope
) -> None:
    recording_id, ids = _lines(settling_engine)
    with Session(settling_engine) as session:
        before = _snapshot(session, recording_id)
        anchor = ids[1] if scope == SpeakerCorrectionScope.UTTERANCE_ONLY else ids[0]
        with pytest.raises(LineActionConflict, match="settling"):
            update_utterance_speaker(
                session,
                recording_id=recording_id,
                utterance_public_id=anchor,
                new_speaker_name="Brand new person",
                scope=scope,
            )
        session.commit()
        assert _snapshot(session, recording_id) == before


@pytest.mark.parametrize("full_replace", [False, True])
def test_bulk_rejection_survives_a_caller_catching_and_committing(
    settling_engine: Engine, full_replace: bool
) -> None:
    recording_id, _ids = _lines(settling_engine)
    with Session(settling_engine) as session:
        before = _snapshot(session, recording_id)
        segments = serialize_canonical_utterances(session, recording_id)
        for segment in segments:
            segment["text"] = "Changed"
            segment["text_manually_edited"] = True
            if full_replace:
                segment["id"] = str(uuid4())
        with pytest.raises(LineActionConflict, match="settling"):
            apply_compatibility_segment_replace(
                session, recording_id=recording_id, segments=segments
            )
        session.commit()
        assert _snapshot(session, recording_id) == before


def test_locked_bulk_edit_preserves_word_protection(
    settling_engine: Engine,
) -> None:
    recording_id, ids = _lines(settling_engine, second_locked=True)
    with Session(settling_engine) as session:
        segments = serialize_canonical_utterances(session, recording_id)
        segments[0]["text"] = "Human correction."
        segments[0]["text_manually_edited"] = True
        apply_compatibility_segment_replace(
            session, recording_id=recording_id, segments=segments
        )
        session.commit()
        rows = session.exec(
            select(TranscriptUtterance).order_by(TranscriptUtterance.start_ms)
        ).all()
        assert rows[0].public_id == ids[0]
        assert rows[0].text == "Human correction."
        assert row_words(rows[0])[0]["start_ms"] is None
        assert rows[1].text == "First words."


def test_bulk_unchanged_unlocked_line_cannot_be_promoted_by_omitting_provisional(
    settling_engine: Engine,
) -> None:
    recording_id, _ids = _lines(settling_engine)
    with Session(settling_engine) as session:
        before = _snapshot(session, recording_id)
        segments = serialize_canonical_utterances(session, recording_id)
        for segment in segments:
            segment.pop("provisional", None)
        with pytest.raises(LineActionConflict, match="settling"):
            apply_compatibility_segment_replace(
                session, recording_id=recording_id, segments=segments
            )
        session.commit()
        assert _snapshot(session, recording_id) == before


def test_bulk_cannot_bypass_word_split_by_replacing_locked_ids(
    settling_engine: Engine,
) -> None:
    recording_id, _ids = _lines(settling_engine, second_locked=True)
    with Session(settling_engine) as session:
        before = _snapshot(session, recording_id)
        segments = serialize_canonical_utterances(session, recording_id)
        segments[0]["id"] = str(uuid4())
        with pytest.raises(LineActionConflict, match="split action"):
            apply_compatibility_segment_replace(
                session, recording_id=recording_id, segments=segments
            )
        session.commit()
        assert _snapshot(session, recording_id) == before


def test_split_preserves_words_and_emits_delta_then_rejects_stale_parent(
    settling_engine: Engine,
) -> None:
    recording_id, ids = _lines(settling_engine, second_locked=True)
    with Session(settling_engine) as session:
        parent = session.exec(
            select(TranscriptUtterance).where(TranscriptUtterance.public_id == ids[0])
        ).one()
        words = row_words(parent)
        revision, _rows, _tombstones = serialize_canonical_delta(session, recording_id)
        command = SplitCommand(
            recording_id, parent.public_id, words[1]["id"], parent.revision
        )
        children = split_utterance(session, command)
        session.commit()
        assert [child.text for child in children] == ["First", "words."]
        assert [word for child in children for word in row_words(child)] == words
        assert {
            child.confidence_payload["human_protection"]["side"] for child in children
        } == {"left", "right"}
        _cursor, delta, tombstones = serialize_canonical_delta(
            session, recording_id, after_revision=revision
        )
        assert ids[0] in tombstones
        assert {child.public_id for child in children} <= {line["id"] for line in delta}
        with pytest.raises(LineActionConflict, match="changed"):
            split_utterance(session, command)
        with pytest.raises(LineActionConflict, match="changed"):
            update_utterance_text(
                session,
                recording_id=recording_id,
                utterance_public_id=ids[0],
                text="Invisible edit",
            )


def test_human_text_is_one_untimed_atom_and_cannot_be_split(
    settling_engine: Engine,
) -> None:
    recording_id, ids = _lines(settling_engine)
    with Session(settling_engine) as session:
        row = update_utterance_text(
            session,
            recording_id=recording_id,
            utterance_public_id=ids[0],
            text="Correct whole phrase",
        )
        words = row_words(row)
        assert len(words) == 1 and words[0]["word"] == row.text
        assert words[0]["start_ms"] is None and words[0]["end_ms"] is None
        assert (
            len(
                row.confidence_payload["human_protection"]["replacement"][
                    "source_word_ids"
                ]
            )
            == 2
        )
        with pytest.raises(LineActionConflict):
            split_utterance(
                session,
                SplitCommand(recording_id, row.public_id, words[0]["id"], row.revision),
            )


def test_selection_guards_words_but_drag_and_typed_notes_remain_available(
    settling_engine: Engine,
) -> None:
    recording_id, ids = _lines(settling_engine)
    with Session(settling_engine) as session:
        selection = TimelineNoteCreate(
            kind=TimelineNoteKind.SELECTION,
            body="note",
            start_ms=0,
            end_ms=3500,
            quoted_text="First words.",
        )
        with pytest.raises(LineActionConflict, match="settling"):
            guard_note_anchor(session, recording_id, selection)
        # Timeline moves no longer require a line lock; source selections do.
        guard_note_anchor(
            session,
            recording_id,
            TimelineNoteUpdate(
                expected_revision=1,
                anchor_ms=2000,
                target_utterance_id=ids[1],
                target_utterance_revision=1,
            ),
        )
        for kind, kwargs in (
            (TimelineNoteKind.HEAD, {}),
            (TimelineNoteKind.TAIL, {}),
            (TimelineNoteKind.POINT, {"anchor_ms": 2200}),
        ):
            guard_note_anchor(
                session,
                recording_id,
                TimelineNoteCreate(kind=kind, body="typed", **kwargs),
            )
        guard_note_anchor(
            session,
            recording_id,
            TimelineNoteCreate(
                kind=TimelineNoteKind.SELECTION,
                body="locked",
                start_ms=0,
                end_ms=1500,
                quoted_text="First words.",
                source_utterances=[{"id": ids[0], "revision": 1}],
            ),
        )
        with pytest.raises(LineActionConflict, match="changed"):
            guard_note_anchor(
                session,
                recording_id,
                TimelineNoteCreate(
                    kind=TimelineNoteKind.SELECTION,
                    body="stale",
                    start_ms=0,
                    end_ms=1500,
                    quoted_text="First words.",
                    source_utterances=[{"id": ids[0], "revision": 99}],
                ),
            )


def test_clearing_manual_protection_updates_durable_projection(
    settling_engine: Engine,
) -> None:
    recording_id, ids = _lines(settling_engine)
    with Session(settling_engine) as session:
        row = update_utterance_text(
            session,
            recording_id=recording_id,
            utterance_public_id=ids[0],
            text="Protected",
        )
        payload = dict(row.confidence_payload)
        payload["final_review"] = {
            "text": "machine alternative",
            "reason": "ambiguous_alignment",
        }
        row.confidence_payload = payload
        session.add(row)
        session.flush()
        clear_utterance_manual_locks(
            session,
            recording_id=recording_id,
            utterance_public_id=row.public_id,
            expected_revision=row.revision,
        )
        session.commit()
        transcript = session.exec(
            select(Transcript).where(Transcript.recording_id == recording_id)
        ).one()
        projected = next(
            segment for segment in transcript.segments if segment["id"] == row.public_id
        )
        assert "human_protection" not in row.confidence_payload
        assert "final_review" not in row.confidence_payload
        assert "human_protection" not in projected["confidence_payload"]
        assert "final_review" not in projected["confidence_payload"]
        assert projected["text_manually_edited"] is False


def test_naming_uses_only_fully_locked_turns_and_never_falls_back(
    settling_engine: Engine,
) -> None:
    recording_id, ids = _lines(settling_engine)
    with Session(settling_engine) as session:
        act = UserNameAct("Alex", None, None, "live")
        event = record_live_speaker_name(
            session, recording_id, "LIVE_PROVISIONAL_1", act
        )
        assert (event.anchor_start_ms, event.anchor_end_ms) == (0, 1500)
        with pytest.raises(LineActionConflict, match="settling"):
            record_live_speaker_name(
                session, recording_id, "LIVE_PROVISIONAL_1", act, (ids[1], 1)
            )
        row = session.exec(
            select(TranscriptUtterance).where(TranscriptUtterance.public_id == ids[0])
        ).one()
        payload = copy.deepcopy(row.confidence_payload)
        payload["live_text_settling"]["line_locked"] = False
        row.confidence_payload = payload
        session.add(row)
        session.flush()
        with pytest.raises(LineActionConflict, match="settling"):
            record_live_speaker_name(session, recording_id, "LIVE_PROVISIONAL_1", act)


def test_unmarked_and_never_settled_cpu_lines_keep_existing_actions(
    settling_engine: Engine,
) -> None:
    recording_id, ids = _lines(settling_engine, marker=False)
    with Session(settling_engine) as session:
        transcript = session.exec(
            select(Transcript).where(Transcript.recording_id == recording_id)
        ).one()
        for state in (None, {"eligible": True, "policy": {"enabled": False}}):
            transcript.settling_state = state
            assert not settling_required(transcript)
            row = update_utterance_text(
                session,
                recording_id=recording_id,
                utterance_public_id=ids[1],
                text="Legacy edit",
            )
            assert len(row_words(row)) == 2
            guard_note_anchor(
                session,
                recording_id,
                TimelineNoteCreate(
                    kind=TimelineNoteKind.SELECTION,
                    body="legacy",
                    start_ms=0,
                    end_ms=3500,
                    quoted_text="legacy",
                ),
            )


def test_sticky_final_rows_remain_editable_after_runtime_metadata_is_removed(
    settling_engine: Engine,
) -> None:
    recording_id, ids = _lines(settling_engine)
    with Session(settling_engine) as session:
        transcript = session.exec(
            select(Transcript).where(Transcript.recording_id == recording_id)
        ).one()
        transcript.settling_state = {"settled": True}
        row = session.exec(
            select(TranscriptUtterance).where(TranscriptUtterance.public_id == ids[1])
        ).one()
        row.state, row.confidence_payload = TranscriptUtteranceState.FINALIZED, None
        session.add(row)
        session.flush()
        update_utterance_text(
            session,
            recording_id=recording_id,
            utterance_public_id=row.public_id,
            text="Final correction",
        )
        public = serialize_canonical_utterances(session, recording_id)
        assert (
            next(line for line in public if line["id"] == row.public_id)["locked"]
            is True
        )
        assert (
            row.confidence_payload["human_protection"]["replacement"]["text"]
            == "Final correction"
        )
