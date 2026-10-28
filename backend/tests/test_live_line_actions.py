"""Line actions on settling recordings: edits freeze lines, words stay protected."""

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from backend.models.live_settling_public import public_settling_fields
from backend.models.pipeline import (
    ProcessingRunKind,
    TranscriptUtterance,
    TranscriptUtteranceState,
)
from backend.models.transcript import Transcript
from backend.tests.test_live_settling_publication import _active, _request, _seed
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
from backend.utils.canonical_pipeline.guards import settling_required
from backend.utils.canonical_pipeline.live_settling import publish_live_settling
from backend.utils.canonical_pipeline.live_settling_projection import (
    mutable_row,
    row_words,
)


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


@pytest.mark.parametrize("action", ["text", "speaker", "split"])
def test_an_action_on_a_settling_line_freezes_it_against_later_decodes(
    settling_engine: Engine, action: str
) -> None:
    recording_id, ids = _lines(settling_engine)
    with Session(settling_engine) as session:
        row = session.exec(
            select(TranscriptUtterance).where(TranscriptUtterance.public_id == ids[1])
        ).one()
        assert mutable_row(row)
        if action == "text":
            frozen = [
                update_utterance_text(
                    session,
                    recording_id=recording_id,
                    utterance_public_id=ids[1],
                    text="Human wording.",
                )
            ]
        elif action == "speaker":
            frozen = [
                update_utterance_speaker(
                    session,
                    recording_id=recording_id,
                    utterance_public_id=ids[1],
                    new_speaker_name="Alex",
                )[0]
            ]
        else:
            frozen = split_utterance(
                session,
                SplitCommand(
                    recording_id, ids[1], row_words(row)[1]["id"], row.revision
                ),
            )
        session.commit()
        before = [(line.public_id, line.text, line.speaker_label) for line in frozen]
        assert not any(mutable_row(line) for line in frozen)
        # It reads as locked too, so the page stops showing its words as settling.
        for line in frozen:
            public = public_settling_fields(line.confidence_payload)
            assert public["locked"] is True
            assert all(word["locked"] for word in public["words"])
        request = _request(session, recording_id, 0, end_ms=4000)
        words = [{"word": "Machine", "start_ms": 2100, "end_ms": 2400}]
        publish_live_settling(session, request=request, words=words)
        session.commit()
        for line in frozen:
            session.refresh(line)
        assert [
            (line.public_id, line.text, line.speaker_label) for line in frozen
        ] == before


@pytest.mark.parametrize("action", ["text", "speaker"])
def test_freezing_a_later_open_line_keeps_the_earlier_one_settling(
    settling_engine: Engine, action: str
) -> None:
    recording_id = _seed(settling_engine)
    words = [
        {"word": "Alpha", "start_ms": 100, "end_ms": 400},
        {"word": " beta.", "start_ms": 500, "end_ms": 900},
        {"word": " Gamma", "start_ms": 3000, "end_ms": 3400},
        {"word": " delta.", "start_ms": 3500, "end_ms": 3900},
    ]
    with Session(settling_engine) as session:
        publish_live_settling(
            session,
            request=_request(session, recording_id, 0, end_ms=4000),
            words=words,
        )
        session.commit()
        first, second = _active(session, recording_id)
        assert (first.text, second.text) == ("Alpha beta.", "Gamma delta.")
        if action == "text":
            update_utterance_text(
                session,
                recording_id=recording_id,
                utterance_public_id=second.public_id,
                text="Gamma delta, corrected.",
            )
        else:
            update_utterance_speaker(
                session,
                recording_id=recording_id,
                utterance_public_id=second.public_id,
                new_speaker_name="Alex",
            )
        session.commit()
        publish_live_settling(
            session,
            request=_request(session, recording_id, 1, end_ms=4200),
            words=words,
        )
        session.commit()
        lines = _active(session, recording_id)
        assert first.public_id in [line.public_id for line in lines]
        assert lines[0].text == "Alpha beta."
        assert mutable_row(lines[0])


@pytest.mark.parametrize("action", ["text", "speaker"])
def test_an_edit_to_a_line_a_settling_pass_superseded_is_refused(
    settling_engine: Engine, action: str
) -> None:
    recording_id, ids = _lines(settling_engine)
    with Session(settling_engine) as session:
        row = session.exec(
            select(TranscriptUtterance).where(TranscriptUtterance.public_id == ids[1])
        ).one()
        row.state = TranscriptUtteranceState.SUPERSEDED
        session.commit()
        with pytest.raises(RuntimeError, match="This line changed"):
            if action == "text":
                update_utterance_text(
                    session,
                    recording_id=recording_id,
                    utterance_public_id=ids[1],
                    text="Lost wording.",
                    expected_revision=row.revision,
                )
            else:
                update_utterance_speaker(
                    session,
                    recording_id=recording_id,
                    utterance_public_id=ids[1],
                    new_speaker_name="Alex",
                    expected_revision=row.revision,
                )


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
        with pytest.raises(RuntimeError, match="changed"):
            split_utterance(session, command)
        with pytest.raises(RuntimeError, match="revision conflict"):
            update_utterance_text(
                session,
                recording_id=recording_id,
                utterance_public_id=ids[0],
                text="Invisible edit",
                expected_revision=command.expected_revision,
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
        with pytest.raises(RuntimeError):
            split_utterance(
                session,
                SplitCommand(recording_id, row.public_id, words[0]["id"], row.revision),
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
