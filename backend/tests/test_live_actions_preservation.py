"""Accepted line work survives machine boundaries, empty results and resets."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

import pytest
from sqlalchemy import Engine, delete
from sqlmodel import Session, select

from backend.models.pipeline import (
    ProcessingRunKind,
    TranscriptUtterance,
    TranscriptUtteranceState,
)
from backend.models.recording import Recording, RecordingStatus
from backend.models.speaker import RecordingSpeaker
from backend.models.speaker_name_event import SpeakerNameEvent
from backend.tests.canonical_engine import _recording, _transcript
from backend.tests.canonical_engine import (
    publication_engine as publication_engine,
)
from backend.tests.test_canonical_publication_fence import Database
from backend.tests.test_canonical_publication_fence import (
    publication_database as publication_database,
)
from backend.tests.test_live_settling_finalisation import _closed_line
from backend.tests.test_reprocess_reset_postgres import _reset
from backend.utils.canonical_pipeline import (
    append_utterances_from_segments,
    finalize_utterances_from_segments,
    refresh_transcript_projection_from_canonical,
    serialize_canonical_utterances,
    update_utterance_text,
)
from backend.utils.canonical_pipeline.constants import _load_transcript
from backend.utils.canonical_pipeline.guards import LineActionConflict
from backend.utils.canonical_pipeline.queries import list_active_utterances
from backend.utils.canonical_pipeline.split import SplitCommand, split_utterance
from backend.utils.config_manager import config_manager
from backend.utils.speaker_names.durability import (
    persist_name_anchors,
    restore_name_anchors,
    snapshot_name_anchors,
)
from backend.utils.speaker_names.review import dismiss_pending_name
from backend.utils.transcript_settling import durable_settling_state
from backend.worker.tasks.final_asr import mark_transcript_failed


def _seed(session: Session, marker: Any = True, *, split: bool = False) -> int:
    recording_id = _recording(session)
    transcript = _transcript(session, recording_id)
    transcript.settling_state = {"settled": marker}
    session.add(transcript)
    segments = []
    for index, text in enumerate(("Human wording", "after split")):
        payload: dict[str, Any] = {
            "asr_segments": [
                {
                    "words": [
                        {
                            "id": f"human-word-{index}",
                            "order": index,
                            "word": text,
                            "start_ms": None,
                            "end_ms": None,
                            "locked": True,
                        }
                    ],
                }
            ],
            "human_protection": {"version": 1, "replacement": {"text": text}},
        }
        if split:
            payload["human_protection"] = {
                "version": 1,
                "split_group_id": "accepted-split",
                "source_public_id": "parent",
                "split_before_word_id": "boundary",
                "side": "left" if index == 0 else "right",
            }
        segments.append(
            {
                "start": index * 2,
                "end": index * 2 + 2,
                "text": text,
                "text_manually_edited": not split,
                "confidence_payload": payload,
            }
        )
    append_utterances_from_segments(
        session,
        recording_id=recording_id,
        segments=segments,
        run_kind=ProcessingRunKind.LIVE,
        source="live",
        state_override=TranscriptUtteranceState.STABLE,
    )
    session.commit()
    return recording_id


def _lines(session: Session, recording_id: int) -> list[dict[str, Any]]:
    refresh_transcript_projection_from_canonical(session, recording_id)
    session.commit()
    return serialize_canonical_utterances(
        session, recording_id, include_confidence_payload=True
    )


@pytest.mark.parametrize("split", [False, True])
def test_drift_keeps_human_components_and_offers_final_wording_once(
    publication_engine: Engine,
    split: bool,
) -> None:
    with Session(publication_engine) as session:
        recording_id = _seed(session, split=split)
        before = serialize_canonical_utterances(
            session, recording_id, include_confidence_payload=True
        )
        final = [
            {
                "start": 0.1,
                "end": 3.9,
                "text": "Machine merged wording",
                "speaker": "UNKNOWN",
            }
        ]
        finalize_utterances_from_segments(
            session, recording_id=recording_id, segments=final
        )
        lines = _lines(session, recording_id)
        assert [line["id"] for line in lines] == [line["id"] for line in before]
        assert [line["text"] for line in lines] == ["Human wording", "after split"]
        assert lines[0]["final_review"] == {
            "text": "Machine merged wording",
            "reason": "ambiguous_alignment",
        }
        assert "final_review" not in lines[1]
        assert all(line["locked"] and line["words"] for line in lines)
        assert [line["confidence_payload"]["human_protection"] for line in lines] == [
            line["confidence_payload"]["human_protection"] for line in before
        ]
        # Retrying the identical candidate neither duplicates lines nor revisions.
        revisions = [line["revision"] for line in lines]
        finalize_utterances_from_segments(
            session, recording_id=recording_id, segments=final
        )
        assert [line["revision"] for line in _lines(session, recording_id)] == revisions


def test_empty_success_keeps_only_protected_lines(publication_engine: Engine) -> None:
    with Session(publication_engine) as session:
        recording_id = _seed(session)
        append_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[{"start": 5, "end": 6, "text": "unprotected machine"}],
            run_kind=ProcessingRunKind.LIVE,
            source="live",
        )
        session.commit()
        finalize_utterances_from_segments(
            session, recording_id=recording_id, segments=[]
        )
        assert [line["text"] for line in _lines(session, recording_id)] == [
            "Human wording",
            "after split",
        ]


def test_exact_final_clears_an_old_review_without_replacing_human_atoms(
    publication_engine: Engine,
) -> None:
    with Session(publication_engine) as session:
        recording_id = _seed(session)
        finalize_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[
                {"start": 0.1, "end": 3.9, "text": "Earlier ambiguous final"},
            ],
        )
        assert _lines(session, recording_id)[0]["final_review"]
        finalize_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[
                {
                    "start": 0,
                    "end": 2,
                    "text": "Different native final",
                    "words": [{"word": "Different", "start": 0, "end": 1}],
                },
                {"start": 2, "end": 4, "text": "Native second final"},
            ],
        )
        lines = _lines(session, recording_id)
        assert "final_review" not in lines[0]
        assert lines[0]["words"][0]["word"] == "Human wording"
        assert lines[0]["words"][0]["start_ms"] is None


@pytest.mark.parametrize("marker", [False, 1, None])
def test_unmarked_drift_keeps_existing_machine_replacement_contract(
    publication_engine: Engine, marker: Any
) -> None:
    with Session(publication_engine) as session:
        recording_id = _seed(session, marker)
        finalize_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[
                {"start": 0.1, "end": 3.9, "text": "Authoritative final"},
            ],
        )
        lines = _lines(session, recording_id)
        assert [line["text"] for line in lines] == ["Authoritative final"]
        assert "final_review" not in lines[0]


def test_marked_reset_retains_accepted_view_and_name_anchors(
    publication_database: Database,
) -> None:
    engine, recording_id, _ = publication_database
    with Session(engine) as session:
        update_utterance_text(
            session,
            recording_id=recording_id,
            utterance_public_id="fence-a",
            text="Accepted correction",
        )
        transcript = _load_transcript(session, recording_id)
        assert transcript is not None
        transcript.settling_state = {"settled": True, "generation": "discard-runtime"}
        session.add(transcript)
        session.add(
            SpeakerNameEvent(
                recording_id=recording_id,
                kind="name",
                name="Alex",
                source="user",
                authority=100,
                surface="live",
                anchor_start_ms=20000,
                anchor_end_ms=29000,
                anchor_label="LIVE_01",
            )
        )
        session.flush()
        persist_name_anchors(session, recording_id)
        session.commit()
        before = serialize_canonical_utterances(session, recording_id)
        anchors = deepcopy(transcript.settling_state["name_anchors"])
    asyncio.run(_reset(engine, recording_id))
    with Session(engine) as session:
        transcript = _load_transcript(session, recording_id)
        assert transcript is not None
        assert serialize_canonical_utterances(session, recording_id) == before
        assert transcript.settling_state == {"settled": True, "name_anchors": anchors}
        assert transcript.transcript_status == "pending"
        assert transcript.text and "Accepted correction" in transcript.text
        assert session.exec(select(SpeakerNameEvent)).one().name == "Alex"


def test_name_snapshot_rebuild_is_idempotent_and_drops_a_superseded_act(
    publication_engine: Engine,
) -> None:
    with Session(publication_engine) as session:
        recording_id = _seed(session)
        first = SpeakerNameEvent(
            recording_id=recording_id,
            kind="name",
            name="Earlier",
            source="user",
            authority=100,
            surface="live",
            anchor_start_ms=0,
            anchor_end_ms=2000,
            anchor_label="LIVE_01",
        )
        session.add(first)
        session.flush()
        latest = SpeakerNameEvent(
            recording_id=recording_id,
            kind="name",
            name="Alex",
            source="user",
            authority=100,
            surface="live",
            anchor_start_ms=0,
            anchor_end_ms=2000,
            anchor_label="LIVE_01",
            supersedes_event_id=first.id,
        )
        session.add(latest)
        session.flush()
        persist_name_anchors(session, recording_id)
        transcript = _transcript(session, recording_id)
        assert [
            anchor["name"] for anchor in transcript.settling_state["name_anchors"]
        ] == ["Alex"]
        transcript.settling_state = durable_settling_state(transcript.settling_state)
        session.execute(
            delete(SpeakerNameEvent).where(
                SpeakerNameEvent.recording_id == recording_id
            )
        )
        session.commit()
        restore_name_anchors(session, recording_id)
        restore_name_anchors(session, recording_id)
        assert session.exec(select(SpeakerNameEvent)).one().name == "Alex"
        assert (
            snapshot_name_anchors(session, recording_id)
            == transcript.settling_state["name_anchors"]
        )


def test_machine_only_failed_reprocess_keeps_the_exact_previous_view(
    publication_database: Database,
) -> None:
    engine, recording_id, _ = publication_database
    with Session(engine) as session:
        transcript = _load_transcript(session, recording_id)
        assert transcript is not None
        transcript.settling_state = {"settled": True}
        session.add(transcript)
        session.commit()
        before = serialize_canonical_utterances(
            session, recording_id, include_confidence_payload=True
        )
    asyncio.run(_reset(engine, recording_id))
    with Session(engine) as session:
        mark_transcript_failed(session, recording_id, "Final engine failed")
        session.expunge_all()
        transcript = _load_transcript(session, recording_id)
        assert transcript is not None
        assert transcript.transcript_status == "error"
        assert transcript.segments == before
        assert (
            serialize_canonical_utterances(
                session, recording_id, include_confidence_payload=True
            )
            == before
        )


@pytest.mark.parametrize("action", ["edit", "split"])
@pytest.mark.parametrize("outcome", ["drift", "exact", "empty", "rollback"])
def test_actual_locked_line_actions_survive_final_publication(
    publication_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    outcome: str,
) -> None:
    monkeypatch.setattr(
        config_manager,
        "get",
        lambda key, default=None: {"live_speaker_labels": "off"}.get(key, default),
    )
    with Session(publication_engine) as session:
        recording_id, line = _closed_line(session)
        if action == "edit":
            update_utterance_text(
                session,
                recording_id=recording_id,
                utterance_public_id=line["id"],
                text="Accepted replacement",
                expected_revision=line["revision"],
            )
        else:
            split_utterance(
                session,
                SplitCommand(
                    recording_id=recording_id,
                    utterance_public_id=line["id"],
                    expected_revision=line["revision"],
                    before_word_id=line["words"][1]["id"],
                ),
            )
        before = _lines(session, recording_id)
        candidates = [
            {
                "start": 0.05,
                "end": 0.9,
                "text": "Machine alternative",
                "speaker": "UNKNOWN",
            },
        ]
        if outcome == "empty":
            candidates = []
        elif outcome == "exact":
            candidates = [
                {
                    "start": row["start"],
                    "end": row["end"],
                    "text": "Machine alternative",
                }
                for row in before
            ]
        finalize_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=candidates,
        )
        if outcome == "rollback":
            session.rollback()
        after = _lines(session, recording_id)
        assert [(row["id"], row["text"], row["words"]) for row in after] == [
            (row["id"], row["text"], row["words"]) for row in before
        ]
        assert sum("final_review" in row for row in after) == (outcome == "drift")
        if outcome == "rollback":
            assert [row["revision"] for row in after] == [
                row["revision"] for row in before
            ]


@pytest.mark.parametrize("marked", [False, True])
@pytest.mark.parametrize("locked", [False, True])
def test_name_dismissal_guards_its_source_before_identity_changes(
    publication_engine: Engine,
    marked: bool,
    locked: bool,
) -> None:
    with Session(publication_engine) as session:
        recording_id = _seed(session, marked)
        speaker = RecordingSpeaker(
            recording_id=recording_id,
            diarization_label="LIVE_01",
            name="Speaker 1",
            local_name="Alex",
            name_source="llm",
        )
        session.add(speaker)
        session.flush()
        for line in list_active_utterances(session, recording_id):
            line.speaker_label = "LIVE_01"
            line.recording_speaker_id = speaker.id
            line.confidence_payload = {
                **(line.confidence_payload or {}),
                "live_text_settling": {"line_locked": locked},
            }
            session.add(line)
        session.commit()
        if marked and not locked:
            with pytest.raises(LineActionConflict, match="still settling"):
                dismiss_pending_name(session, speaker, None)
            assert speaker.local_name == "Alex"
            assert session.exec(select(SpeakerNameEvent)).all() == []
        else:
            assert dismiss_pending_name(session, speaker, None) == "Speaker 1"
            assert session.exec(select(SpeakerNameEvent)).one().kind == "dismiss"


def test_reprocess_backfills_archived_human_work_before_refreshing_projection(
    publication_database: Database,
) -> None:
    engine, recording_id, _ = publication_database
    with Session(engine) as session:
        update_utterance_text(
            session,
            recording_id=recording_id,
            utterance_public_id="fence-a",
            text="Archived correction",
        )
        transcript = _load_transcript(session, recording_id)
        recording = session.get(Recording, recording_id)
        assert transcript is not None and recording is not None
        transcript.settling_state = {"settled": True}
        recording.status = RecordingStatus.ERROR
        session.add_all([transcript, recording])
        # Format 3 archives projection rows and rebuilds canonical rows later.
        session.execute(
            delete(TranscriptUtterance).where(
                TranscriptUtterance.recording_id == recording_id
            )
        )
        session.commit()
        archived_text = transcript.text
        assert transcript.segments and not serialize_canonical_utterances(
            session, recording_id
        )
    asyncio.run(_reset(engine, recording_id))
    with Session(engine) as session:
        transcript = _load_transcript(session, recording_id)
        assert transcript is not None and transcript.text == archived_text
        restored = serialize_canonical_utterances(session, recording_id)
        correction = next(
            line for line in restored if line["text"] == "Archived correction"
        )
        assert correction["text_manually_edited"] is True
        assert len(restored) == 3
