"""The real generated-state reset preserves only durable settling provenance."""

from __future__ import annotations

import asyncio
import threading

import pytest
from sqlmodel import Session

from backend.tests.test_canonical_publication_fence import Database
from backend.tests.test_canonical_publication_fence import (
    publication_database as publication_database,
)
from backend.tests.test_live_settling_finalisation import _final_segment
from backend.tests.test_live_settling_provenance import _evidence, _read
from backend.tests.test_live_settling_provenance import live_labels as live_labels
from backend.tests.test_reprocess_publication_postgres import _reset
from backend.utils.canonical_pipeline import (
    finalize_utterances_from_segments,
    serialize_canonical_utterances,
)
from backend.utils.canonical_pipeline.constants import _load_transcript
from backend.utils.canonical_pipeline.queries import list_active_utterances


@pytest.mark.parametrize("engine", ["whisper", "parakeet", "canary"])
@pytest.mark.parametrize("marker", [True, False, 1, None])
def test_actual_reprocess_reset_retains_only_literal_settled_provenance(
    publication_database: Database,
    live_labels: str,
    engine: str,
    marker: bool | int | None,
) -> None:
    database, recording_id, _ = publication_database
    with Session(database) as session:
        transcript = _load_transcript(session, recording_id)
        assert transcript is not None
        transcript.settling_state = {
            "eligible": True,
            "settled": marker,
            "generation": "discarded-capture",
            "sealed": True,
            "policy": {"k": 3, "window_s": 10, "enabled": True},
        }
        session.add(transcript)
        session.commit()
        identity = transcript.id
        sequence = transcript.change_sequence
    asyncio.run(_reset(database, recording_id, threading.Event()))
    with Session(database) as session:
        transcript = _load_transcript(session, recording_id)
        assert transcript is not None
        assert transcript.id == identity and transcript.change_sequence > sequence
        assert transcript.settling_state == (
            {"settled": True} if marker is True else None
        )
        assert transcript.segments == []
        assert serialize_canonical_utterances(session, recording_id) == []
        for run, first in enumerate(("Goodbye", "Farewell", "Farewell")):
            segment = _final_segment(engine, first)
            if run == 2:
                segment.pop("words")
            finalize_utterances_from_segments(
                session,
                recording_id=recording_id,
                segments=[segment],
                idempotency_key=f"after-reset-{run}",
            )
            line = _read(session, recording_id)
            assert line["text"] == first + " world"
            payload = line.get("confidence_payload")
            if marker is True:
                assert payload is not None
                if run < 2:
                    assert payload == _evidence(engine, first)
                else:
                    assert payload["asr_segments"] == [
                        {"start_ms": 100, "end_ms": 850, "text": "Farewell world"}
                    ]
                    assert payload["asr_word_timestamps_available"] is False
            elif engine == "canary":
                assert payload == _evidence(engine, "Farewell" if run == 2 else first)
            else:
                assert payload == {}
                assert (
                    list_active_utterances(session, recording_id)[0].confidence_payload
                    is None
                )
