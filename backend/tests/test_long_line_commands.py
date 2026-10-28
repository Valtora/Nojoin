"""Real finalisation and commands retain the complete 30 s line and its tail."""

from uuid import UUID

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from backend.tests.canonical_engine import (
    publication_engine as publication_engine,
)
from backend.tests.test_live_settling_finalisation import _closed_line
from backend.tests.test_long_transcript_lines import _line
from backend.utils.canonical_pipeline import (
    finalize_utterances_from_segments,
    serialize_canonical_utterances,
    update_utterance_speaker,
    update_utterance_text,
)
from backend.utils.canonical_pipeline.split import SplitCommand, split_utterance


@pytest.mark.parametrize("action", ["split", "edit", "speaker"])
def test_final_thirty_second_line_has_usable_word_boundaries_and_revisioned_actions(
    publication_engine: Engine,
    action: str,
) -> None:
    with Session(publication_engine) as session:
        recording_id, _ = _closed_line(session)
        source = _line(30)
        finalize_utterances_from_segments(
            session, recording_id=recording_id, segments=[source]
        )
        line = serialize_canonical_utterances(session, recording_id)[0]
        assert line["start"] == 0 and line["end"] == 30
        assert len(line["words"]) == 30
        assert all(str(UUID(word["id"])) == word["id"] for word in line["words"])
        assert "locked" not in line and "text_locked" not in line
        if action == "split":
            split_utterance(
                session,
                SplitCommand(
                    recording_id, line["id"], line["words"][25]["id"], line["revision"]
                ),
            )
            children = serialize_canonical_utterances(session, recording_id)
            assert [(child["start"], child["end"]) for child in children] == [
                (0, 25),
                (25, 30),
            ]
            assert " ".join(child["text"] for child in children) == source["text"]
            assert children[1]["words"][0]["id"] == line["words"][25]["id"]
            assert all(child["locked"] for child in children)
        elif action == "edit":
            revised = source["text"].replace("word29", "Tail correction")
            update_utterance_text(
                session,
                recording_id=recording_id,
                utterance_public_id=line["id"],
                text=revised,
                expected_revision=line["revision"],
            )
            edited = serialize_canonical_utterances(session, recording_id)[0]
            assert edited["text"] == revised and edited["end"] == 30
            assert all(word["start_ms"] is None for word in edited["words"])
        else:
            update_utterance_speaker(
                session,
                recording_id=recording_id,
                utterance_public_id=line["id"],
                new_speaker_name="Blair",
                expected_revision=line["revision"],
            )
            assigned = serialize_canonical_utterances(session, recording_id)[0]
            assert assigned["text"] == source["text"] and assigned["end"] == 30
            assert assigned["speaker"] != "A"
            assert assigned["words"] == line["words"]
