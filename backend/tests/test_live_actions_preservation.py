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
    update_utterance_speaker,
    update_utterance_text,
)
from backend.utils.canonical_pipeline.constants import _load_transcript
from backend.utils.canonical_pipeline.split import SplitCommand, split_utterance
from backend.utils.config_manager import config_manager
from backend.utils.speaker_names.durability import (
    persist_name_anchors,
    restore_name_anchors,
    snapshot_name_anchors,
)
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


def test_speaker_correction_takes_final_wording_and_keeps_the_chosen_speaker(
    publication_engine: Engine,
) -> None:
    with Session(publication_engine) as session:
        recording_id = _recording(session)
        transcript = _transcript(session, recording_id)
        transcript.settling_state = {"settled": True}
        session.add(transcript)
        append_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[
                {
                    "start": index * 2,
                    "end": index * 2 + 2,
                    "text": f"live {index}",
                    "speaker": "LIVE_00",
                }
                for index in range(20)
            ],
            run_kind=ProcessingRunKind.LIVE,
            source="live",
            state_override=TranscriptUtteranceState.STABLE,
        )
        session.commit()
        corrected = serialize_canonical_utterances(session, recording_id)[7]
        _, speaker = update_utterance_speaker(
            session,
            recording_id=recording_id,
            utterance_public_id=corrected["id"],
            new_speaker_name="Chosen person",
            expected_revision=corrected["revision"],
        )
        speaker_id = speaker.id
        session.commit()
        # Final boundaries drift half a second, so each segment overlaps two lines.
        finalize_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[
                {
                    "start": index * 2 + 0.5,
                    "end": index * 2 + 2.5,
                    "text": f"final {index}",
                    "speaker": "SPEAKER_00",
                }
                for index in range(20)
            ],
        )
        lines = _lines(session, recording_id)
        assert [line["text"] for line in lines] == [f"final {i}" for i in range(20)]
        assert not any("final_review" in line for line in lines)
        assert lines[7]["recording_speaker_id"] == speaker_id
        assert lines[7]["speaker_manually_edited"]
        assert lines[0]["recording_speaker_id"] != speaker_id


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


def test_exact_final_replaces_an_old_review_without_replacing_human_atoms(
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
                    "text": " Human wording ",
                    "words": [{"word": "Human", "start": 0, "end": 1}],
                },
                {"start": 2, "end": 4, "text": "Native second final"},
            ],
        )
        lines = _lines(session, recording_id)
        # Identical wording needs no review; a different final is offered
        # beside the kept text instead of vanishing.
        assert "final_review" not in lines[0]
        assert [line["text"] for line in lines] == ["Human wording", "after split"]
        assert lines[1]["final_review"] == {
            "text": "Native second final",
            "reason": "ambiguous_alignment",
        }
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
        reviews = [
            row["final_review"]["text"] for row in after if "final_review" in row
        ]
        expected = {"drift": 1, "exact": len(after)}.get(outcome, 0)
        assert reviews == ["Machine alternative"] * expected
        if outcome == "rollback":
            assert [row["revision"] for row in after] == [
                row["revision"] for row in before
            ]


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


def test_final_speech_beside_a_text_correction_stays_ordinary_transcript(
    publication_engine: Engine,
) -> None:
    with Session(publication_engine) as session:
        recording_id = _recording(session)
        transcript = _transcript(session, recording_id)
        transcript.settling_state = {"settled": True}
        session.add(transcript)
        append_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[
                {
                    "start": index * 2,
                    "end": index * 2 + 2,
                    "text": f"live {index}",
                    "speaker": "LIVE_00",
                }
                for index in range(15)
            ],
            run_kind=ProcessingRunKind.LIVE,
            source="live",
            state_override=TranscriptUtteranceState.STABLE,
        )
        session.commit()
        live = serialize_canonical_utterances(session, recording_id)
        update_utterance_text(
            session,
            recording_id=recording_id,
            utterance_public_id=live[7]["id"],
            text="Corrected words",
        )
        _, speaker = update_utterance_speaker(
            session,
            recording_id=recording_id,
            utterance_public_id=live[8]["id"],
            new_speaker_name="Chosen person",
            expected_revision=live[8]["revision"],
        )
        speaker_id = speaker.id
        session.commit()
        # One 30 s final segment spans the 2 s correction at 14-16 s.
        words = [
            {"word": f" w{second}", "start": second, "end": second + 1}
            for second in range(30)
        ]
        finalize_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[
                {
                    "start": 0,
                    "end": 30,
                    "text": " ".join(f"w{second}" for second in range(30)),
                    "speaker": "SPEAKER_00",
                    "words": words,
                }
            ],
        )
        lines = _lines(session, recording_id)
        assert [line["text"] for line in lines] == [
            " ".join(f"w{second}" for second in range(14)),
            "Corrected words",
            " ".join(f"w{second}" for second in range(16, 30)),
        ]
        assert [(line["start"], line["end"]) for line in lines] == [
            (0, 14),
            (14, 16),
            (16, 30),
        ]
        assert lines[1]["final_review"] == {
            "text": "w14 w15",
            "reason": "ambiguous_alignment",
        }
        assert not any("final_review" in line for line in (lines[0], lines[2]))
        # The speaker chosen beside the text correction reaches the final line.
        assert lines[2]["recording_speaker_id"] == speaker_id
        assert lines[2]["speaker_manually_edited"]
        assert lines[0]["recording_speaker_id"] != speaker_id
        stored = _load_transcript(session, recording_id)
        assert stored is not None and stored.text is not None
        assert "w0" in stored.text and "w29" in stored.text


def test_a_word_straddling_a_protected_line_spreads_its_speaker_to_neither_side(
    publication_engine: Engine,
) -> None:
    with Session(publication_engine) as session:
        recording_id = _recording(session)
        transcript = _transcript(session, recording_id)
        transcript.settling_state = {"settled": True}
        session.add(transcript)
        append_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[
                {"start": start, "end": end, "text": f"live {start}", "speaker": "L0"}
                for start, end in ((0, 10), (10, 12), (12, 20))
            ],
            run_kind=ProcessingRunKind.LIVE,
            source="live",
            state_override=TranscriptUtteranceState.STABLE,
        )
        session.commit()
        protected_id = serialize_canonical_utterances(session, recording_id)[1]["id"]
        update_utterance_text(
            session,
            recording_id=recording_id,
            utterance_public_id=protected_id,
            text="Corrected words",
        )
        _, speaker = update_utterance_speaker(
            session,
            recording_id=recording_id,
            utterance_public_id=protected_id,
            new_speaker_name="Chosen person",
        )
        speaker_id = speaker.id
        session.commit()
        # The words either side of the corrected 10-12 s line overlap it by
        # 0.4 s, but their midpoints lie outside it.
        words = [
            *({"word": f" w{i}", "start": i, "end": i + 1} for i in range(9)),
            {"word": " before", "start": 9.0, "end": 10.4},
            {"word": " inside", "start": 10.4, "end": 11.6},
            {"word": " after", "start": 11.6, "end": 13.0},
            *({"word": f" w{i}", "start": i, "end": i + 1} for i in range(13, 20)),
        ]
        finalize_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[
                {
                    "start": 0,
                    "end": 20,
                    "text": "".join(word["word"] for word in words).strip(),
                    "speaker": "SPEAKER_00",
                    "words": words,
                }
            ],
        )
        lines = _lines(session, recording_id)
        assert len(lines) == 3
        assert lines[1]["recording_speaker_id"] == speaker_id
        # Neither neighbour takes the corrected line's speaker or its lock.
        for line in (lines[0], lines[2]):
            assert line["recording_speaker_id"] != speaker_id
            assert not line["speaker_manually_edited"]
        assert lines[1]["final_review"]["text"] == "inside"
        assert [line["text"] for line in lines] == [
            " ".join([*(f"w{i}" for i in range(9)), "before"]),
            "Corrected words",
            " ".join(["after", *(f"w{i}" for i in range(13, 20))]),
        ]
        assert [(line["start"], line["end"]) for line in lines] == [
            (0, 10),
            (10, 12),
            (12, 20),
        ]


def test_one_untimed_word_keeps_the_speech_beside_a_correction_in_the_transcript(
    publication_engine: Engine,
) -> None:
    with Session(publication_engine) as session:
        recording_id = _recording(session)
        transcript = _transcript(session, recording_id)
        transcript.settling_state = {"settled": True}
        session.add(transcript)
        append_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[
                {"start": i * 2, "end": i * 2 + 2, "text": f"live {i}", "speaker": "L0"}
                for i in range(15)
            ],
            run_kind=ProcessingRunKind.LIVE,
            source="live",
            state_override=TranscriptUtteranceState.STABLE,
        )
        session.commit()
        update_utterance_text(
            session,
            recording_id=recording_id,
            utterance_public_id=serialize_canonical_utterances(session, recording_id)[
                7
            ]["id"],
            text="Corrected words",
        )
        session.commit()
        # Partial Canary alignment: one word of the 30 s segment over the
        # corrected 14-16 s line has no timing.
        words = [
            {
                "word": f" w{second}",
                "start": None if second == 5 else second,
                "end": None if second == 5 else second + 1,
                "source_id": f"source-{second}",
            }
            for second in range(30)
        ]
        finalize_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[
                {
                    "start": 0,
                    "end": 30,
                    "text": " ".join(f"w{second}" for second in range(30)),
                    "speaker": "SPEAKER_00",
                    "words": words,
                }
            ],
        )
        stored = _load_transcript(session, recording_id)
        assert stored is not None and stored.text is not None
        assert "w0" in stored.text and "w5" in stored.text and "w29" in stored.text
        lines = _lines(session, recording_id)
        # The untimed word stays with the timed word before it.
        assert [line["text"] for line in lines] == [
            " ".join(f"w{second}" for second in range(14)),
            "Corrected words",
            " ".join(f"w{second}" for second in range(16, 30)),
        ]
        assert [(line["start"], line["end"]) for line in lines] == [
            (0, 14),
            (14, 16),
            (16, 30),
        ]
        assert lines[1]["final_review"]["text"] == "w14 w15"


@pytest.mark.parametrize("forced", [False, True], ids=["native", "forced"])
def test_a_named_correction_holding_no_final_word_splits_its_neighbours(
    publication_engine: Engine, forced: bool
) -> None:
    with Session(publication_engine) as session:
        recording_id = _recording(session)
        transcript = _transcript(session, recording_id)
        transcript.settling_state = {"settled": True}
        session.add(transcript)
        append_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[
                {"start": start, "end": end, "text": f"live {start}", "speaker": "L0"}
                for start, end in ((0, 10), (10, 12), (12, 20))
            ],
            run_kind=ProcessingRunKind.LIVE,
            source="live",
            state_override=TranscriptUtteranceState.STABLE,
        )
        session.commit()
        protected_id = serialize_canonical_utterances(session, recording_id)[1]["id"]
        update_utterance_text(
            session,
            recording_id=recording_id,
            utterance_public_id=protected_id,
            text="Corrected words",
        )
        _, speaker = update_utterance_speaker(
            session,
            recording_id=recording_id,
            utterance_public_id=protected_id,
            new_speaker_name="Chosen person",
        )
        speaker_id = speaker.id
        session.commit()
        # Final ASR has no word whose midpoint lies in the corrected 10-12 s
        # line: the boundary words end at 10.6 s and start at 11.4 s.
        words = [
            *({"word": f" w{i}", "start": i, "end": i + 1} for i in range(9)),
            {"word": " before", "start": 9.0, "end": 10.6},
            {"word": " after", "start": 11.4, "end": 13.0},
            *({"word": f" w{i}", "start": i, "end": i + 1} for i in range(13, 20)),
        ]
        if forced:
            for order, word in enumerate(words):
                word["source_id"] = f"source-{order}"
        finalize_utterances_from_segments(
            session,
            recording_id=recording_id,
            segments=[
                {
                    "start": 0,
                    "end": 20,
                    "text": "".join(word["word"] for word in words).strip(),
                    "speaker": "SPEAKER_00",
                    "words": words,
                }
            ],
        )
        lines = _lines(session, recording_id)
        assert len(lines) == 3
        assert lines[1]["recording_speaker_id"] == speaker_id
        for line in (lines[0], lines[2]):
            assert line["recording_speaker_id"] != speaker_id
            assert not line["speaker_manually_edited"]
        assert [line["text"] for line in lines] == [
            " ".join([*(f"w{i}" for i in range(9)), "before"]),
            "Corrected words",
            " ".join(["after", *(f"w{i}" for i in range(13, 20))]),
        ]
        assert [(line["start"], line["end"]) for line in lines] == [
            (0, 10),
            (10, 12),
            (12, 20),
        ]
        assert "final_review" not in lines[1]
